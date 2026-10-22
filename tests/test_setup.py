SETUP = "/work/scripts/setup.sh"
ENV = {"KOKABIEL_LIB": "/work/scripts/lib", "KOKABIEL_INSTALL_ROOT": "/tmp/root"}
DEFAULT_DISABLE = (
    "ttyd vsftpd samba4 wsdd2 minidlna aria2 miniupnpd adblock smartdns ddns sqm "
    "nlbwmon radius avahi-daemon collectd luci_statistics watchcat hd-idle "
    "fa-fancontrol odhcpd"
).split()


def run_setup(probe, site="COLLECTOR_HOST=192.0.2.10\n", env=None):
    probe.write("/tmp/site.env", site)
    probe.fake_nic("eth0", "02:00:00:00:00:01")
    probe.fake_nic("eth1", "02:00:00:00:00:02")
    return probe.run(f"{SETUP} --env /tmp/site.env --skip-packages",
                     env={**ENV, **(env or {})})


def snapshot(probe):
    return probe.run("cat /etc/config/* | md5sum; uci changes").out


def test_setup_detaches_capture_from_lan(probe):
    r = run_setup(probe)
    assert r.rc == 0, r.err
    assert "eth1" not in probe.uci("network.@device[1].ports")
    assert probe.uci("network.lan.disabled") == "1"
    assert probe.uci("network.capture.proto") == "none"
    assert probe.uci("network.capture.device") == "eth1"
    assert probe.uci("dhcp.lan.ignore") == "1"
    assert probe.uci("dhcp.lan.dhcpv6") == "disabled"
    assert probe.uci("dhcp.lan.ra") == "disabled"
    # the pre-existing eth1 device section gains ipv6 off and keeps its MAC pin
    assert probe.uci("network.@device[2].name") == "eth1"
    assert probe.uci("network.@device[2].ipv6") == "0"
    assert probe.uci("network.@device[2].macaddr") == "02:00:00:00:00:02"


def test_setup_capture_zone_drops(probe):
    assert run_setup(probe).rc == 0
    z = "firewall.kokabiel_capture"
    assert probe.uci(f"{z}.name") == "kokabiel_capture"
    for opt in ("input", "output", "forward"):
        assert probe.uci(f"{z}.{opt}") == "DROP"
    assert probe.uci(f"{z}.network") == "capture"


def test_setup_removes_capture_from_other_zones(probe):
    probe.run("uci add_list firewall.@zone[0].network=capture; uci commit firewall")
    assert run_setup(probe).rc == 0
    assert "capture" not in probe.uci("firewall.@zone[0].network").split()


def test_setup_ssh_wan_rule_present_once(probe):
    assert run_setup(probe).rc == 0
    out = probe.run(
        "uci show firewall | grep -c \"\\.name='Allow-SSH-WAN'\""
    ).out.strip()
    assert out == "1"
    assert probe.uci("firewall.allow_ssh_wan.dest_port") == "22"
    assert probe.uci("firewall.allow_ssh_wan.src") == "wan"
    assert probe.uci("firewall.allow_ssh_wan.target") == "ACCEPT"


def test_setup_disables_services(probe):
    assert run_setup(probe).rc == 0
    calls = probe.calls()
    for s in DEFAULT_DISABLE + ["uhttpd"]:
        assert f"{s} stop" in calls, s
        assert f"{s} disable" in calls, s
    assert "dropbear disable" not in calls
    for want in ("network reload", "firewall restart", "dropbear restart",
                 "kokabiel enable", "kokabiel start"):
        assert want in calls


def test_setup_disable_list_overridable(probe):
    r = run_setup(probe, "COLLECTOR_HOST=192.0.2.10\nKOKABIEL_DISABLE_SERVICES='ttyd'\n")
    assert r.rc == 0, r.err
    calls = probe.calls()
    assert "ttyd disable" in calls and "samba4 disable" not in calls


def test_setup_luci_localhost_when_enabled(probe):
    r = run_setup(probe, "COLLECTOR_HOST=192.0.2.10\nENABLE_LUCI=1\n")
    assert r.rc == 0, r.err
    assert probe.uci("uhttpd.main.listen_http") == "127.0.0.1:80"
    assert probe.uci("uhttpd.main.listen_https") == ""
    assert "uhttpd disable" not in probe.calls()
    assert "uhttpd enable" in probe.calls()


def test_setup_records_macs_and_options(probe):
    assert run_setup(probe, "COLLECTOR_HOST=192.0.2.10\nPROBE_NAME=probe-a\n").rc == 0
    assert probe.uci("kokabiel.main.capture_mac") == "02:00:00:00:00:02"
    assert probe.uci("kokabiel.main.mgmt_mac") == "02:00:00:00:00:01"
    assert probe.uci("kokabiel.main.capture_if") == "eth1"
    assert probe.uci("kokabiel.main.mgmt_if") == "eth0"
    assert probe.uci("kokabiel.main.collector_host") == "192.0.2.10"
    assert probe.uci("kokabiel.main.probe_name") == "probe-a"
    assert probe.uci("system.@system[0].hostname") == "probe-a"


