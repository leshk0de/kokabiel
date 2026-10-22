"""libpcap immediate-mode shim: packaging, install and the real softflowd effect."""
import re
import subprocess

import pytest

from conftest import REPO, _image_tag
from test_setup import ENV

INIT = REPO / "scripts/files/init.d/kokabiel-flows"
SO = REPO / "build/pcap_immediate.so"
DST = "/tmp/root/usr/lib/kokabiel/pcap_immediate.so"


def elf_header(machine):
    """printf-able 20-byte ELF64 LE ET_DYN header with the given e_machine bytes."""
    return "\\177ELF\\002\\001\\001" + "\\000" * 9 + "\\003\\000" + machine


def stage_scripts(probe, with_shim, where="build"):
    """Writable copy of scripts/ so setup's ../build lookup can be controlled."""
    probe.run("rm -rf /tmp/stage && mkdir -p /tmp/stage/build && cp -r /work/scripts /tmp/stage/")
    dst = {"build": "/tmp/stage/build", "files": "/tmp/stage/scripts/files"}[where]
    if with_shim == "real":
        probe.run(f"cp /work/build/pcap_immediate.so {dst}/")
    elif with_shim:
        probe.run(f"printf '{with_shim}' > {dst}/pcap_immediate.so")


def run_staged_setup(probe):
    probe.write("/tmp/site.env", "COLLECTOR_HOST=192.0.2.10\n")
    probe.fake_nic("eth0", "02:00:00:00:00:01")
    probe.fake_nic("eth1", "02:00:00:00:00:02")
    return probe.run("/tmp/stage/scripts/setup.sh --env /tmp/site.env --skip-packages",
                     env={**ENV, "KOKABIEL_LIB": "/tmp/stage/scripts/lib"})


def test_init_sets_ld_preload_when_shim_present():
    text = INIT.read_text()
    assert re.search(r'\[ -f "?\$KK_SHIM"? \]', text)
    assert "procd_set_param env LD_PRELOAD=$KK_SHIM" in text or \
        'procd_set_param env LD_PRELOAD="$KK_SHIM"' in text
    assert "/usr/lib/kokabiel/pcap_immediate.so" in (REPO / "scripts/lib/flows.sh").read_text()


@pytest.mark.skipif(not SO.exists(), reason="build/pcap_immediate.so not built; run 'make shim'")
@pytest.mark.parametrize("where", ["build", "files"])
def test_setup_installs_shim_when_built(probe, where):
    stage_scripts(probe, "real", where)
    r = run_staged_setup(probe)
    assert r.rc == 0, r.err
    assert probe.run(f"ls -l {DST}").out.startswith("-rw-r--r--")
    assert probe.run(f"cmp {DST} /work/build/pcap_immediate.so").rc == 0
    assert "/usr/lib/kokabiel/" in probe.read("/tmp/root/etc/sysupgrade.conf").split()


def test_setup_warns_without_shim(probe):
    stage_scripts(probe, False)
    r = run_staged_setup(probe)
    assert r.rc == 0, r.err
    assert "softflowd will not process packets without the immediate-mode shim; run 'make shim'" in r.err
    assert probe.run(f"test -e {DST}").rc != 0


def _processed(preload):
    pre = f"LD_PRELOAD={preload} " if preload else ""
    script = (
        f"{pre}/usr/sbin/softflowd -d -i lo -n 127.0.0.1:2055 -v 10 -T full -c /tmp/ctl & "
        "sleep 1; ping -c 5 127.0.0.1 >/dev/null; sleep 1; "
        "/usr/sbin/softflowctl -c /tmp/ctl statistics; kill %1"
    )
    p = subprocess.run(
        ["docker", "run", "--rm", "--cap-add", "NET_RAW", "--cap-add", "NET_ADMIN",
         "-v", f"{REPO}:/work:ro", _image_tag(), "sh", "-c", script],
        capture_output=True, text=True, timeout=60)
    m = re.search(r"Packets processed:\s*(\d+)", p.stdout)
    assert m, p.stdout + p.stderr
    return int(m.group(1))


@pytest.mark.skipif(not SO.exists(), reason="build/pcap_immediate.so not built; run 'make shim'")
def test_shim_makes_softflowd_process_packets(_container):
    assert _processed(None) == 0  # documents the stock softflowd 1.1.0 bug
    assert _processed("/work/build/pcap_immediate.so") > 0


BAD = "pcap_immediate.so is not an aarch64 shared object; not installed"


@pytest.mark.parametrize("content", ["not an elf at all", None])
def test_setup_rejects_bad_shim(probe, content):
    content = content or elf_header("\\076\\000")  # x86-64
    stage_scripts(probe, content)
    probe.run(f"mkdir -p {DST.rsplit('/', 1)[0]} && echo old > {DST}")
    r = run_staged_setup(probe)
    assert r.rc == 0, r.err
    assert BAD in r.err
    assert probe.run(f"test -e {DST}").rc != 0  # stale shim removed too
    assert any("daemon.err" in c and BAD in c for c in probe.calls("logger"))


def test_setup_accepts_aarch64_header(probe):
    stage_scripts(probe, elf_header("\\267\\000"))
    r = run_staged_setup(probe)
    assert r.rc == 0, r.err
    assert BAD not in r.err
    assert probe.run(f"test -f {DST}").rc == 0
