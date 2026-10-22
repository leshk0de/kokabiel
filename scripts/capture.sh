#!/bin/sh
# kokabiel-capture: targeted packet capture for one host.
# Usage: kokabiel-capture TARGET [SECONDS] [--max-mb N]
#   TARGET   IPv4, IPv6 or MAC (xx:xx:xx:xx:xx:xx)
#   SECONDS  1-3600 (default 60)
# Writes to the SD card when CAPTURE_DIR is a mount point, else to RAM.
# Last stdout line is a JSON summary.
# Exit codes: 0 ok, 2 usage, 3 config, 4 space or memory, 5 tcpdump failed, 75 busy.
#
# Test hook: KOKABIEL_RUN overrides /var/run.

KK_LIB="${KOKABIEL_LIB:-$(dirname "$0")/lib}"
[ -d "$KK_LIB" ] || KK_LIB=/usr/libexec/kokabiel
# shellcheck source=lib/common.sh disable=SC1091
. "$KK_LIB/common.sh"
# shellcheck source=lib/sd.sh disable=SC1091
. "$KK_LIB/sd.sh"

USAGE="usage: kokabiel-capture TARGET [SECONDS] [--max-mb N]"
RUN=${KOKABIEL_RUN:-/var/run}
LOCK=$RUN/kokabiel-capture.lock
STATE=$RUN/kokabiel-capture.state
HAVE_LOCK=0
ERRF=
CHILD=

# shellcheck disable=SC2329 # invoked via trap
cleanup() {
	if [ -n "$CHILD" ]; then
		kill "$CHILD" 2>/dev/null
		wait "$CHILD" 2>/dev/null
	fi
	[ -z "$ERRF" ] || rm -f "$ERRF"
	if [ "$HAVE_LOCK" = 1 ]; then
		rm -f "$STATE"
		rm -rf "$LOCK"
	fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# ---- target validation (strict; nothing else runs before this) ----

# Digits only, no leading zeros (so ash arithmetic never reads octal).
is_uint() {
	case $1 in
	'' | *[!0-9]* | 0[0-9]*) return 1 ;;
	esac
}

