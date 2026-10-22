# shellcheck shell=sh
# Kokabiel SD card library. Source after common.sh; do not execute.
#
# Test hooks: KOKABIEL_SD_TMP  directory used for the read-only marker mount
#             (default: a fresh mktemp -d).

KK_SD_LABEL=KOKABIEL-CAP
KK_SD_MARKER=.kokabiel

# kk_sd_capdir: where the card is mounted.
kk_sd_capdir() {
	if [ -n "${CAPTURE_DIR:-}" ]; then
		echo "$CAPTURE_DIR"
	else
		_kk_cd=$(kk_uci_get capture_dir 2>/dev/null) || _kk_cd=
		echo "${_kk_cd:-/mnt/kokabiel-cap}"
	fi
}

# _kk_sd_devnums NAME: " maj:min maj:min ... " for the disk and its partitions
# (from sysfs). rc 1 if any is unreadable (callers fail closed).
_kk_sd_devnums() {
	_kk_dv=$(cat "$(kk_sysfs "block/$1/dev")" 2>/dev/null) || return 1
	[ -n "$_kk_dv" ] || return 1
	_kk_all=" $_kk_dv "
	for _kk_pp in $(_kk_sd_parts "$1"); do
		_kk_dv=$(cat "$(kk_sysfs "block/$1/$_kk_pp/dev")" 2>/dev/null) || return 1
		[ -n "$_kk_dv" ] || return 1
		_kk_all="$_kk_all$_kk_dv "
	done
	echo "$_kk_all"
}

# _kk_sd_root_on_cmdline NAME: true if the kernel cmdline root= points at the disk.
_kk_sd_root_on_cmdline() {
	_kk_root=$(tr ' ' '\n' <"${KOKABIEL_PROC:-/proc}/cmdline" 2>/dev/null | sed -n 's/^root=//p' | head -n 1)
	[ -n "$_kk_root" ] || return 1
	case $_kk_root in
	"/dev/$1" | "/dev/$1"p*) return 0 ;;
	PARTUUID=* | UUID=*)
		_kk_key=${_kk_root%%=*}
		# shellcheck disable=SC2018,SC2019 # BusyBox tr lacks reliable [:class:] support
		_kk_want=$(echo "${_kk_root#*=}" | tr 'A-Z' 'a-z')
		for _kk_pp in $(_kk_sd_parts "$1"); do
			# shellcheck disable=SC2018,SC2019
			_kk_have=$(_kk_sd_blkid "/dev/$_kk_pp" "$_kk_key" | tr 'A-Z' 'a-z')
			[ -n "$_kk_have" ] && [ "$_kk_have" = "$_kk_want" ] && return 0
		done
		;;
	esac
	return 1
}

# kk_sd_safe NAME: NAME (mmcblkN) is an SD card that does not back the running
# system. Decided by device numbers, not names, and fails closed: unsafe if
# mountinfo is unreadable, if any partition is mounted anywhere except the
# capture dir, if root= on the kernel cmdline names the disk, or if a loop
# device's backing file lives on one of its partitions.
kk_sd_safe() {
	case $1 in
	mmcblk[0-9] | mmcblk[0-9][0-9]) ;;
	*) return 1 ;;
	esac
	[ -d "$(kk_sysfs "block/$1")" ] || return 1
	[ "$(cat "$(kk_sysfs "block/$1/device/type")" 2>/dev/null)" = SD ] || return 1
	_kk_mi=${KOKABIEL_PROC:-/proc}/self/mountinfo
	[ -r "$_kk_mi" ] || return 1
	_kk_devs=$(_kk_sd_devnums "$1") || return 1
	_kk_cd=$(kk_sd_capdir)
	# mountinfo: id parent maj:min root mountpoint opts [optional...] - fstype source superopts
	# The capture-dir exemption never covers / /rom /overlay. A mountinfo without a
	# "/" entry is not trusted (unsafe).
	awk -v devs="$_kk_devs" -v cd="$_kk_cd" -v n="$1" '
		{
			for (i = 6; i <= NF && $i != "-"; i++) ;
			src = $(i + 2)
			sys = ($5 == "/" || $5 == "/rom" || $5 == "/overlay")
			if ($5 == "/") hasroot = 1
			if (($5 != cd || sys) && (index(devs, " " $3 " ") || src ~ ("^/dev/" n "(p[0-9]+)?$"))) bad = 1
		}
		END { exit (bad || !hasroot) ? 0 : 1 }' "$_kk_mi" && return 1
	_kk_sd_root_on_cmdline "$1" && return 1
	for _kk_lf in "$(kk_sysfs block)"/loop*/loop/backing_file; do
		[ -r "$_kk_lf" ] || continue
		_kk_bf=$(cat "$_kk_lf")
		_kk_bd=$(awk -v bf="$_kk_bf" '
			{ mp = $5; if (mp == "/" || bf == mp || index(bf, mp "/") == 1) if (length(mp) > best) { best = length(mp); d = $3 } }
			END { print d }' "$_kk_mi")
		case $_kk_devs in *" $_kk_bd "*) [ -n "$_kk_bd" ] && return 1 ;; esac
	done
	return 0
}

