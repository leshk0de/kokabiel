#!/bin/sh
# kokabiel-status: health checks for the probe. One line per check:
#   OK|FAIL|INFO <check> <detail>
# Exits 1 if any check is FAIL, else 0.
#
# Test hooks: KOKABIEL_SOFTFLOWCTL (default /usr/sbin/softflowctl),
#             KOKABIEL_CTL (softflowd control socket), KOKABIEL_RUN (/var/run).

KK_LIB="${KOKABIEL_LIB:-$(dirname "$0")/lib}"
[ -d "$KK_LIB" ] || KK_LIB=/usr/libexec/kokabiel
# shellcheck source=lib/common.sh disable=SC1091
. "$KK_LIB/common.sh"
# shellcheck source=lib/sd.sh disable=SC1091
. "$KK_LIB/sd.sh"
# shellcheck source=lib/flows.sh disable=SC1091
. "$KK_LIB/flows.sh"

SOFTFLOWCTL=${KOKABIEL_SOFTFLOWCTL:-/usr/sbin/softflowctl}
CTL=${KOKABIEL_CTL:-/var/run/kokabiel-softflowd.ctl}
FAILED=0

ok() { echo "OK $1 $2"; }
fail() {
	echo "FAIL $1 $2"
	FAILED=1
}
info() { echo "INFO $1 $2"; }
# check NAME CONDITION-RC DETAIL: OK if rc is 0, else FAIL.
check() {
	if [ "$2" = 0 ]; then ok "$1" "$3"; else fail "$1" "$3"; fi
}

cif=$(kk_uci_get capture_if) || cif=
if [ -z "$cif" ]; then
	fail capture_link "uci kokabiel.main.capture_if is not set"
	fail capture_promisc "uci kokabiel.main.capture_if is not set"
	fail capture_no_address "uci kokabiel.main.capture_if is not set"
else
	base=$(kk_sysfs "class/net/$cif")
	carrier=$(cat "$base/carrier" 2>/dev/null) || carrier=
	[ "$carrier" = 1 ] && rc=0 || rc=1
	check capture_link "$rc" "$cif carrier=${carrier:-unknown}"

	flags=$(cat "$base/flags" 2>/dev/null) || flags=
	promisc=1
	case $flags in
	0x[0-9a-fA-F]*) [ $(((flags / 256) % 2)) = 1 ] && promisc=0 ;;
	esac
	check capture_promisc "$promisc" "$cif flags=${flags:-unknown}"

	addrs=$(ip -o addr show dev "$cif" 2>/dev/null)
	[ -z "$addrs" ] && rc=0 || rc=1
	check capture_no_address "$rc" "$cif ${addrs:-has no address}"
fi

# mac_guard: live MACs equal the ones recorded at setup.
guard=0
detail="capture and management MACs match"
for pair in "capture_if capture_mac" "mgmt_if mgmt_mac"; do
	# shellcheck disable=SC2086 # intentional word splitting
	set -- $pair
	ifn=$(kk_uci_get "$1") || ifn=
	want=$(kk_uci_get "$2") || want=
	if [ -z "$ifn" ] || [ -z "$want" ]; then
		guard=1
		detail="$1/$2 not recorded in uci"
		break
	fi
	have=$(kk_mac "$ifn" 2>/dev/null) || have=
	# shellcheck disable=SC2018,SC2019 # BusyBox tr lacks reliable [:class:] support
	want=$(echo "$want" | tr 'A-Z' 'a-z')
	if [ "$have" != "$want" ]; then
		guard=1
		detail="$ifn is ${have:-unreadable}, expected $want"
		break
	fi
done
check mac_guard "$guard" "$detail"

if [ -f "$KK_SHIM" ]; then ok flows_shim "$KK_SHIM"; else fail flows_shim "missing $KK_SHIM (softflowd processes no packets without it)"; fi

stats=$("$SOFTFLOWCTL" -c "$CTL" statistics 2>/dev/null)
src=$?
check softflowd_running "$src" "control socket $CTL"

# listeners: non-loopback TCP listeners must be a subset of {22, 9100}.
bad=$(netstat -tln 2>/dev/null | awk '
	$1 ~ /^tcp/ && $4 != "" {
		n = split($4, a, ":"); port = a[n]
		host = substr($4, 1, length($4) - length(port) - 1)
		if (host ~ /^127\./ || host == "::1" || host == "[::1]") next
		if (port == "22" || port == "9100") next
		printf "%s%s", sep, port; sep = ","
	}')
if [ -z "$bad" ]; then
	ok listeners "only 22 and 9100 on non-loopback addresses"
else
	fail listeners "unexpected ports $bad"
fi

# ---- INFO ----

capdir=$(kk_sd_capdir)
if kk_sd_describe | grep -q '"present":true'; then
	free=$(df -Pk "$capdir" 2>/dev/null | awk 'NR == 2 { print $4 * 1024 }')
	if kk_sd_is_mounted; then
		info sd "present free_bytes=${free:-unknown}"
	else
		info sd "present unmounted"
	fi
else
	info sd absent
fi
info collector "$(kk_uci_get collector_host):$(kk_uci_get collector_port)"
if [ -n "$cif" ]; then
	rxd=$(cat "$base/statistics/rx_dropped" 2>/dev/null) || rxd=
	rxm=$(cat "$base/statistics/rx_missed_errors" 2>/dev/null) || rxm=
	rxp=$(cat "$base/statistics/rx_packets" 2>/dev/null) || rxp=
	info rx "packets=${rxp:-?} dropped=${rxd:-?} missed=${rxm:-?}"
fi
pd=$(echo "$stats" | sed -n 's/^Packets dropped by libpcap: *\([0-9][0-9]*\).*/\1/p')
info pcap_dropped "${pd:-unknown}"

exit "$FAILED"
