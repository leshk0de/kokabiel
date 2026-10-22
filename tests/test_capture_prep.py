ENV = {"KOKABIEL_LIB": "/work/scripts/lib"}
PREP = "/work/scripts/files/capture-prep"
HOTPLUG = "/work/scripts/files/hotplug/30-kokabiel"
INIT = "/work/scripts/files/init.d/kokabiel"

CONF = """config kokabiel 'main'
\toption capture_if 'eth1'
\toption capture_mac '02:00:00:00:00:02'
\toption mgmt_if 'eth0'
\toption mgmt_mac '02:00:00:00:00:01'
"""

ETHTOOL_G = """#!/bin/sh
echo "ethtool $*" >> /tmp/stubs.log
if [ "$1" = "-g" ]; then
cat <<EOT
Ring parameters for $2:
Pre-set maximums:
RX:		1024
RX Mini:	n/a
TX:		1024
Current hardware settings:
RX:		256
TX:		256
EOT
fi
exit 0
"""


def setup(probe, eth0="02:00:00:00:00:01", eth1="02:00:00:00:00:02", conf=CONF):
    probe.write("/etc/config/kokabiel", conf)
    probe.fake_nic("eth0", eth0)
    probe.fake_nic("eth1", eth1)
    probe.stub("ethtool", ETHTOOL_G)


def test_capture_prep_applies_settings(probe):
    setup(probe)
    r = probe.run(PREP, env=ENV)
    assert r.rc == 0, r.err
    calls = probe.calls()
    for want in (
        "ip link set dev eth1 up promisc on",
        "ethtool -K eth1 gro off",
        "ethtool -K eth1 rxvlan off",
        "ethtool -G eth1 rx 1024",
        "sysctl -w net.ipv6.conf.eth1.disable_ipv6=1",
    ):
        assert want in calls
    assert not [c for c in calls if "eth0" in c.split()]


def test_capture_prep_refuses_swapped_macs(probe):
    setup(probe, eth0="02:00:00:00:00:02", eth1="02:00:00:00:00:01")
    r = probe.run(PREP, env=ENV)
    assert r.rc == 10
    assert probe.calls("ip") == []
    assert probe.calls("ethtool") == []
    assert probe.calls("sysctl") == []
    assert "kokabiel-flows stop" in probe.calls()
    assert any("MAC guard failed" in c for c in probe.calls("logger"))


def test_capture_prep_no_config(probe):
    probe.fake_nic("eth1", "02:00:00:00:00:02")
    r = probe.run(PREP, env=ENV)
    assert r.rc == 3
    assert probe.calls("ip") == []


def test_capture_prep_ethtool_unsupported_feature_continues(probe):
    setup(probe)
    probe.stub("ethtool", ETHTOOL_G.replace(
        'if [ "$1" = "-g" ]',
        '[ "$*" = "-K eth1 lro off" ] && exit 1\nif [ "$1" = "-g" ]'))
    r = probe.run(PREP, env=ENV)
    assert r.rc == 0, r.err
    calls = probe.calls()
    assert "ethtool -K eth1 lro off" in calls
    assert "ethtool -K eth1 rxvlan off" in calls
    assert "ethtool -G eth1 rx 1024" in calls


def test_hotplug_runs_only_for_capture_ifup(probe):
    probe.write("/tmp/libexec/capture-prep",
                '#!/bin/sh\necho "capture-prep-ran" >> /tmp/stubs.log\n')
    probe.run("chmod +x /tmp/libexec/capture-prep")
    env = {"KOKABIEL_LIBEXEC": "/tmp/libexec"}
    probe.run(f"ACTION=ifup INTERFACE=wan sh {HOTPLUG}; sleep 1", env=env)
    assert "capture-prep-ran" not in probe.calls()
    probe.run(f"ACTION=ifdown INTERFACE=capture sh {HOTPLUG}; sleep 1", env=env)
    assert "capture-prep-ran" not in probe.calls()
    probe.run(f"ACTION=ifup INTERFACE=capture sh {HOTPLUG}; sleep 1", env=env)
    assert "capture-prep-ran" in probe.calls()


def test_init_script_structure(probe):
    src = probe.read(INIT)
    assert src.startswith("#!/bin/sh /etc/rc.common")
    assert "START=19" in src
    assert "capture-prep" in src
    assert "KOKABIEL_LIBEXEC" in src


def assert_refused(probe, r):
    assert r.rc == 10, r.err
    assert probe.calls("ip") == []
    assert probe.calls("ethtool") == []
    assert probe.calls("sysctl") == []
    assert "kokabiel-flows stop" in probe.calls()
    assert any("MAC guard failed" in c for c in probe.calls("logger"))


def test_guard_capture_mismatch_only(probe):
    setup(probe, eth1="02:00:00:00:00:99")
    assert_refused(probe, probe.run(PREP, env=ENV))


def test_guard_mgmt_mismatch_only(probe):
    setup(probe, eth0="02:00:00:00:00:99")
    assert_refused(probe, probe.run(PREP, env=ENV))


def test_guard_capture_mac_unset(probe):
    setup(probe, conf=CONF.replace("\toption capture_mac '02:00:00:00:00:02'\n", ""))
    assert_refused(probe, probe.run(PREP, env=ENV))


def test_guard_mgmt_identity_unset(probe):
    conf = "".join(ln + "\n" for ln in CONF.splitlines() if "mgmt_" not in ln)
    setup(probe, conf=conf)
    r = probe.run(PREP, env=ENV)
    assert_refused(probe, r)
    assert "management identity not recorded" in r.err


def test_guard_capture_iface_missing(probe):
    setup(probe)
    probe.run("rm -rf /tmp/sys/class/net/eth1")
    assert_refused(probe, probe.run(PREP, env=ENV))
