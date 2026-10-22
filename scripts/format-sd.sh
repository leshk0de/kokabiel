#!/bin/sh
# kokabiel-format-sd: erase an SD card and prepare it for captures.
# Usage: kokabiel-format-sd --device DEV [--confirm ERASE] [--probe NAME]
# Exit codes: 0 ok, 3 usage/failure, 6 not a safe SD device, 7 not confirmed.
#
# Test hooks: KOKABIEL_SD_SETTLE seconds to wait for the new partition node (5),
# KOKABIEL_SD_NODE_TEST test(1) operator used for that node (-b).

KK_LIB="${KOKABIEL_LIB:-$(dirname "$0")/lib}"
[ -d "$KK_LIB" ] || KK_LIB=/usr/libexec/kokabiel
# shellcheck source=lib/common.sh disable=SC1091
. "$KK_LIB/common.sh"
# shellcheck source=lib/sd.sh disable=SC1091
. "$KK_LIB/sd.sh"

DEV=
CONFIRM=
PROBE=
while [ $# -gt 0 ]; do
	case $1 in
	--device | --confirm | --probe)
		[ $# -ge 2 ] || kk_die "$1 needs a value" 3
		case $1 in
		--device) DEV=$2 ;;
		--confirm) CONFIRM=$2 ;;
		--probe) PROBE=$2 ;;
		esac
		shift 2
		;;
	*) kk_die "usage: kokabiel-format-sd --device DEV [--confirm ERASE] [--probe NAME]" 3 ;;
	esac
done
[ -n "$DEV" ] || kk_die "usage: kokabiel-format-sd --device DEV [--confirm ERASE] [--probe NAME]" 3

kk_sd_safe "${DEV#/dev/}" || kk_die "$DEV is not a safe SD card (must be an SD mmcblk with nothing mounted at /, /rom or /overlay)" 6
# Only the discovered device is acceptable, never a path that merely resolves like one.
case $DEV in /dev/mmcblk*) ;; *) kk_die "$DEV is not under /dev" 6 ;; esac

[ -n "$PROBE" ] || PROBE=$(kk_uci_get probe_name 2>/dev/null) || PROBE=
[ -n "$PROBE" ] || PROBE=$(hostname 2>/dev/null) || PROBE=probe

if [ "$CONFIRM" != ERASE ]; then
	_dn=${DEV#/dev/}
	_sect=$(cat "$(kk_sysfs "block/$_dn/size")" 2>/dev/null) || _sect=0
	_ser=$(cat "$(kk_sysfs "block/$_dn/device/serial")" 2>/dev/null) || _ser=
	kk_sd_describe
	printf 'Erase SD card %sMB %s on %s and use it for captures? Type ERASE to confirm: ' \
		"$((_sect / 2048))" "$_ser" "$PROBE"
	if ( : </dev/tty ) 2>/dev/null; then
		IFS= read -r _ans </dev/tty || _ans=
	else
		IFS= read -r _ans || _ans=
	fi
	echo
	[ "$_ans" = ERASE ] || kk_die "not confirmed; $DEV untouched" 7
fi

# Re-check right before the destructive part: the world may have changed while
# the prompt was waiting for an answer (or --confirm was scripted).
kk_sd_safe "${DEV#/dev/}" || kk_die "$DEV is no longer a safe SD card; aborting, nothing erased" 6

# Release any partition of the card that is mounted.
for _p in $(_kk_sd_parts "${DEV#/dev/}"); do
	if awk -v p="/dev/$_p" '$1 == p { f = 1 } END { exit !f }' "${KOKABIEL_PROC:-/proc}/mounts" 2>/dev/null; then
		umount "/dev/$_p" || kk_die "cannot unmount /dev/$_p"
	fi
done

PART=${DEV}p1
dd if=/dev/zero of="$DEV" bs=1M count=16 || kk_die "dd failed on $DEV"
echo 'type=83' | sfdisk "$DEV" || kk_die "sfdisk failed on $DEV"
# Test hook: KOKABIEL_SD_NODE_TEST (default -b) is the test(1) operator for the node.
KOKABIEL_SD_NODE_TEST=${KOKABIEL_SD_NODE_TEST:--b}
_i=0
while ! test "$KOKABIEL_SD_NODE_TEST" "$PART" && [ "$_i" -lt "${KOKABIEL_SD_SETTLE:-5}" ]; do
	sleep 1
	_i=$((_i + 1))
done
test "$KOKABIEL_SD_NODE_TEST" "$PART" || kk_die "$PART did not appear after partitioning" 3
mkfs.ext4 -F -L "$KK_SD_LABEL" "$PART" || kk_die "mkfs.ext4 failed on $PART"

_t=$(mktemp -d /tmp/kokabiel-fmt.XXXXXX) || kk_die "cannot create temp dir"
mount "$PART" "$_t" || {
	rmdir "$_t" 2>/dev/null
	kk_die "cannot mount $PART"
}
{
	echo "probe=$PROBE"
	echo "formatted=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$_t/$KK_SD_MARKER" || {
	umount "$_t" 2>/dev/null
	kk_die "cannot write marker"
}
umount "$_t" || kk_die "cannot unmount $_t"
rmdir "$_t" 2>/dev/null || true
kk_log "formatted $DEV as $KK_SD_LABEL"