# kk_sd_find: print /dev/mmcblkN of the first safe SD card; rc 1 if none.
kk_sd_find() {
	for _kk_d in "$(kk_sysfs block)"/mmcblk*; do
		[ -d "$_kk_d" ] || continue
		_kk_n=${_kk_d##*/}
		kk_sd_safe "$_kk_n" || continue
		echo "/dev/$_kk_n"
		return 0
	done
	return 1
}

# _kk_sd_blkid DEV KEY: value of KEY (LABEL, UUID, TYPE, PARTUUID) from blkid,
# with blkid's backslash escapes undone; empty if absent.
_kk_sd_blkid() {
	blkid "$1" 2>/dev/null | sed -nE 's/(^|.* )'"$2"'="(([^"\\]|\\.)*)".*/\2/p' |
		head -n 1 | sed 's/\\\(.\)/\1/g'
}

# _kk_json_esc: stdin to JSON-string-safe stdout (quotes, backslashes, controls).
_kk_json_esc() {
	tr -d '\000-\037' | sed 's/\\/\\\\/g;s/"/\\"/g'
}

# _kk_sd_parts NAME: print partition names (e.g. mmcblk0p1) of disk NAME.
_kk_sd_parts() {
	for _kk_pd in "$(kk_sysfs "block/$1")/$1"p*; do
		[ -d "$_kk_pd" ] && echo "${_kk_pd##*/}"
	done
	return 0
}

# kk_sd_kokabiel_part DEV: print "<partition> <uuid>" if DEV holds a partition
# labelled KOKABIEL-CAP whose filesystem root carries the marker (verified by a
# read-only temporary mount). rc 1 otherwise. The label alone is never enough.
kk_sd_kokabiel_part() {
	_kk_dn=${1#/dev/}
	for _kk_p in $(_kk_sd_parts "$_kk_dn"); do
		[ "$(_kk_sd_blkid "/dev/$_kk_p" LABEL)" = "$KK_SD_LABEL" ] || continue
		_kk_ok=1
		_kk_at=$(awk -v p="/dev/$_kk_p" '$1 == p { print $2; exit }' "${KOKABIEL_PROC:-/proc}/mounts" 2>/dev/null)
		if [ -n "$_kk_at" ]; then
			# already mounted: look at the live mount, do not mount twice
			[ -f "$_kk_at/$KK_SD_MARKER" ] && _kk_ok=0
		else
			if [ -n "${KOKABIEL_SD_TMP:-}" ]; then
				_kk_t=$KOKABIEL_SD_TMP
				mkdir -p "$_kk_t"
			else
				_kk_t=$(mktemp -d /tmp/kokabiel-sd.XXXXXX) || return 1
			fi
			if mount -o ro "/dev/$_kk_p" "$_kk_t" 2>/dev/null; then
				[ -f "$_kk_t/$KK_SD_MARKER" ] && _kk_ok=0
				umount "$_kk_t" 2>/dev/null || true
			fi
			[ -n "${KOKABIEL_SD_TMP:-}" ] || rmdir "$_kk_t" 2>/dev/null || true
		fi
		if [ "$_kk_ok" = 0 ]; then
			echo "$_kk_p $(_kk_sd_blkid "/dev/$_kk_p" UUID)"
			return 0
		fi
	done
	return 1
}

# kk_sd_is_kokabiel DEV: true if DEV is a Kokabiel capture card.
kk_sd_is_kokabiel() {
	_kk_x=$(kk_sd_kokabiel_part "$1") && [ -n "$_kk_x" ]
}

# kk_sd_describe: one line of JSON describing the card (see Task 5 contract).
kk_sd_describe() {
	_kk_dev=$(kk_sd_find) || {
		echo '{"present":false,"device":null,"size_mb":0,"serial":"","name":"","kokabiel":false,"partitions":[]}'
		return 0
	}
	_kk_dn=${_kk_dev#/dev/}
	_kk_sect=$(cat "$(kk_sysfs "block/$_kk_dn/size")" 2>/dev/null) || _kk_sect=0
	case $_kk_sect in '' | *[!0-9]*) _kk_sect=0 ;; esac
	_kk_ser=$(cat "$(kk_sysfs "block/$_kk_dn/device/serial")" 2>/dev/null | _kk_json_esc)
	_kk_nm=$(cat "$(kk_sysfs "block/$_kk_dn/device/name")" 2>/dev/null | _kk_json_esc)
	if kk_sd_is_kokabiel "$_kk_dev"; then _kk_kk=true; else _kk_kk=false; fi
	_kk_pj=
	for _kk_p in $(_kk_sd_parts "$_kk_dn"); do
		_kk_l=$(_kk_sd_blkid "/dev/$_kk_p" LABEL | _kk_json_esc)
		_kk_f=$(_kk_sd_blkid "/dev/$_kk_p" TYPE | _kk_json_esc)
		_kk_u=$(_kk_sd_blkid "/dev/$_kk_p" UUID | _kk_json_esc)
		_kk_pj="$_kk_pj${_kk_pj:+,}{\"name\":\"$_kk_p\",\"label\":\"$_kk_l\",\"fstype\":\"$_kk_f\",\"uuid\":\"$_kk_u\"}"
	done
	printf '{"present":true,"device":"%s","size_mb":%s,"serial":"%s","name":"%s","kokabiel":%s,"partitions":[%s]}\n' \
		"$_kk_dev" "$((_kk_sect / 2048))" "$_kk_ser" "$_kk_nm" "$_kk_kk" "$_kk_pj"
}

# kk_sd_is_mounted: true only if the capture dir is a mount point.
kk_sd_is_mounted() {
	_kk_cd=$(kk_sd_capdir)
	awk -v d="$_kk_cd" '$2 == d { found = 1 } END { exit !found }' \
		"${KOKABIEL_PROC:-/proc}/mounts" 2>/dev/null
}

# kk_sd_mount: ensure the fstab entry exists (card present and Kokabiel's) and
# mount it, else remove the entry. Always returns 0.
kk_sd_mount() {
	_kk_cd=$(kk_sd_capdir)
	_kk_dev=$(kk_sd_find) || _kk_dev=
	_kk_info=
	[ -z "$_kk_dev" ] || _kk_info=$(kk_sd_kokabiel_part "$_kk_dev") || _kk_info=
	_kk_uuid=${_kk_info#* }
	if [ -n "$_kk_info" ] && [ -n "$_kk_uuid" ]; then
		[ -e /etc/config/fstab ] || : >/etc/config/fstab
		uci set fstab.kokabiel_cap=mount
		uci set fstab.kokabiel_cap.uuid="$_kk_uuid"
		uci set fstab.kokabiel_cap.target="$_kk_cd"
		uci set fstab.kokabiel_cap.options=noatime
		uci set fstab.kokabiel_cap.enabled=1
		uci commit fstab
		mkdir -p "$_kk_cd" 2>/dev/null || true
		block mount || kk_log "block mount failed"
	else
		if uci -q get fstab.kokabiel_cap >/dev/null 2>&1; then
			uci -q delete fstab.kokabiel_cap || true
			uci -q commit fstab || true
		fi
	fi
	return 0
}
