import json

CAP = "/work/scripts/capture.sh"
ENV = {"KOKABIEL_LIB": "/work/scripts/lib", "KOKABIEL_RUN": "/tmp/run",
       "CAPTURE_DIR": "/mnt/kokabiel-cap", "CAPTURE_IF": "eth1",
       "CAPTURE_RAM_MAX_MB": "100", "CAPTURE_SD_MAX_MB": "4096",
       "PROBE_NAME": "probe-a"}

TCPDUMP_OK = """#!/bin/sh
echo "tcpdump $*" >> /tmp/stubs.log
while [ $# -gt 0 ]; do
  if [ "$1" = -w ]; then head -c 1000 /dev/zero > "$2"; fi
  shift
done
echo "42 packets captured" >&2
exit 0
"""
TIMEOUT_EXEC = """#!/bin/sh
echo "timeout $*" >> /tmp/stubs.log
shift
exec "$@"
"""


def cap(probe, args, env=None, mem_mb=4096):
    probe.run("mkdir -p /tmp/run /mnt/kokabiel-cap; rm -rf /tmp/run/kokabiel-capture.*")
    probe.write("/tmp/proc/meminfo", f"MemTotal: 8000000 kB\nMemAvailable: {mem_mb * 1024} kB\n")
    probe.write("/tmp/proc/mounts", "rootfs / rootfs rw 0 0\n")
    return probe.run(f"{CAP} {args}", env={**ENV, **(env or {})})


def test_capture_rejects_injection(probe):
    for t in ("'1.2.3.4; reboot'", "'$(id)'", "not-an-ip", "300.1.1.1", "1.2.3",
              "'02:00:00:00:00'", "'1::2::3'", "'zz:zz::1'", "''"):
        r = cap(probe, t)
        assert r.rc == 2, (t, r.rc, r.err)
    assert probe.calls("tcpdump") == [] and probe.calls("timeout") == []


def test_capture_ip_filter_vlan_aware(probe):
    r = cap(probe, "192.0.2.50 30")
    assert r.rc == 0, r.err
    c = probe.calls("timeout")[0]
    assert c.startswith("timeout 30 tcpdump -i eth1 -s 0 -U -w /tmp/kokabiel-captures/probe-a-192-0-2-50-")
    assert c.endswith(" -C 20 -W 5 host 192.0.2.50 or ( vlan and host 192.0.2.50 )")


def test_capture_ipv6_and_mac_filter(probe):
    assert cap(probe, "2001:db8::1").rc == 0
    assert probe.calls("timeout")[0].endswith("host 2001:db8::1 or ( vlan and host 2001:db8::1 )")
    probe.run("rm -f /tmp/stubs.log")
    assert cap(probe, "02:00:00:aa:bb:cc").rc == 0
    assert probe.calls("timeout")[0].endswith(
        "ether host 02:00:00:aa:bb:cc or ( vlan and ether host 02:00:00:aa:bb:cc )")


def test_capture_unmounted_dir_falls_back_to_ram(probe):
    r = cap(probe, "192.0.2.50")
    assert r.rc == 0, r.err
    assert "-w /tmp/kokabiel-captures/" in probe.calls("timeout")[0]
    assert json.loads(r.out.strip().splitlines()[-1])["storage"] == "ram"


def test_capture_ram_headroom_refusal(probe):
    r = cap(probe, "192.0.2.50", mem_mb=500)
    assert r.rc == 4
    assert probe.calls("tcpdump") == [] and probe.calls("timeout") == []


def test_capture_ram_cap_out_of_range(probe):
    assert cap(probe, "192.0.2.50 --max-mb 600").rc == 2
    assert cap(probe, "192.0.2.50 --max-mb 50").rc == 2
    assert probe.calls("timeout") == []


def test_capture_bad_seconds(probe):
    for s in ("0", "3601", "abc"):
        assert cap(probe, f"192.0.2.50 {s}").rc == 2


def test_capture_lock_busy(probe):
    probe.run("mkdir -p /tmp/run /mnt/kokabiel-cap")
    probe.write("/tmp/proc/meminfo", "MemAvailable: 4194304 kB\n")
    probe.run("mkdir -p /tmp/run/kokabiel-capture.lock")
    r = probe.run(f"{CAP} 192.0.2.50", env=ENV)
    assert r.rc == 75
    assert probe.run("test -d /tmp/run/kokabiel-capture.lock").rc == 0  # not ours: kept


def test_capture_json_summary(probe):
    probe.stub("tcpdump", TCPDUMP_OK)
    probe.stub("timeout", TIMEOUT_EXEC)
    r = cap(probe, "192.0.2.50 5")
    assert r.rc == 0, r.err
    d = json.loads(r.out.strip().splitlines()[-1])
    assert d["packets"] == 42 and d["bytes"] == 1000 and d["storage"] == "ram"
    assert d["target"] == "192.0.2.50" and d["exit"] == "timeout" and len(d["files"]) == 1
    assert probe.run("ls /tmp/run/kokabiel-capture.*").rc != 0


def test_capture_tcpdump_failure_exit_5(probe):
    probe.stub("timeout", "exit 1")
    r = cap(probe, "192.0.2.50")
    assert r.rc == 5
    assert json.loads(r.out.strip().splitlines()[-1])["exit"] == "error"
    assert probe.run("ls /tmp/run/kokabiel-capture.*").rc != 0
