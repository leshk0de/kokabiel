#!/bin/sh
# Kokabiel probe setup: network, firewall, services, persistence. Idempotent.
# Usage: setup.sh [--env FILE] [--skip-packages]
# Exit codes: 0 ok, 3 config/usage, 4 lock-out guard.
#
# Test hooks: KOKABIEL_INSTALL_ROOT prefixes every installed file (not /etc/config).

KK_LIB="${KOKABIEL_LIB:-$(dirname "$0")/lib}"
[ -d "$KK_LIB" ] || KK_LIB=/usr/libexec/kokabiel
# shellcheck source=lib/common.sh disable=SC1091
. "$KK_LIB/common.sh"
# shellcheck source=lib/sd.sh disable=SC1091
. "$KK_LIB/sd.sh"

SCRIPT_DIR=$(dirname "$0")
ROOT=${KOKABIEL_INSTALL_ROOT:-}
LIBEXEC=$ROOT/usr/libexec/kokabiel

# _carries_capture DEV: true if DEV is CAPTURE_IF or a bridge that has it as a port.
_carries_capture() {
	[ "$1" = "$CAPTURE_IF" ] && return 0
	[ -e "$(kk_sysfs "class/net/$1/brif/$CAPTURE_IF")" ] && return 0
	for _s in $(uci_sections network device); do
		[ "$(uci -q get "$_s.name")" = "$1" ] || continue
		uci -q get "$_s.ports" | tr ' ' '\n' | grep -qx "$CAPTURE_IF" && return 0
	done
	return 1
}

# guard_lockout: refuse before touching anything if CAPTURE_IF (directly or as a
# bridge port) carries the default route or the local address of this SSH session.
guard_lockout() {
	_devs=$( (ip route show default; ip -6 route show default) 2>/dev/null |
		awk '{ for (i = 1; i < NF; i++) if ($i == "dev") print $(i + 1) }')
	for _d in $_devs; do
		_carries_capture "$_d" &&
			kk_die "$_d (which includes $CAPTURE_IF) carries the default route; refusing to take $CAPTURE_IF out of service (set CAPTURE_IF to the mirror port)" 4
	done
	if [ -n "${SSH_CONNECTION:-}" ]; then
		# shellcheck disable=SC2086 # intentional word splitting
		set -- $SSH_CONNECTION
		_local=${3:-}
		if [ -n "$_local" ]; then
			_devs=$(ip -o addr show 2>/dev/null |
				awk -v a="$_local" '{ for (i = 3; i <= NF; i++) if ($i == "inet" || $i == "inet6") { split($(i + 1), p, "/"); if (p[1] == a) print $2 } }')
			for _d in $_devs; do
				_d=${_d%%@*}
				_carries_capture "$_d" &&
					kk_die "this SSH session arrives on $_d ($_local), which includes $CAPTURE_IF; refusing to lock you out" 4
			done
		fi
	fi
}

install_packages() {
	[ "$SKIP_PACKAGES" = 1 ] && return 0
	kk_pkg_install softflowd ethtool block-mount e2fsprogs sfdisk tcpdump prometheus-node-exporter-lua
}

# install_file SRC DST MODE: copy only when content differs.
install_file() {
	if [ -f "$2" ] && cmp -s "$1" "$2"; then
		chmod "$3" "$2"
		return 0
	fi
	mkdir -p "$(dirname "$2")"
	cp "$1" "$2" || kk_die "cannot install $2"
	chmod "$3" "$2"
}

