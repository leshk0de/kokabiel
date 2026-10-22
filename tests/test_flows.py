LIB = ". /work/scripts/lib/common.sh; . /work/scripts/lib/flows.sh"
INIT = "/work/scripts/files/init.d/kokabiel-flows"
SETUP = "/work/scripts/setup.sh"
ENV = {"KOKABIEL_LIB": "/work/scripts/lib", "KOKABIEL_INSTALL_ROOT": "/tmp/root"}

BASE = (
    "softflowd -d -i eth1 -n 192.0.2.10:2055 -v {ver} -m 65536 -T {track} "
    "-t maxlife=300 -t tcp.rst=10 -t tcp.fin=10 -t udp=60 -t icmp=30 "
    "-t general=60 -c /var/run/kokabiel-softflowd.ctl"
)


def seed(probe, **extra):
    opts = {"collector_host": "192.0.2.10", "collector_port": "2055",
            "export_version": "10", "capture_if": "eth1", **extra}
    cmds = ["uci set kokabiel.main=kokabiel"]
    cmds += [f"uci set kokabiel.main.{k}={v}" for k, v in opts.items()]
    cmds.append("uci commit kokabiel")
    probe.run("touch /etc/config/kokabiel; " + "; ".join(cmds))


def test_flows_cmdline_defaults(probe):
    seed(probe)
    r = probe.run(f"{LIB}; kk_flows_cmdline")
    assert r.rc == 0, r.err
    assert r.out.strip() == BASE.format(ver=10, track="full")


def test_flows_cmdline_v9(probe):
    seed(probe, export_version="9", track_level="ether", collector_port="2056")
    r = probe.run(f"{LIB}; kk_flows_cmdline")
    assert r.rc == 0, r.err
    want = BASE.format(ver=9, track="ether").replace(":2055", ":2056")
    assert r.out.strip() == want


def test_flows_init_has_respawn(probe):
    src = probe.read(INIT)
    assert "procd_set_param respawn" in src
    assert "START=60" in src
    assert "USE_PROCD=1" in src
    assert "flows.sh" in src and "kk_flows_cmdline" in src


def test_setup_disables_packaged_softflowd(probe):
    probe.write("/tmp/site.env", "COLLECTOR_HOST=192.0.2.10\n")
    probe.fake_nic("eth0", "02:00:00:00:00:01")
    probe.fake_nic("eth1", "02:00:00:00:00:02")
    r = probe.run(f"{SETUP} --env /tmp/site.env --skip-packages", env=ENV)
    assert r.rc == 0, r.err
    calls = probe.calls()
    for want in ("softflowd stop", "softflowd disable",
                 "kokabiel-flows enable", "kokabiel-flows restart"):
        assert want in calls
    assert calls.index("softflowd stop") < calls.index("kokabiel-flows restart")
    ls = probe.run("ls -l /tmp/root/etc/init.d/kokabiel-flows").out
    assert ls.startswith("-rwxr-xr-x")


def test_setup_installs_softflowd_and_ethtool(probe):
    probe.write("/tmp/site.env", "COLLECTOR_HOST=192.0.2.10\n")
    probe.fake_nic("eth0", "02:00:00:00:00:01")
    probe.fake_nic("eth1", "02:00:00:00:00:02")
    probe.stub("apk", 'echo "apk $*" >> /tmp/stubs.log\n[ "$1" = info ] && exit 1\nexit 0')
    r = probe.run(f"{SETUP} --env /tmp/site.env", env=ENV)
    assert r.rc == 0, r.err
    assert any(c.startswith("apk add") and "softflowd" in c and "ethtool" in c
               for c in probe.calls("apk"))
