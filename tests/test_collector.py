"""Prometheus collector (Lua), run through tests/lua/harness.lua."""
import re

from conftest import REPO

HARNESS = "/work/tests/lua/harness.lua"
COLLECTOR = "/work/scripts/files/prometheus-collectors/kokabiel.lua"
FIXTURE = (REPO / "tests/fixtures/softflowctl-statistics.txt").read_text()
UCI = """config kokabiel 'main'
\toption capture_if 'eth1'
\toption capture_mac '02:00:00:00:00:02'
\toption mgmt_if 'eth0'
\toption mgmt_mac '02:00:00:00:00:01'
\toption capture_dev_path '0001:11:00.0'
\toption mgmt_dev_path '0002:21:00.0'
\toption capture_dir '/tmp/capdir'
"""
# The image's BusyBox has no timeout applet and the default stub does not exec
# its command, so tests install a working one.
TIMEOUT_STUB = """n=$1; shift
"$@" & p=$!
( sleep "$n"; kill $p ) >/dev/null 2>&1 &
w=$!
wait $p; rc=$?
kill $w 2>/dev/null
[ $rc -ge 128 ] && exit 124
exit $rc"""
ENV = {
    "KOKABIEL_CTL_TIMEOUT": "1",
    "KOKABIEL_SOFTFLOWCTL": "/tmp/stubs/softflowctl",
    "KOKABIEL_RUN": "/tmp/run",
    "KOKABIEL_OVERLAY": "/tmp",
}


def fixture_num(label):
    return int(re.search(rf"^{label}:?\s*(\d+)", FIXTURE, re.M).group(1))


def setup(probe, flags="0x1103", mac1="02:00:00:00:00:01", mac2="02:00:00:00:00:02",
          dev1="auto", dev2="auto"):
    probe.write("/etc/config/kokabiel", UCI)
    probe.fake_nic("eth0", mac1, device=dev1)
    probe.fake_nic("eth1", mac2, flags=flags, rx_packets=11, rx_bytes=222,
                   rx_dropped=3, rx_missed_errors=4, carrier_changes=5, device=dev2)
    probe.write("/tmp/fixture.txt", FIXTURE)
    probe.stub("softflowctl", "cat /tmp/fixture.txt")
    probe.stub("timeout", TIMEOUT_STUB)
    probe.run("mkdir -p /tmp/run")


def collect(probe, env=None):
    r = probe.run(f"lua {HARNESS} {COLLECTOR}", env={**ENV, **(env or {})})
    assert r.rc == 0, r.err
    out = {}
    for line in r.out.splitlines():
        name, val = line.rsplit(" ", 1)
        out[name] = float(val)
    return out


def test_collector_link_and_guard(probe):
    setup(probe)
    m = collect(probe)
    assert m["kokabiel_capture_link_up"] == 1
    assert m["kokabiel_capture_promisc"] == 1
    assert m["kokabiel_interface_guard_ok"] == 1
    assert m["kokabiel_capture_rx_packets_total"] == 11
    assert m["kokabiel_capture_rx_bytes_total"] == 222
    assert m["kokabiel_capture_rx_dropped_total"] == 3
    assert m["kokabiel_capture_rx_missed_errors_total"] == 4
    assert m["kokabiel_capture_carrier_changes_total"] == 5


def test_collector_promisc_off(probe):
    setup(probe, flags="0x1003")
    assert collect(probe)["kokabiel_capture_promisc"] == 0


def test_collector_guard_pci_swap_with_matching_macs(probe):
    setup(probe, dev1="0001:11:00.0", dev2="0002:21:00.0")
    assert collect(probe)["kokabiel_interface_guard_ok"] == 0


def test_collector_guard_ignores_mac_change(probe):
    setup(probe, mac1="02:00:00:00:00:02", mac2="02:00:00:00:00:01")
    assert collect(probe)["kokabiel_interface_guard_ok"] == 1


def test_collector_guard_fail_closed_when_unreadable(probe):
    setup(probe, dev2=None)
    assert collect(probe)["kokabiel_interface_guard_ok"] == 0


def test_collector_softflowd_stats(probe):
    setup(probe)
    m = collect(probe)
    assert m["kokabiel_softflowd_up"] == 1
    assert fixture_num("Packets processed") > 0
    assert m["kokabiel_softflowd_active_flows"] == fixture_num("Number of active flows")
    assert m["kokabiel_softflowd_flows_exported_total"] == fixture_num("Flows exported")
    assert m["kokabiel_softflowd_packets_received_total"] == fixture_num("Packets received by libpcap")
    assert m["kokabiel_softflowd_packets_processed_total"] == fixture_num("Packets processed")
    assert m["kokabiel_softflowd_pcap_dropped_total"] == fixture_num("Packets dropped by libpcap")


def test_collector_softflowd_down(probe):
    setup(probe)
    probe.stub("softflowctl", "exit 1")
    m = collect(probe)
    assert m["kokabiel_softflowd_up"] == 0
    assert "kokabiel_softflowd_active_flows" not in m