# shim_is_aarch64 FILE: ELF64, little-endian, ET_DYN, EM_AARCH64 (header via od or hexdump).
shim_is_aarch64() {
	# BusyBox on OpenWrt ships hexdump but not od; accept either.
	if command -v od >/dev/null 2>&1; then
		_h=$(od -An -v -tx1 -N20 "$1" 2>/dev/null | tr -d '\n') || return 1
	else
		_h=$(head -c 20 "$1" 2>/dev/null | hexdump -v -e '1/1 "%02x "' 2>/dev/null) || return 1
	fi
	# shellcheck disable=SC2086 # intentional word splitting
	set -- $_h
	[ $# -ge 20 ] || return 1
	[ "$1 $2 $3 $4 $5 $6" = "7f 45 4c 46 02 01" ] &&
		[ "${17} ${18}" = "03 00" ] && [ "${19} ${20}" = "b7 00" ]
}

# install_shim: the libpcap immediate-mode LD_PRELOAD shim (built by 'make shim').
install_shim() {
	_so=
	for _c in "$SCRIPT_DIR/../build/pcap_immediate.so" "$SCRIPT_DIR/files/pcap_immediate.so"; do
		[ -f "$_c" ] && _so=$_c && break
	done
	_dst=$ROOT/usr/lib/kokabiel/pcap_immediate.so
	if [ -n "$_so" ] && ! shim_is_aarch64 "$_so"; then
		_m="pcap_immediate.so is not an aarch64 shared object; not installed"
		logger -t kokabiel -p daemon.err "$_m" 2>/dev/null || true
		echo "kokabiel: ERROR: $_m" >&2
		rm -f "$_dst"
		return 0
	fi
	if [ -n "$_so" ]; then
		install_file "$_so" "$_dst" 0644
	else
		kk_log "WARNING: softflowd will not process packets without the immediate-mode shim; run 'make shim'"
	fi
}

install_files() {
	for _f in "$KK_LIB"/*.sh; do
		install_file "$_f" "$LIBEXEC/$(basename "$_f")" 0644
	done
	install_file "$SCRIPT_DIR/sd.sh" "$ROOT/usr/sbin/kokabiel-sd" 0755
	install_file "$SCRIPT_DIR/format-sd.sh" "$ROOT/usr/sbin/kokabiel-format-sd" 0755
	install_file "$SCRIPT_DIR/capture.sh" "$ROOT/usr/sbin/kokabiel-capture" 0755
	install_file "$SCRIPT_DIR/status.sh" "$ROOT/usr/sbin/kokabiel-status" 0755
	install_file "$SCRIPT_DIR/files/prometheus-collectors/kokabiel.lua" \
		"$ROOT/usr/lib/lua/prometheus-collectors/kokabiel.lua" 0644
	install_file "$SCRIPT_DIR/files/capture-prep" "$LIBEXEC/capture-prep" 0755
	install_file "$SCRIPT_DIR/files/init.d/kokabiel" "$ROOT/etc/init.d/kokabiel" 0755
	install_file "$SCRIPT_DIR/files/init.d/kokabiel-flows" "$ROOT/etc/init.d/kokabiel-flows" 0755
	install_shim
	install_file "$SCRIPT_DIR/files/hotplug/30-kokabiel" "$ROOT/etc/hotplug.d/iface/30-kokabiel" 0644
	# Keep the installed files across sysupgrade.
	mkdir -p "$ROOT/etc"
	for _p in /usr/libexec/kokabiel/ /usr/lib/kokabiel/ /etc/init.d/kokabiel /etc/init.d/kokabiel-flows /etc/hotplug.d/iface/30-kokabiel /etc/kokabiel/ /usr/sbin/kokabiel-sd /usr/sbin/kokabiel-format-sd /usr/sbin/kokabiel-capture /usr/sbin/kokabiel-status /usr/lib/lua/prometheus-collectors/kokabiel.lua; do
		grep -qxF "$_p" "$ROOT/etc/sysupgrade.conf" 2>/dev/null ||
			echo "$_p" >>"$ROOT/etc/sysupgrade.conf"
	done
}

# uci_set_opt PKG.SEC.OPT VALUE: set, or delete when VALUE is empty.
uci_set_opt() {
	if [ -n "$2" ]; then uci set "$1=$2"; else uci -q delete "$1" || true; fi
}

write_kokabiel_config() {
	[ -e /etc/config/kokabiel ] || : >/etc/config/kokabiel
	uci set kokabiel.main=kokabiel
	uci set kokabiel.main.collector_host="$COLLECTOR_HOST"
	uci set kokabiel.main.collector_port="$COLLECTOR_PORT"
	uci set kokabiel.main.export_version="$EXPORT_VERSION"
	uci set kokabiel.main.capture_if="$CAPTURE_IF"
	uci set kokabiel.main.capture_mac="$(kk_mac "$CAPTURE_IF")"
	uci set kokabiel.main.mgmt_if="$MGMT_IF"
	uci set kokabiel.main.mgmt_mac="$(kk_mac "$MGMT_IF")"
	uci_set_opt kokabiel.main.probe_name "$PROBE_NAME"
	uci set kokabiel.main.capture_dir="$CAPTURE_DIR"
	uci set kokabiel.main.capture_sd_max_mb="$CAPTURE_SD_MAX_MB"
	uci set kokabiel.main.capture_ram_max_mb="$CAPTURE_RAM_MAX_MB"
	uci set kokabiel.main.track_level="$TRACK_LEVEL"
}

# uci_sections PKG TYPE: print "pkg.section" for every section of TYPE.
uci_sections() {
	uci show "$1" 2>/dev/null | sed -n "s/^\\($1\\.[^.=]*\\)=$2\$/\\1/p"
}

configure_network() {
	_brs=$(uci_sections network device)
	for _s in $_brs; do
		[ "$(uci -q get "$_s.type")" = bridge ] || continue
		uci -q get "$_s.ports" | tr ' ' '\n' | grep -qx "$CAPTURE_IF" &&
			uci del_list "$_s.ports=$CAPTURE_IF"
		if [ -z "$(uci -q get "$_s.ports")" ]; then
			_br=$(uci -q get "$_s.name")
			for _i in $(uci_sections network interface); do
				[ "$(uci -q get "$_i.device")" = "$_br" ] && uci set "$_i.disabled=1"
			done
		fi
	done

	uci set network.capture=interface
	uci set network.capture.proto=none
	uci set network.capture.device="$CAPTURE_IF"

	# ipv6 off on the capture device: reuse an existing device section if any.
	_dev=
	for _s in $_brs; do
		[ "$(uci -q get "$_s.name")" = "$CAPTURE_IF" ] && _dev=$_s && break
	done
	if [ -z "$_dev" ]; then
		_dev=network.kokabiel_capture_dev
		uci set "$_dev=device"
		uci set "$_dev.name=$CAPTURE_IF"
	fi
	uci set "$_dev.ipv6=0"
}

# dnsmasq answers on loopback only: no DNS listener on the network.
configure_dnsmasq() {
	_dm='dhcp.@dnsmasq[0]'
	uci -q get "$_dm" >/dev/null 2>&1 || return 0
	uci set "$_dm.localuse=1"
	uci -q delete "$_dm.interface" || true
	uci add_list "$_dm.interface=lo"
	uci -q delete "$_dm.notinterface" || true
	uci add_list "$_dm.notinterface=wan"
}

configure_dhcp() {
	configure_dnsmasq
	uci set dhcp.lan.ignore=1
	uci set dhcp.lan.dhcpv6=disabled
	uci set dhcp.lan.ra=disabled
}

configure_firewall() {
	uci set firewall.kokabiel_capture=zone
	uci set firewall.kokabiel_capture.name=kokabiel_capture
	uci set firewall.kokabiel_capture.input=DROP
	uci set firewall.kokabiel_capture.output=DROP
	uci set firewall.kokabiel_capture.forward=DROP
	uci -q delete firewall.kokabiel_capture.network || true
	uci add_list firewall.kokabiel_capture.network=capture

	for _z in $(uci_sections firewall zone); do
		[ "$_z" = firewall.kokabiel_capture ] && continue
		uci -q get "$_z.network" | tr ' ' '\n' | grep -qx capture &&
			uci del_list "$_z.network=capture"
	done

	# Keep any existing Allow-SSH-WAN rule; otherwise create the named one.
	if ! uci show firewall 2>/dev/null | grep -q "\\.name='Allow-SSH-WAN'\$"; then
		uci set firewall.allow_ssh_wan=rule
		uci set firewall.allow_ssh_wan.name=Allow-SSH-WAN
		uci set firewall.allow_ssh_wan.src=wan
		uci set firewall.allow_ssh_wan.proto=tcp
		uci set firewall.allow_ssh_wan.dest_port=22
		uci set firewall.allow_ssh_wan.target=ACCEPT
	fi
}

# ipv4_network DEV: print the IPv4 network (a.b.c.d/len) of DEV's first address.
ipv4_network() {
	ip -o -4 addr show dev "$1" 2>/dev/null | awk '
		{
			for (i = 1; i < NF; i++) if ($i == "inet") { c = $(i + 1); break }
			if (c == "") exit
			split(c, a, "/"); split(a[1], o, "."); p = a[2] + 0
			if (p < 1 || p > 32 || length(o) != 4) exit
			for (i = 1; i <= 4; i++) {
				bits = p - (i - 1) * 8
				if (bits < 0) bits = 0
				if (bits > 8) bits = 8
				blk = 2 ^ (8 - bits)
				n[i] = int(o[i] / blk) * blk
			}
			printf "%d.%d.%d.%d/%d\n", n[1], n[2], n[3], n[4], p
			exit
		}'
}

# configure_metrics: exporter listens on the wan interface, port 9100; the
# firewall opens it only to METRICS_ALLOWED_SRC (default: MGMT_IF's subnet).
# With no computable source the port stays closed.
configure_metrics() {
	[ -e /etc/config/prometheus-node-exporter-lua ] || : >/etc/config/prometheus-node-exporter-lua
	uci set prometheus-node-exporter-lua.main=prometheus-node-exporter-lua
	uci set prometheus-node-exporter-lua.main.listen_interface=wan
	uci set prometheus-node-exporter-lua.main.listen_port=9100

	_src=$METRICS_ALLOWED_SRC
	[ -n "$_src" ] || _src=$(ipv4_network "$MGMT_IF")
	if [ -z "$_src" ]; then
		kk_log "WARNING: cannot compute $MGMT_IF's IPv4 network and METRICS_ALLOWED_SRC is unset; metrics port 9100 stays closed"
		uci -q delete firewall.kokabiel_metrics || true
		return 0
	fi
	uci set firewall.kokabiel_metrics=rule
	uci set firewall.kokabiel_metrics.name=Allow-Kokabiel-Metrics
	uci set firewall.kokabiel_metrics.src=wan
	uci set firewall.kokabiel_metrics.src_ip="$_src"
	uci set firewall.kokabiel_metrics.proto=tcp
	uci set firewall.kokabiel_metrics.dest_port=9100
	uci set firewall.kokabiel_metrics.target=ACCEPT
}

configure_dropbear() {
	uci -q delete 'dropbear.@dropbear[0].Interface' || true
}

# shellcheck disable=SC2153 # SYSLOG_* come from kk_load_config
configure_system() {
	[ -z "$PROBE_NAME" ] || uci set "system.@system[0].hostname=$PROBE_NAME"
	if [ -n "$SYSLOG_HOST" ]; then
		uci set "system.@system[0].log_ip=$SYSLOG_HOST"
		uci set "system.@system[0].log_port=$SYSLOG_PORT"
		uci set "system.@system[0].log_proto=$SYSLOG_PROTO"
	else
		for _o in log_ip log_port log_proto; do
			uci -q delete "system.@system[0].$_o" || true
		done
	fi
}

configure_services() {
	# shellcheck disable=SC2086 # intentional word splitting of the list
	for _s in $KOKABIEL_DISABLE_SERVICES; do
		kk_svc "$_s" stop
		kk_svc "$_s" disable
	done
	if [ "$ENABLE_LUCI" = 1 ]; then
		uci -q delete uhttpd.main.listen_http || true
		uci add_list uhttpd.main.listen_http=127.0.0.1:80
		uci -q delete uhttpd.main.listen_https || true
		kk_svc uhttpd enable
	else
		kk_svc uhttpd stop
		kk_svc uhttpd disable
	fi
}

apply() {
	uci commit
	kk_svc network reload
	kk_svc firewall restart
	kk_svc dropbear restart
	kk_svc prometheus-node-exporter-lua enable
	kk_svc prometheus-node-exporter-lua restart
	[ "$ENABLE_LUCI" = 1 ] && kk_svc uhttpd restart
	kk_svc kokabiel enable
	kk_svc kokabiel start
	# Kokabiel runs softflowd itself; the packaged service must not.
	kk_svc softflowd stop
	kk_svc softflowd disable
	kk_svc kokabiel-flows enable
	kk_svc kokabiel-flows restart
}

main() {
	ENV_FILE=
	SKIP_PACKAGES=0
	while [ $# -gt 0 ]; do
		case $1 in
		--env)
			[ $# -ge 2 ] || kk_die "--env needs a file"
			ENV_FILE=$2
			shift 2
			;;
		--skip-packages)
			SKIP_PACKAGES=1
			shift
			;;
		*) kk_die "usage: setup.sh [--env FILE] [--skip-packages]" ;;
		esac
	done

	kk_load_config ${ENV_FILE:+"$ENV_FILE"}
	guard_lockout
	install_packages
	install_files
	write_kokabiel_config
	configure_network
	configure_dhcp
	configure_firewall
	configure_dropbear
	configure_metrics
	configure_system
	configure_services
	apply
	kk_sd_mount
	kk_log "setup complete"
}

main "$@"
