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
    probe.run("mkdir -p /tmp/run/kokabiel-capture.lock; echo 1 > /tmp/run/kokabiel-capture.lock/pid")
    r = probe.run(f"{CAP} 192.0.2.50", env=ENV)
    assert r.rc == 75
    assert probe.run("test -d /tmp/run/kokabiel-capture.lock").rc == 0  # live owner: kept


def test_capture_stale_lock_is_reclaimed(probe):
    probe.run("mkdir -p /tmp/run /mnt/kokabiel-cap; rm -rf /tmp/run/kokabiel-capture.*")
    probe.write("/tmp/proc/meminfo", "MemAvailable: 4194304 kB\n")
    for setup in ("echo 999999 > /tmp/run/kokabiel-capture.lock/pid", "true",
                  "echo junk > /tmp/run/kokabiel-capture.lock/pid"):
        probe.run(f"rm -rf /tmp/run/kokabiel-capture.*; mkdir /tmp/run/kokabiel-capture.lock; {setup}")
        r = probe.run(f"{CAP} 192.0.2.50", env=ENV)
        assert r.rc == 0, (setup, r.err)
        assert probe.run("ls /tmp/run/kokabiel-capture.*").rc != 0


def test_capture_state_has_pid(probe):
    probe.stub("tcpdump", "cp /tmp/run/kokabiel-capture.state /tmp/state.seen")
    probe.stub("timeout", TIMEOUT_EXEC)
    assert cap(probe, "192.0.2.50").rc == 0
    st = probe.read("/tmp/state.seen")
    assert "target=192.0.2.50" in st and "pid=" in st and "dir=/tmp/kokabiel-captures" in st


def test_capture_term_stops_child_and_cleans_up(probe):
    probe.stub("tcpdump", "echo $$ > /tmp/tcpdump.pid\nexec sleep 60")
    probe.stub("timeout", TIMEOUT_EXEC)
    probe.run("mkdir -p /tmp/run; rm -rf /tmp/run/kokabiel-capture.* /tmp/tcpdump.pid")
    probe.write("/tmp/proc/meminfo", "MemAvailable: 4194304 kB\n")
    envs = " ".join(f"{k}={v}" for k, v in ENV.items())
    r = probe.run(
        f"env {envs} {CAP} 192.0.2.50 30 >/dev/null 2>&1 & p=$!; "
        "i=0; while [ ! -s /tmp/tcpdump.pid ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done; "
        "t=$(date +%s); kill -TERM $p; wait $p; echo rc=$?; echo dt=$(( $(date +%s) - t )); "
        "kill -0 $(cat /tmp/tcpdump.pid) 2>/dev/null && echo alive || echo gone; "
        "ls /tmp/run/kokabiel-capture.* 2>/dev/null | wc -l")
    out = r.out.split()
    assert "gone" in out and out[-1] == "0", r.out
    assert int(next(x for x in out if x.startswith("dt=")).split("=")[1]) < 3


def test_capture_rejects_leading_zero_numbers(probe):
    assert cap(probe, "192.0.2.50 --max-mb 0100").rc == 2
    assert cap(probe, "192.0.2.50 08").rc == 2
    assert probe.calls("timeout") == []


def sd_cap(probe, args, free_mb=100000, files=None, env=None):
    probe.run("mkdir -p /tmp/run /mnt/kokabiel-cap /tmp/sdcap; rm -rf /tmp/run/kokabiel-capture.*")
    probe.write("/tmp/proc/meminfo", "MemAvailable: 100 kB\n")  # RAM must be irrelevant
    probe.write("/tmp/proc/mounts", "rootfs / rootfs rw 0 0\n"
                "/dev/mmcblk1p1 /tmp/sdcap ext4 rw 0 0\n")
    probe.stub("df", f'echo "df $*" >> /tmp/stubs.log\n'
               f'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
               f'echo "/dev/x 9 9 {free_mb * 1024} 1% /tmp/sdcap"')
    if files:
        probe.stub("tcpdump", 'echo "tcpdump $*" >> /tmp/stubs.log\n'
                   'while [ $# -gt 0 ]; do [ "$1" = -w ] && w=$2; shift; done\n'
                   + "".join(f'head -c {n} /dev/zero > "${{w}}{sfx}"\n' for sfx, n in files)
                   + 'echo "7 packets captured" >&2')
        probe.stub("timeout", TIMEOUT_EXEC)
    return probe.run(f"{CAP} {args}", env={**ENV, "CAPTURE_DIR": "/tmp/sdcap", **(env or {})})


def test_capture_sd_storage(probe):
    r = sd_cap(probe, "192.0.2.50")
    assert r.rc == 0, r.err
    c = probe.calls("timeout")[0]
    assert "-w /tmp/sdcap/captures/probe-a-192-0-2-50-" in c and " -C 819 -W 5 " in c
    assert json.loads(r.out.strip().splitlines()[-1])["storage"] == "sd"


def test_capture_sd_low_space_refused(probe):
    r = sd_cap(probe, "192.0.2.50", free_mb=4096 + 255)
    assert r.rc == 4
    assert probe.calls("timeout") == []
    assert sd_cap(probe, "192.0.2.50", free_mb=4096 + 256).rc == 0


def test_capture_sd_rotation_files_summed(probe):
    r = sd_cap(probe, "192.0.2.50", files=[("", 1000), ("1", 500), ("2", 200)])
    assert r.rc == 0, r.err
    d = json.loads(r.out.strip().splitlines()[-1])
    assert len(d["files"]) == 3 and d["bytes"] == 1700 and d["packets"] == 7


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