is_ipv4() {
	case $1 in
	'' | *[!0-9.]*) return 1 ;;
	*.*.*.*.*) return 1 ;;
	*.*.*.*) ;;
	*) return 1 ;;
	esac
	_o_ifs=$IFS
	IFS=.
	# shellcheck disable=SC2086 # intentional split on dots; chars already restricted
	set -- $1
	IFS=$_o_ifs
	[ $# -eq 4 ] || return 1
	for _o in "$@"; do
		is_uint "$_o" || return 1
		[ ${#_o} -le 3 ] && [ "$_o" -le 255 ] || return 1
	done
}

is_mac() {
	case $1 in
	[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]:[0-9a-fA-F][0-9a-fA-F]) return 0 ;;
	esac
	return 1
}

is_ipv6() {
	case $1 in
	'' | *[!0-9a-fA-F:]* | *:::*) return 1 ;;
	*:*:*) ;;
	*) return 1 ;;
	esac
	_dbl=0
	case $1 in
	*::*::*) return 1 ;;
	*::*) _dbl=1 ;;
	esac
	# a single leading or trailing colon is only legal as part of "::"
	case $1 in
	:[!:]* | *[!:]:) return 1 ;;
	esac
	_o_ifs=$IFS
	IFS=:
	# shellcheck disable=SC2086 # intentional split on colons; chars already restricted
	set -- $1
	IFS=$_o_ifs
	_n=0
	for _g in "$@"; do
		[ -z "$_g" ] && continue
		[ ${#_g} -le 4 ] || return 1
		_n=$((_n + 1))
	done
	if [ "$_dbl" = 1 ]; then [ "$_n" -le 7 ]; else [ "$_n" -eq 8 ]; fi
}

TARGET=
SECONDS_=60
MAXMB=
KIND=
while [ $# -gt 0 ]; do
	case $1 in
	--max-mb)
		[ $# -ge 2 ] || kk_die "$USAGE" 2
		MAXMB=$2
		shift 2
		;;
	*)
		if [ -z "$TARGET" ]; then
			TARGET=$1
		elif [ "$SECONDS_" = 60 ] && [ -z "${GOT_SECS:-}" ]; then
			SECONDS_=$1
			GOT_SECS=1
		else
			kk_die "$USAGE" 2
		fi
		shift
		;;
	esac
done
[ -n "$TARGET" ] || kk_die "$USAGE" 2
if is_mac "$TARGET"; then
	KIND=mac
elif is_ipv4 "$TARGET" || is_ipv6 "$TARGET"; then
	KIND=ip
else
	kk_die "invalid target (want IPv4, IPv6 or MAC): $USAGE" 2
fi
is_uint "$SECONDS_" && [ ${#SECONDS_} -le 4 ] && [ "$SECONDS_" -ge 1 ] && [ "$SECONDS_" -le 3600 ] ||
	kk_die "SECONDS must be 1-3600" 2
if [ -n "$MAXMB" ]; then
	is_uint "$MAXMB" && [ ${#MAXMB} -le 8 ] || kk_die "--max-mb must be a number" 2
fi

# ---- config: uci kokabiel.main, env overrides, defaults ----

cfg() { # cfg ENVVALUE OPTION DEFAULT
	if [ -n "$1" ]; then
		echo "$1"
		return
	fi
	_v=$(kk_uci_get "$2" 2>/dev/null) || _v=
	echo "${_v:-$3}"
}
CAPTURE_IF=$(cfg "${CAPTURE_IF:-}" capture_if eth1)
CAPTURE_DIR=$(cfg "${CAPTURE_DIR:-}" capture_dir /mnt/kokabiel-cap)
SD_MAX=$(cfg "${CAPTURE_SD_MAX_MB:-}" capture_sd_max_mb 4096)
RAM_MAX=$(cfg "${CAPTURE_RAM_MAX_MB:-}" capture_ram_max_mb 100)
PROBE=$(cfg "${PROBE_NAME:-}" probe_name "")
[ -n "$PROBE" ] || PROBE=$(hostname 2>/dev/null) || PROBE=
[ -n "$PROBE" ] || PROBE=kokabiel
PROBE=$(printf '%s' "$PROBE" | tr -c 'A-Za-z0-9_-' '-')
export CAPTURE_DIR # kk_sd_capdir reads it

case $CAPTURE_DIR in
/ | /rom | /overlay | /rom/ | /overlay/ | //) kk_die "unsafe CAPTURE_DIR $CAPTURE_DIR" 3 ;;
/*) ;;
*) kk_die "CAPTURE_DIR must be absolute" 3 ;;
esac
case $CAPTURE_IF in
'' | *[!A-Za-z0-9_.-]*) kk_die "invalid capture interface" 3 ;;
esac
if ! { is_uint "$SD_MAX" && is_uint "$RAM_MAX"; }; then kk_die "invalid capture caps in config" 3; fi

# ---- storage selection ----

if kk_sd_is_mounted; then
	STORAGE=sd
	DIR=$CAPTURE_DIR/captures
	CAP=${MAXMB:-$SD_MAX}
	[ "$CAP" -ge 1 ] || kk_die "--max-mb must be at least 1" 2
	NEED=$((CAP + 256))
else
	STORAGE=ram
	DIR=/tmp/kokabiel-captures
	CAP=${MAXMB:-$RAM_MAX}
	{ [ "$CAP" -ge 100 ] && [ "$CAP" -le 500 ]; } || kk_die "RAM capture cap must be 100-500 MB (got $CAP)" 2
	NEED=$((CAP + 512))
fi

# ---- lock ----

mkdir -p "$RUN" 2>/dev/null
if ! mkdir "$LOCK" 2>/dev/null; then
	# Reclaim a lock whose owner died (SIGKILL, OOM, power loss); a lock
	# without a readable pid is treated as stale too.
	_opid=$(cat "$LOCK/pid" 2>/dev/null)
	if is_uint "$_opid" && kill -0 "$_opid" 2>/dev/null; then
		kk_die "a capture is already running (pid $_opid)" 75
	fi
	rm -rf "$LOCK"
	mkdir "$LOCK" 2>/dev/null || kk_die "a capture is already running" 75
fi
HAVE_LOCK=1
echo "$$" >"$LOCK/pid"

# ---- space / memory check ----

if [ "$STORAGE" = sd ]; then
	AVAIL=$(df -Pk "$CAPTURE_DIR" 2>/dev/null | awk 'NR == 2 { print int($4 / 1024) }')
	what="free space on $CAPTURE_DIR"
else
	AVAIL=$(awk '/^MemAvailable:/ { print int($2 / 1024) }' "${KOKABIEL_PROC:-/proc}/meminfo" 2>/dev/null)
	what="available memory"
fi
is_uint "$AVAIL" || kk_die "cannot determine $what" 4
[ "$AVAIL" -ge "$NEED" ] || kk_die "$what is ${AVAIL} MB, need at least ${NEED} MB" 4

mkdir -p "$DIR" || kk_die "cannot create $DIR" 4

# ---- capture ----

SAFE=$(printf '%s' "$TARGET" | tr ':.' '--')
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BASE=$DIR/$PROBE-$SAFE-$STAMP.pcap
START=$(date +%s)
printf 'target=%s\nstarted=%s\ndir=%s\npid=%s\n' "$TARGET" "$START" "$DIR" "$$" >"$STATE"
ERRF=$RUN/kokabiel-capture.$$.err
CSIZE=$((CAP / 5))
[ "$CSIZE" -ge 1 ] || CSIZE=1

if [ "$KIND" = mac ]; then
	timeout "$SECONDS_" tcpdump -i "$CAPTURE_IF" -s 0 -U -w "$BASE" -C "$CSIZE" -W 5 \
		ether host "$TARGET" or '(' vlan and ether host "$TARGET" ')' 2>"$ERRF" &
else
	timeout "$SECONDS_" tcpdump -i "$CAPTURE_IF" -s 0 -U -w "$BASE" -C "$CSIZE" -W 5 \
		host "$TARGET" or '(' vlan and host "$TARGET" ')' 2>"$ERRF" &
fi
# Background + wait so INT/TERM run the trap at once instead of after tcpdump.
CHILD=$!
wait "$CHILD"
RC=$?
CHILD=
END=$(date +%s)

PACKETS=$(sed -n 's/^\([0-9][0-9]*\) packets\{0,1\} captured.*/\1/p' "$ERRF" | tail -n 1)
: "${PACKETS:=0}"
case $RC in
0 | 124 | 143) EXIT=timeout ;;
*) EXIT=error ;;
esac

FILES=
BYTES=0
for f in "$BASE"*; do
	[ -f "$f" ] || continue
	_s=$(wc -c <"$f" | tr -d ' ')
	BYTES=$((BYTES + _s))
	FILES="${FILES:+$FILES,}\"$f\""
done

# Target and paths are restricted to [0-9A-Za-z:._/-], so no JSON escaping is needed.
printf '{"files":[%s],"bytes":%s,"packets":%s,"duration":%s,"storage":"%s","exit":"%s","target":"%s"}\n' \
	"$FILES" "$BYTES" "$PACKETS" "$((END - START))" "$STORAGE" "$EXIT" "$TARGET"
[ "$EXIT" = timeout ] || exit 5
exit 0
