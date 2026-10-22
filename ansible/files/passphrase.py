#!/usr/bin/env python3
"""Print one passphrase: three EFF large-wordlist words plus a 4-digit number."""
import argparse
import re
import secrets
from pathlib import Path

DEFAULT_WORDLIST = Path(__file__).resolve().parent / "eff_large_wordlist.txt"


def load_words(path):
    words = []
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and re.fullmatch(r"[a-z]+", parts[1]):
            words.append(parts[1])
    if not words:
        raise SystemExit(f"no usable words in {path}")
    return words


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wordlist", default=str(DEFAULT_WORDLIST))
    args = ap.parse_args()
    words = load_words(args.wordlist)
    picked = [secrets.choice(words) for _ in range(3)]
    print("-".join(picked) + f"-{secrets.randbelow(10000):04d}")


if __name__ == "__main__":
    main()
