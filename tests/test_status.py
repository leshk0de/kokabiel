"""kokabiel-status: OK/FAIL/INFO lines and exit code."""
from conftest import REPO
from test_collector import UCI, setup

STATUS = "/work/scripts/status.sh"
ENV = {
    "KOKABIEL_LIB": "/work/scripts/lib",
    "KOKABIEL_SOFTFLOWCTL": "/tmp/stubs/softflowctl",
    "KOKABIEL_SHIM": "/tmp/shim.so",
    "KOKABIEL_RUN": "/tmp/run",
}
NETSTAT_OK = (
    "cat <<'EOF'\n"
    "Active Internet connections (only servers)\n"
    "Proto Recv-Q Send-Q Local Address           Foreign Address         State\n"
    "tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN\n"
    "tcp        0      0 192.0.2.20:9100         0.0.0.0:*               LISTEN\n"
    "tcp        0      0 127.0.0.1:53            0.0.0.0:*               LISTEN\n"
    "tcp        0      0 :::22                   :::*                    LISTEN\n"
    "EOF"
)


def status(probe, **kw):
    setup(probe, **kw)
    probe.write("/tmp/shim.so", "x")
    probe.write("/etc/config/kokabiel", UCI + "\toption collector_host '192.0.2.10'\n"
                "\toption collector_port '2055'\n")
    probe.stub("netstat", NETSTAT_OK)
    return lambda: probe.run(STATUS, env=ENV)


def test_status_all_ok_exit0(probe):
    r = status(probe)()
    assert r.rc == 0, r.out + r.err
    for chk in ("capture_link", "capture_promisc", "capture_no_address", "mac_guard",
                "flows_shim", "softflowd_running", "listeners"):
        assert f"OK {chk}" in r.out, r.out
    assert "FAIL" not in r.out
    assert "INFO collector 192.0.2.10:2055" in r.out
    assert "INFO sd absent" in r.out
    assert "INFO rx packets=11 dropped=3 missed=4" in r.out
    assert "INFO pcap_dropped " in r.out


def test_status_promisc_off_exit1(probe):
    r = status(probe, flags="0x1003")()
    assert r.rc == 1
    assert "FAIL capture_promisc" in r.out


def test_status_link_down_exit1(probe):
    run = status(probe)
    probe.run("echo 0 > /tmp/sys/class/net/eth1/carrier")
    r = run()
    assert r.rc == 1 and "FAIL capture_link" in r.out


def test_status_capture_has_address(probe):
    run = status(probe)
    probe.stub("ip", 'case "$*" in *"addr show dev eth1"*) echo "3: eth1 inet 192.0.2.99/24 scope global eth1";; esac')
    r = run()
    assert r.rc == 1 and "FAIL capture_no_address" in r.out


def test_status_mac_mismatch(probe):
    r = status(probe, mac1="02:00:00:00:00:02", mac2="02:00:00:00:00:01")()
    assert r.rc == 1 and "FAIL mac_guard" in r.out


def test_status_shim_missing(probe):
    run = status(probe)
    probe.run("rm -f /tmp/shim.so")
    r = run()
    assert r.rc == 1 and "FAIL flows_shim" in r.out


def test_status_softflowd_down(probe):
    run = status(probe)
    probe.stub("softflowctl", "exit 1")
    r = run()
    assert r.rc == 1 and "FAIL softflowd_running" in r.out


def test_status_unexpected_listener(probe):
    run = status(probe)
    probe.stub("netstat", NETSTAT_OK.replace(
        "tcp        0      0 127.0.0.1:53",
        "tcp        0      0 0.0.0.0:7681            0.0.0.0:*               LISTEN\n"
        "tcp        0      0 127.0.0.1:53"))
    r = run()
    assert r.rc == 1
    assert "FAIL listeners" in r.out and "7681" in r.out