def test_setup_installs_files(probe):
    assert run_setup(probe).rc == 0
    r = probe.run("cd /tmp/root && ls -l usr/libexec/kokabiel/common.sh "
                  "usr/libexec/kokabiel/capture-prep etc/init.d/kokabiel "
                  "etc/hotplug.d/iface/30-kokabiel")
    modes = [ln.split()[0] for ln in r.out.splitlines()]
    assert modes == ["-rw-r--r--", "-rwxr-xr-x", "-rwxr-xr-x", "-rw-r--r--"], r.out


def test_setup_dropbear_interface_deleted(probe):
    probe.run("uci set dropbear.main.Interface=lan; uci commit dropbear")
    assert run_setup(probe).rc == 0
    assert probe.uci("dropbear.@dropbear[0].Interface") == ""


def ip_stub(probe, addr_lines="", route4="", route6=""):
    probe.stub("ip", f"""case "$*" in
"-o addr show") cat <<'EOT'
{addr_lines}
EOT
;;
"route show default") cat <<'EOT'
{route4}
EOT
;;
"-6 route show default") cat <<'EOT'
{route6}
EOT
;;
esac""")


def refused(probe, env=None, bridge_ports=None, brif=False):
    probe.fake_nic("eth0", "02:00:00:00:00:01")
    probe.fake_nic("eth1", "02:00:00:00:00:02")
    if brif:
        probe.run("mkdir -p /tmp/sys/class/net/br-lan/brif/eth1")
    before = snapshot(probe)
    r = run_setup(probe, env=env)
    assert r.rc == 4, r.err
    assert snapshot(probe) == before
    assert probe.run("uci changes").out.strip() == ""
    assert probe.run("ls /etc/config/kokabiel").rc != 0
    assert probe.run("find /tmp/root -type f 2>/dev/null").out.strip() == ""


SSH4 = {"SSH_CONNECTION": "198.51.100.5 5000 192.0.2.20 22"}
BRLAN4 = "5: br-lan    inet 192.0.2.20/24 brd 192.0.2.255 scope global br-lan"


def test_setup_refuses_when_capture_if_is_ssh_path(probe):
    ip_stub(probe, "3: eth1    inet 192.0.2.20/24 brd 192.0.2.255 scope global eth1")
    refused(probe, SSH4)


def test_setup_refuses_ssh_on_bridge_with_capture_port_uci(probe):
    ip_stub(probe, BRLAN4)  # fixture br-lan has ports eth1, no sysfs
    refused(probe, SSH4)


def test_setup_refuses_ssh_on_bridge_with_capture_port_sysfs(probe):
    ip_stub(probe, BRLAN4)
    probe.run("uci delete network.@device[1].ports; uci commit network")
    refused(probe, SSH4, brif=True)


def test_setup_refuses_ssh_ipv6_on_capture_if(probe):
    ip_stub(probe, "3: eth1    inet6 2001:db8::20/64 scope global")
    refused(probe, {"SSH_CONNECTION": "2001:db8::5 5000 2001:db8::20 22"})


def test_setup_refuses_default_route_on_capture_if(probe):
    ip_stub(probe, route4="default via 192.0.2.1 dev eth1")
    refused(probe)


def test_setup_refuses_default_route_via_bridge_with_capture(probe):
    ip_stub(probe, route4="default via 192.0.2.1 dev br-lan")
    refused(probe)


def test_setup_refuses_ipv6_default_route_on_capture_if(probe):
    ip_stub(probe, route6="default via fe80::1 dev eth1 metric 1024")
    refused(probe)


def test_setup_proceeds_when_ssh_arrives_on_mgmt_if(probe):
    ip_stub(probe, "2: eth0    inet 192.0.2.20/24 scope global eth0",
            route4="default via 192.0.2.1 dev eth0")
    r = run_setup(probe, env=SSH4)
    assert r.rc == 0, r.err


def test_setup_twice_is_idempotent(probe):
    assert run_setup(probe).rc == 0
    first = snapshot(probe)
    cfg1 = probe.run("for f in /etc/config/*; do echo $f; cat $f; done").out
    assert run_setup(probe).rc == 0
    assert probe.run("for f in /etc/config/*; do echo $f; cat $f; done").out == cfg1
    assert snapshot(probe) == first
    assert probe.run("uci changes").out.strip() == ""


def test_setup_syslog_optional(probe):
    assert run_setup(probe).rc == 0
    assert probe.uci("system.@system[0].log_ip") == ""
    assert run_setup(probe, "COLLECTOR_HOST=192.0.2.10\nSYSLOG_HOST=192.0.2.30\n").rc == 0
    assert probe.uci("system.@system[0].log_ip") == "192.0.2.30"
    assert probe.uci("system.@system[0].log_port") == "514"
    assert probe.uci("system.@system[0].log_proto") == "udp"
    # turning it back off removes it
    assert run_setup(probe).rc == 0
    assert probe.uci("system.@system[0].log_ip") == ""


def test_setup_bad_config_exits_3(probe):
    r = run_setup(probe, "COLLECTOR_HOST=<COLLECTOR_IP>\n")
    assert r.rc == 3
