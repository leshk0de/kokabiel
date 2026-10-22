"""kokabiel-status: OK/FAIL/INFO lines and exit code."""
from conftest import REPO
from test_collector import UCI, setup

STATUS = "/work/scripts/status.sh"
ENV = {
    "KOKABIEL_LIB": "/work/scripts/lib",
    "KOKABIEL_SOFTFLOWCTL": "/tmp/stubs/softflowctl",
    "KOKABIEL_SHIM": "/tmp/shim.so",
    "KOKABIEL_RUN": "/tmp/run",
    "KOKABIEL_CTL_TIMEOUT": "1",
}
NETSTAT_OK = (
    'echo "netstat $*" >> /tmp/stubs.log\n'
    "cat <<'EOF'\n"
    "Active Internet connections (only servers)\n"
    "Proto Recv-Q Send-Q Local Address           Foreign Address         State\n"
    "tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN\n"
    "tcp        0      0 192.0.2.20:9100         0.0.0.0:*               LISTEN\n"
    "tcp        0      0 127.0.0.1:53            0.0.0.0:*               LISTEN\n"
    "tcp        0      0 :::22                   :::*                    LISTEN\n"
    "udp        0      0 127.0.0.1:53            0.0.0.0:*\n"
    "udp        0      0 ::1:53                  :::*\n"
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
    for chk in ("capture_link", "capture_promisc", "capture_no_address", "nic_guard",
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


def test_status_nic_swap_with_matching_macs(probe):
    r = status(probe, dev1="0001:11:00.0", dev2="0002:21:00.0")()
    assert r.rc == 1 and "FAIL nic_guard" in r.out
    assert "--rerecord-nics" in r.out or "swap" in r.out


def test_status_nic_guard_ignores_mac_change(probe):
    r = status(probe, mac1="02:00:00:00:00:02", mac2="02:00:00:00:00:01")()
    assert "OK nic_guard" in r.out, r.out


def test_status_nic_path_unreadable(probe):
    r = status(probe, dev2=None)()
    assert r.rc == 1 and "FAIL nic_guard" in r.out


def test_status_nic_path_unrecorded(probe):
    run = status(probe)
    probe.run("uci delete kokabiel.main.capture_dev_path; uci commit kokabiel")
    r = run()
    assert r.rc == 1 and "FAIL nic_guard" in r.out and "not recorded" in r.out


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


def test_status_softflowd_hang_fails_quickly(probe):
    import time
    run = status(probe)
    probe.stub("softflowctl", "exec sleep 30")
    t = time.time()
    r = run()
    assert time.time() - t < 10
    assert r.rc == 1 and "FAIL softflowd_running" in r.out


def test_status_unexpected_udp_listener(probe):
    run = status(probe)
    probe.stub("netstat", NETSTAT_OK.replace(
        "udp        0      0 127.0.0.1:53",
        "udp        0      0 0.0.0.0:5353            0.0.0.0:*\n"
        "udp        0      0 127.0.0.1:53"))
    r = run()
    assert r.rc == 1
    assert "FAIL listeners" in r.out and "udp/5353" in r.out


def test_status_listeners_queries_udp(probe):
    run = status(probe)
    run()
    assert "netstat -uln" in probe.calls("netstat")


def test_status_dhcp_client_udp_allowed(probe):
    run = status(probe)
    probe.stub("netstat", NETSTAT_OK.replace(
        "udp        0      0 127.0.0.1:53",
        "udp        0      0 0.0.0.0:68              0.0.0.0:*\n"
        "udp        0      0 :::546                  :::*\n"
        "udp        0      0 127.0.0.1:53"))
    r = run()
    assert "OK listeners" in r.out, r.out
