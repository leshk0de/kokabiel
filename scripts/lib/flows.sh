# shellcheck shell=sh
# Kokabiel flow-export library. Source after common.sh; do not execute.

# LD_PRELOAD shim forcing libpcap immediate mode (softflowd 1.1.0 lacks it).
# Test hook: KOKABIEL_SHIM overrides the path.
# shellcheck disable=SC2034 # read by init.d/kokabiel-flows and status
KK_SHIM=${KOKABIEL_SHIM:-/usr/lib/kokabiel/pcap_immediate.so}

# kk_flows_cmdline: print the exact softflowd argv from uci kokabiel.main.
# track_level defaults to full, collector_port to 2055, export_version to 10.
kk_flows_cmdline() {
	_kk_if=$(kk_uci_get capture_if) || _kk_if=
	_kk_host=$(kk_uci_get collector_host) || _kk_host=
	_kk_port=$(kk_uci_get collector_port) || _kk_port=
	_kk_ver=$(kk_uci_get export_version) || _kk_ver=
	_kk_track=$(kk_uci_get track_level) || _kk_track=
	[ -n "$_kk_if" ] || kk_die "uci kokabiel.main.capture_if is not set"
	[ -n "$_kk_host" ] || kk_die "uci kokabiel.main.collector_host is not set"
	echo "softflowd -d -i $_kk_if -n $_kk_host:${_kk_port:-2055} -v ${_kk_ver:-10}" \
		"-m 65536 -T ${_kk_track:-full}" \
		"-t maxlife=300 -t tcp.rst=10 -t tcp.fin=10 -t udp=60 -t icmp=30 -t general=60" \
		"-c /var/run/kokabiel-softflowd.ctl"
}
