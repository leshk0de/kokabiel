"""The repo is public: no private addresses in tracked files."""
import ipaddress
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWED_IPS = {"192.168.2.1", "127.0.0.1"}
ALLOWED_NETS = [ipaddress.ip_network("192.168.2.0/24")]
RFC1918 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
MAC = re.compile(r"(?<![0-9a-fA-F:])(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}(?![0-9a-fA-F:])")


def _files():
    # Tracked files plus untracked-but-not-ignored ones, so it fails before commit.
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, check=True,
    ).stdout.decode().split("\0")
    for name in filter(None, out):
        p = REPO / name
        if not p.is_file():
            continue
        try:
            yield name, p.read_text()
        except UnicodeDecodeError:
            continue


def test_no_private_addresses():
    denylist_path = REPO / "tests" / "private-denylist.txt"
    deny = []
    if denylist_path.exists():
        deny = [ln.strip().lower() for ln in denylist_path.read_text().splitlines()
                if ln.strip()]
    bad = []
    for name, text in _files():
        if name == "tests/test_privacy.py":
            continue
        for m in IPV4.finditer(text):
            try:
                ip = ipaddress.ip_address(m.group(1))
            except ValueError:
                continue
            if not any(ip in n for n in RFC1918):
                continue
            if m.group(1) in ALLOWED_IPS or any(ip in n for n in ALLOWED_NETS):
                continue
            bad.append(f"{name}: {m.group(1)}")
        for m in MAC.finditer(text):
            if not m.group(0).lower().startswith("02:00:00:"):
                bad.append(f"{name}: {m.group(0)}")
        low = text.lower()
        bad += [f"{name}: denylisted {d!r}" for d in deny if d in low]
    assert not bad, "\n".join(bad)
