"""Test harness: one session-scoped OpenWrt container, a per-test `probe` fixture."""
import hashlib
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TESTS = REPO / "tests"
IMAGE_REPO = "kokabiel-test"

STUB_COMMANDS = (
    "ip ethtool logger apk opkg softflowd softflowctl tcpdump block blkid "
    "mkfs.ext4 sfdisk dd mount umount sysctl netstat timeout hostname"
).split()

# Services named in the spec's discovery list, plus the ones Kokabiel owns.
INIT_SERVICES = (
    "ttyd vsftpd samba4 wsdd2 minidlna aria2 miniupnpd adblock smartdns ddns sqm "
    "nlbwmon radius avahi avahi-daemon collectd luci_statistics watchcat hd-idle fa-fancontrol "
    "uhttpd dropbear firewall network log cron sysntpd dnsmasq rpcd blockd fstab "
    "triggerhappy packet_steering urngd odhcpd "
    "softflowd kokabiel kokabiel-flows prometheus-node-exporter-lua"
).split()

ENV = {
    "KOKABIEL_SYSFS": "/tmp/sys",
    "KOKABIEL_INITD": "/tmp/initd",
    "KOKABIEL_PROC": "/tmp/proc",
}


@dataclass
class Result:
    rc: int
    out: str
    err: str


def _docker(*args, stdin=None, check=True):
    p = subprocess.run(
        ["docker", *args], input=stdin, capture_output=True, text=True
    )
    if check and p.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed: {p.stderr}")
    return p


def _image_tag():
    digest = hashlib.sha256((TESTS / "Dockerfile").read_bytes()).hexdigest()[:12]
    return f"{IMAGE_REPO}:{digest}"


def _default_stub(name):
    return f'#!/bin/sh\necho "{name} $*" >> /tmp/stubs.log\nexit 0\n'


def _reset_script():
    parts = [
        "rm -rf /tmp/sys /tmp/proc /tmp/stubs /tmp/initd /etc/kokabiel "
        "/usr/libexec/kokabiel /tmp/stubs.log /tmp/root",
        "mkdir -p /tmp/sys /tmp/proc /tmp/stubs /tmp/initd /etc/kokabiel "
        "/usr/libexec/kokabiel",
        ": > /tmp/stubs.log",
        "rm -f /etc/config/*",
        "cp /work/tests/fixtures/etc-config/* /etc/config/",
    ]
    for name in STUB_COMMANDS:
        parts.append(
            f"printf '%s' {shlex.quote(_default_stub(name))} > /tmp/stubs/{name}"
            f" && chmod +x /tmp/stubs/{name}"
        )
    for svc in INIT_SERVICES:
        body = f'#!/bin/sh\necho "{svc} $*" >> /tmp/stubs.log\nexit 0\n'
        parts.append(
            f"printf '%s' {shlex.quote(body)} > /tmp/initd/{svc}"
            f" && chmod +x /tmp/initd/{svc}"
        )
    return "\n".join(parts)


class Probe:
    def __init__(self, container):
        self.container = container

    def run(self, cmd, env=None, stdin=None):
        full = {**ENV, **(env or {})}
        args = ["exec", "-i"]
        for k, v in full.items():
            args += ["-e", f"{k}={v}"]
        script = f"PATH=/tmp/stubs:$PATH; export PATH; {cmd}"
        p = _docker(
            *args, self.container, "sh", "-c", script,
            stdin=stdin if stdin is not None else "", check=False,
        )
        return Result(p.returncode, p.stdout, p.stderr)

    def _sh(self, script, stdin=None):
        p = _docker("exec", "-i", self.container, "sh", "-c", script,
                    stdin=stdin if stdin is not None else "")
        return p.stdout

    def calls(self, cmd=None):
        lines = self._sh("cat /tmp/stubs.log").splitlines()
        if cmd is None:
            return lines
        return [ln for ln in lines if ln == cmd or ln.startswith(cmd + " ")]

    def stub(self, name, script):
        if not script.startswith("#!"):
            script = "#!/bin/sh\n" + script
        if not script.endswith("\n"):
            script += "\n"
        self._sh(f"cat > /tmp/stubs/{name} && chmod +x /tmp/stubs/{name}", stdin=script)

    def write(self, path, content):
        q = shlex.quote(path)
        self._sh(f'mkdir -p "$(dirname {q})" && cat > {q}', stdin=content)

    def read(self, path):
        return self._sh(f"cat {shlex.quote(path)}")

    def uci(self, expr):
        return self._sh(f"uci -q get {expr} || true").strip()

    def fake_nic(self, name, mac, carrier=1, flags="0x1003", rx_dropped=0,
                 rx_missed_errors=0, carrier_changes=0, rx_packets=0, rx_bytes=0):
        base = f"/tmp/sys/class/net/{name}"
        files = {
            "address": mac, "carrier": carrier, "flags": flags,
            "carrier_changes": carrier_changes,
            "statistics/rx_dropped": rx_dropped,
            "statistics/rx_missed_errors": rx_missed_errors,
            "statistics/rx_packets": rx_packets,
            "statistics/rx_bytes": rx_bytes,
        }
        cmds = [f"mkdir -p {base}/statistics"]
        cmds += [f"echo {shlex.quote(str(v))} > {base}/{k}" for k, v in files.items()]
        self._sh("\n".join(cmds))

    def fake_mmc(self, name, type, size_sectors, serial="0x00000001", partitions=()):
        base = f"/tmp/sys/block/{name}"
        cmds = [f"mkdir -p {base}/device"]
        cmds += [
            f"echo {size_sectors} > {base}/size",
            f"echo {shlex.quote(type)} > {base}/device/type",
            f"echo {shlex.quote(serial)} > {base}/device/serial",
            f"echo {shlex.quote(name)} > {base}/device/name",
        ]
        # device numbers: major 179, minor = 8 * disk index + partition number
        disk = int(name.removeprefix("mmcblk"))
        cmds.append(f"echo 179:{disk * 8} > {base}/dev")
        for p in partitions:
            minor = disk * 8 + int(p.rsplit("p", 1)[1])
            cmds.append(f"mkdir -p {base}/{shlex.quote(p)}")
            cmds.append(f"echo 179:{minor} > {base}/{shlex.quote(p)}/dev")
        self._sh("\n".join(cmds))

    def mountinfo(self, *lines):
        """Write /tmp/proc/self/mountinfo (the guard fails closed without it)."""
        self.write("/tmp/proc/self/mountinfo", "".join(ln + "\n" for ln in lines))

    def reset(self):
        self._sh(_reset_script())


@pytest.fixture(scope="session")
def _container():
    tag = _image_tag()
    if _docker("image", "inspect", tag, check=False).returncode != 0:
        _docker("build", "-t", tag, "-f", str(TESTS / "Dockerfile"), str(TESTS))
    cid = _docker(
        "run", "-d", "--rm", "-v", f"{REPO}:/work:ro", tag, "tail", "-f", "/dev/null"
    ).stdout.strip()
    try:
        yield cid
    finally:
        _docker("rm", "-f", cid, check=False)


@pytest.fixture
def probe(_container):
    p = Probe(_container)
    p.reset()
    return p
