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

SCRIPT_DIR=$(dirname "$0")
ROOT=${KOKABIEL_INSTALL_ROOT:-}
LIBEXEC=$ROOT/usr/libexec/kokabiel

# guard_lockout: refuse before touching anything if CAPTURE_IF is how we are
# reaching the box (default route, or the local address of this SSH session).
guard_lockout() {
	if ip route show default 2>/dev/null | grep -Eq "(^|[[:space:]])dev $CAPTURE_IF([[:space:]]|\$)"; then
		kk_die "$CAPTURE_IF carries the default route; refusing to take it out of service (set CAPTURE_IF to the mirror port)" 4
	fi
	if [ -n "${SSH_CONNECTION:-}" ]; then
		# shellcheck disable=SC2086 # intentional word splitting
		set -- $SSH_CONNECTION
		_local=${3:-}
		if [ -n "$_local" ] &&
			ip -o -4 addr show dev "$CAPTURE_IF" 2>/dev/null | grep -Eq "inet $_local/"; then
			kk_die "this SSH session arrives on $CAPTURE_IF ($_local); refusing to lock you out" 4
		fi
	fi
}

install_packages() {
	[ "$SKIP_PACKAGES" = 1 ] && return 0
	# Package list is extended by later tasks (softflowd, exporter, ...).
	return 0
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

install_files() {
	for _f in "$KK_LIB"/*.sh; do
		install_file "$_f" "$LIBEXEC/$(basename "$_f")" 0644
	done
	install_file "$SCRIPT_DIR/files/capture-prep" "$LIBEXEC/capture-prep" 0755
	install_file "$SCRIPT_DIR/files/init.d/kokabiel" "$ROOT/etc/init.d/kokabiel" 0755
	install_file "$SCRIPT_DIR/files/hotplug/30-kokabiel" "$ROOT/etc/hotplug.d/iface/30-kokabiel" 0644
	# Keep the installed files across sysupgrade.
	mkdir -p "$ROOT/etc"
	for _p in /usr/libexec/kokabiel/ /etc/init.d/kokabiel /etc/hotplug.d/iface/30-kokabiel /etc/kokabiel/; do
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

configure_dhcp() {
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
	[ "$ENABLE_LUCI" = 1 ] && kk_svc uhttpd restart
	kk_svc kokabiel enable
	kk_svc kokabiel start
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
	configure_system
	configure_services
	apply
	kk_log "setup complete"
}

main "$@"
