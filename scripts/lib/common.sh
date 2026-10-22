# shellcheck shell=sh
# Kokabiel shared library. POSIX sh for BusyBox ash; source it, do not execute it.
#
# How on-probe scripts source this library (it never assumes its own location):
#
#   KK_LIB="${KOKABIEL_LIB:-$(dirname "$0")/lib}"
#   [ -d "$KK_LIB" ] || KK_LIB=/usr/libexec/kokabiel
#   . "$KK_LIB/common.sh"
#
# Test hooks (default to the real system paths):
#   KOKABIEL_ENV    site.env to load        KOKABIEL_SYSFS  /sys
#   KOKABIEL_INITD  /etc/init.d             KOKABIEL_PROC   /proc

# kk_die MSG [CODE]: log, print "kokabiel: MSG" to stderr, exit CODE (default 3).
kk_die() {
	logger -t kokabiel -p daemon.err "$1" 2>/dev/null || true
	echo "kokabiel: $1" >&2
	exit "${2:-3}"
}

# kk_log MSG: info-level log to syslog and stderr.
kk_log() {
	logger -t kokabiel -p daemon.info "$1" 2>/dev/null || true
	echo "kokabiel: $1" >&2
}

# _kk_uint NAME MIN MAX: NAME must hold an integer in [MIN, MAX].
_kk_uint() {
	_kk_name=$1
	eval "_kk_val=\${$_kk_name}"
	# shellcheck disable=SC2154 # assigned by the eval above
	case $_kk_val in
	'' | *[!0-9]*) kk_die "$_kk_name must be an integer (got '$_kk_val')" ;;
	esac
	if [ "$_kk_val" -lt "$2" ] || [ "$_kk_val" -gt "$3" ]; then
		kk_die "$_kk_name must be between $2 and $3 (got $_kk_val)"
	fi
}

_KK_VARS="COLLECTOR_HOST COLLECTOR_PORT EXPORT_VERSION CAPTURE_IF MGMT_IF PROBE_NAME CAPTURE_DIR CAPTURE_SD_MAX_MB CAPTURE_RAM_MAX_MB ENABLE_LUCI SYSLOG_HOST SYSLOG_PORT SYSLOG_PROTO METRICS_ALLOWED_SRC TRACK_LEVEL KOKABIEL_DISABLE_SERVICES"

# _kk_parse_env FILE: safe KEY=VALUE parser (no eval of file content). Accepts
# blank lines, # comments, CRLF, and one layer of matching quotes; a trailing
# " # comment" is allowed after the value. Unknown keys are ignored with a warning.
_kk_parse_env() {
	_kk_cr=$(printf '\r')
	_kk_n=0
	while IFS= read -r _kk_line || [ -n "$_kk_line" ]; do
		_kk_n=$((_kk_n + 1))
		_kk_line=${_kk_line%"$_kk_cr"}
		_kk_line=${_kk_line#"${_kk_line%%[![:space:]]*}"}
		case $_kk_line in '' | '#'*) continue ;; esac
		case $_kk_line in
		[A-Z_]*=*) ;;
		*) kk_die "$1 line $_kk_n: expected NAME=value" ;;
		esac
		_kk_key=${_kk_line%%=*}
		case $_kk_key in
		*[!A-Z0-9_]*) kk_die "$1 line $_kk_n: invalid variable name '$_kk_key'" ;;
		esac
		_kk_val=${_kk_line#*=}
		case $_kk_val in
		\"*)
			_kk_rest=${_kk_val#\"}
			case $_kk_rest in
			*\"*) ;;
			*) kk_die "$1 line $_kk_n: unterminated double quote" ;;
			esac
			_kk_val=${_kk_rest%%\"*}
			_kk_rest=${_kk_rest#*\"}
			;;
		\'*)
			_kk_rest=${_kk_val#\'}
			case $_kk_rest in
			*\'*) ;;
			*) kk_die "$1 line $_kk_n: unterminated single quote" ;;
			esac
			_kk_val=${_kk_rest%%\'*}
			_kk_rest=${_kk_rest#*\'}
			;;
		*)
			_kk_rest=
			case $_kk_val in
			*[[:space:]]'#'*)
				_kk_rest=${_kk_val#*[[:space:]]'#'}
				_kk_val=${_kk_val%%[[:space:]]'#'*}
				_kk_rest="#$_kk_rest"
				;;
			esac
			_kk_val=${_kk_val%"${_kk_val##*[![:space:]]}"}
			case $_kk_val in
			*[[:space:]]*) kk_die "$1 line $_kk_n: unquoted value contains whitespace" ;;
			esac
			;;
		esac
		_kk_rest=${_kk_rest#"${_kk_rest%%[![:space:]]*}"}
		case $_kk_rest in
		'' | '#'*) ;;
		*) kk_die "$1 line $_kk_n: unexpected text after value" ;;
		esac
		case " $_KK_VARS " in
		*" $_kk_key "*) eval "$_kk_key=\$_kk_val" ;;
		*) kk_log "$1 line $_kk_n: ignoring unknown variable $_kk_key" ;;
		esac
	done <"$1"
}