def test_collector_storage_and_temperature(probe):
    setup(probe)
    probe.write("/tmp/sys/class/thermal/thermal_zone0/type", "soc-thermal\n")
    probe.write("/tmp/sys/class/thermal/thermal_zone0/temp", "47500\n")
    m = collect(probe)
    assert m['kokabiel_temperature_celsius{zone="soc-thermal"}'] == 47.5
    assert m['kokabiel_storage_size_bytes{mount="overlay"}'] > 0
    assert m['kokabiel_storage_free_bytes{mount="overlay"}'] > 0


def test_collector_sd_absent(probe):
    setup(probe)
    m = collect(probe)
    assert m["kokabiel_sd_present"] == 0
    assert not [k for k in m if 'mount="sd"' in k]


def test_collector_sd_present(probe):
    setup(probe)
    probe.write("/tmp/proc/mounts", "/dev/mmcblk0p1 /tmp/capdir ext4 rw 0 0\n")
    probe.run("mkdir -p /tmp/capdir")
    m = collect(probe)
    assert m["kokabiel_sd_present"] == 1
    assert m['kokabiel_storage_size_bytes{mount="sd"}'] > 0


def test_collector_capture_running(probe):
    setup(probe)
    probe.run("mkdir -p /tmp/capdir /tmp/proc/4242")
    probe.write("/tmp/run/kokabiel-capture.state",
                "target=192.0.2.9\nstarted=1700000000\ndir=/tmp/capdir\npid=4242\n")
    probe.run("head -c 2048 /dev/zero > /tmp/capdir/probe-192-0-2-9-20231114T221400Z.pcap0;"
              "head -c 999 /dev/zero > /tmp/capdir/probe-192-0-2-9-20200101T000000Z.pcap")
    m = collect(probe)
    assert m["kokabiel_capture_running"] == 1
    assert m["kokabiel_capture_bytes"] == 2048
    assert m['kokabiel_capture_info{target="192.0.2.9"}'] == 1


def test_collector_capture_stale_state_not_running(probe):
    setup(probe)
    probe.write("/tmp/run/kokabiel-capture.state",
                "target=192.0.2.9\nstarted=1700000000\ndir=/tmp/capdir\npid=4242\n")
    m = collect(probe)
    assert m["kokabiel_capture_running"] == 0
    assert not [k for k in m if k.startswith("kokabiel_capture_info")]


def test_collector_unexpected_listeners(probe):
    setup(probe)
    probe.stub("netstat", "cat <<'EOF'\n"
               "tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN\n"
               "tcp        0      0 192.0.2.20:9100         0.0.0.0:*               LISTEN\n"
               "tcp        0      0 127.0.0.1:53            0.0.0.0:*               LISTEN\n"
               "tcp        0      0 :::7681                 :::*                    LISTEN\n"
               "udp        0      0 127.0.0.1:53            0.0.0.0:*\n"
               "udp        0      0 0.0.0.0:5353            0.0.0.0:*\n"
               "udp        0      0 :::161                  :::*\n"
               "EOF")
    assert collect(probe)["kokabiel_unexpected_listeners"] == 3


def test_collector_softflowd_hang_times_out(probe):
    import time
    setup(probe)
    probe.stub("softflowctl", "exec sleep 30")
    t = time.time()
    m = collect(probe)
    assert time.time() - t < 10
    assert m["kokabiel_softflowd_up"] == 0


def test_collector_guard_fail_closed_when_unrecorded(probe):
    setup(probe)
    probe.write("/etc/config/kokabiel", UCI.replace("\toption mgmt_dev_path '0002:21:00.0'\n", ""))
    assert collect(probe)["kokabiel_interface_guard_ok"] == 0


def test_collector_capture_bytes_stamp_slack(probe):
    setup(probe)
    probe.run("mkdir -p /tmp/capdir /tmp/proc/4242")
    probe.write("/tmp/run/kokabiel-capture.state",
                "target=192.0.2.9\nstarted=1700000000\ndir=/tmp/capdir\npid=4242\n")
    probe.run("rm -f /tmp/capdir/*")
    # started = 20231114T221320Z: one second earlier counts, three seconds earlier does not
    probe.run("head -c 100 /dev/zero > /tmp/capdir/probe-192-0-2-9-20231114T221319Z.pcap;"
              "head -c 7 /dev/zero > /tmp/capdir/probe-192-0-2-9-20231114T221317Z.pcap")
    assert collect(probe)["kokabiel_capture_bytes"] == 100


def test_collector_dhcp_client_udp_allowed(probe):
    setup(probe)
    probe.stub("netstat", "cat <<'EOF'\n"
               "tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN\n"
               "udp        0      0 0.0.0.0:68              0.0.0.0:*\n"
               "udp        0      0 :::546                  :::*\n"
               "EOF")
    assert collect(probe)["kokabiel_unexpected_listeners"] == 0
