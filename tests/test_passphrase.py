import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ansible" / "files" / "passphrase.py"
WORDLIST = ROOT / "ansible" / "files" / "eff_large_wordlist.txt"


def run():
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True, check=True)
    assert r.stdout.endswith("\n") and r.stdout.count("\n") == 1
    return r.stdout.strip()


def words():
    out = set()
    for line in WORDLIST.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"[a-z]+", parts[1]):
            out.add(parts[1])
    return out


def test_format():
    for _ in range(200):
        assert re.fullmatch(r"[a-z]+-[a-z]+-[a-z]+-\d{4}", run())


def test_words_from_list():
    allowed = words()
    for _ in range(50):
        for w in run().split("-")[:3]:
            assert w in allowed


def test_uses_secrets():
    src = SCRIPT.read_text()
    assert "secrets." in src
    assert "import random" not in src


def test_varies():
    outs = [run() for _ in range(50)]
    assert len(set(outs)) == 50