# kk_load_config [FILE]: source site.env, apply defaults, validate, export.
# FILE, else $KOKABIEL_ENV, else site.env next to the calling script, else
# /etc/kokabiel/site.env. Exits 3 on any problem.
kk_load_config() {
	_kk_file=${1:-${KOKABIEL_ENV:-}}
	if [ -z "$_kk_file" ]; then
		if [ -f "$(dirname "$0")/site.env" ]; then
			_kk_file=$(dirname "$0")/site.env
		else
			_kk_file=/etc/kokabiel/site.env
		fi
	fi
	[ -r "$_kk_file" ] || kk_die "cannot read config file $_kk_file"
	_kk_parse_env "$_kk_file"

	: "${COLLECTOR_PORT:=2055}"
	: "${EXPORT_VERSION:=10}"
	: "${CAPTURE_IF:=eth1}"
	: "${MGMT_IF:=eth0}"
	: "${PROBE_NAME:=}"
	: "${CAPTURE_DIR:=/mnt/kokabiel-cap}"
	: "${CAPTURE_SD_MAX_MB:=4096}"
	: "${CAPTURE_RAM_MAX_MB:=100}"
	: "${ENABLE_LUCI:=0}"
	: "${SYSLOG_HOST:=}"
	: "${SYSLOG_PORT:=514}"
	: "${SYSLOG_PROTO:=udp}"
	: "${METRICS_ALLOWED_SRC:=}"
	: "${TRACK_LEVEL:=full}"
	: "${KOKABIEL_DISABLE_SERVICES:=ttyd vsftpd samba4 wsdd2 minidlna aria2 miniupnpd adblock smartdns ddns sqm nlbwmon radius avahi-daemon collectd luci_statistics watchcat hd-idle fa-fancontrol odhcpd lldpd umdns}"

	case ${COLLECTOR_HOST:-} in
	'') kk_die "COLLECTOR_HOST is required (set it in $_kk_file)" ;;
	*'<'*'>'*) kk_die "COLLECTOR_HOST is still a placeholder ('$COLLECTOR_HOST'); set a real address in $_kk_file" ;;
	esac
	_kk_uint COLLECTOR_PORT 1 65535
	case $EXPORT_VERSION in
	9 | 10) ;;
	*) kk_die "EXPORT_VERSION must be 9 or 10 (got '$EXPORT_VERSION')" ;;
	esac
	case $CAPTURE_DIR in
	/ | /rom | /overlay | /rom/ | /overlay/ | //) kk_die "CAPTURE_DIR must not be $CAPTURE_DIR (system mount)" ;;
	/*) ;;
	*) kk_die "CAPTURE_DIR must be an absolute path (got '$CAPTURE_DIR')" ;;
	esac
	_kk_uint CAPTURE_RAM_MAX_MB 100 500
	_kk_uint CAPTURE_SD_MAX_MB 1 99999999
	case $ENABLE_LUCI in
	0 | 1) ;;
	*) kk_die "ENABLE_LUCI must be 0 or 1 (got '$ENABLE_LUCI')" ;;
	esac
	_kk_uint SYSLOG_PORT 1 65535
	case $SYSLOG_PROTO in
	udp | tcp) ;;
	*) kk_die "SYSLOG_PROTO must be udp or tcp (got '$SYSLOG_PROTO')" ;;
	esac
	case $TRACK_LEVEL in
	full | port | proto | ip | vlan | ether) ;;
	*) kk_die "TRACK_LEVEL must be one of full, port, proto, ip, vlan, ether (got '$TRACK_LEVEL')" ;;
	esac

	export COLLECTOR_HOST COLLECTOR_PORT EXPORT_VERSION CAPTURE_IF MGMT_IF PROBE_NAME \
		CAPTURE_DIR CAPTURE_SD_MAX_MB CAPTURE_RAM_MAX_MB ENABLE_LUCI SYSLOG_HOST \
		SYSLOG_PORT SYSLOG_PROTO METRICS_ALLOWED_SRC TRACK_LEVEL KOKABIEL_DISABLE_SERVICES
}

# kk_uci_get OPTION: value of kokabiel.main.OPTION (empty if unset).
kk_uci_get() {
	uci -q get "kokabiel.main.$1"
}

# kk_pkg_mgr: print apk or opkg; exit 3 if neither is available.
kk_pkg_mgr() {
	if command -v apk >/dev/null 2>&1; then
		echo apk
	elif command -v opkg >/dev/null 2>&1; then
		echo opkg
	else
		kk_die "no package manager (apk or opkg) found"
	fi
}

# kk_pkg_install PKG...: install the packages that are missing. Never kmod-*:
# FriendlyWrt's vendor kernel does not match the feeds' kmods.
kk_pkg_install() {
	for _kk_p in "$@"; do
		case $_kk_p in
		kmod-*) kk_die "refusing to install $_kk_p: kmod packages do not match the vendor kernel" ;;
		esac
	done
	_kk_mgr=$(kk_pkg_mgr) || exit $?
	_kk_missing=
	for _kk_p in "$@"; do
		if [ "$_kk_mgr" = apk ]; then
			apk info -e "$_kk_p" >/dev/null 2>&1 && continue
		else
			[ -n "$(opkg list-installed "$_kk_p" 2>/dev/null)" ] && continue
		fi
		_kk_missing="$_kk_missing $_kk_p"
	done
	[ -n "$_kk_missing" ] || return 0
	if [ "$_kk_mgr" = apk ]; then
		apk update >/dev/null 2>&1 || kk_log "apk update failed; trying install anyway"
		# shellcheck disable=SC2086 # intentional word splitting
		apk add $_kk_missing || kk_die "apk add failed:$_kk_missing"
	else
		opkg update >/dev/null 2>&1 || kk_log "opkg update failed; trying install anyway"
		# shellcheck disable=SC2086
		opkg install $_kk_missing || kk_die "opkg install failed:$_kk_missing"
	fi
}

# kk_svc NAME ACTION: run an init script; no-op (rc 0) if it does not exist.
kk_svc() {
	_kk_init=${KOKABIEL_INITD:-/etc/init.d}/$1
	[ -x "$_kk_init" ] || return 0
	"$_kk_init" "$2"
}

# kk_sysfs PATH: path under sysfs (overridable for tests).
kk_sysfs() {
	echo "${KOKABIEL_SYSFS:-/sys}/$1"
}

# kk_mac IFACE: lowercase MAC address of IFACE.
kk_mac() {
	# shellcheck disable=SC2018,SC2019 # BusyBox tr lacks reliable [:class:] support
	tr 'A-Z' 'a-z' <"$(kk_sysfs "class/net/$1/address")"
}
