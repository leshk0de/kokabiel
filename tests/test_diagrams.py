"""Committed diagram SVGs must match their D2 sources."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DIAGRAMS = REPO / "docs/diagrams"
SOURCES = sorted(DIAGRAMS.glob("*.d2"))


def _d2_flags():
    m = re.search(r"^D2_FLAGS := (.+)$", (REPO / "Makefile").read_text(), re.M)
    assert m, "D2_FLAGS missing from Makefile"
    return m.group(1).split()


def test_every_source_has_svg():
    assert SOURCES, "expected docs/diagrams/*.d2"
    missing = [s.name for s in SOURCES if not s.with_suffix(".svg").exists()]
    assert not missing, f"no SVG for {missing}; run 'make diagrams'"


def test_doc_image_links_resolve():
    for doc in [REPO / "README.md", *(REPO / "docs").glob("*.md")]:
        for target in re.findall(r"!\[[^\]]*\]\(([^)]+\.svg)\)", doc.read_text()):
            assert (doc.parent / target).exists(), f"{doc.name}: broken image {target}"


@pytest.mark.skipif(shutil.which("d2") is None, reason="d2 not installed; run 'make tools'")
@pytest.mark.parametrize("src", SOURCES, ids=lambda p: p.name)
def test_svg_matches_source(src):
    r = subprocess.run(["d2", *_d2_flags(), "-", "-"], input=src.read_bytes(),
                       capture_output=True, check=True)
    assert r.stdout == src.with_suffix(".svg").read_bytes(), \
        f"{src.with_suffix('.svg').name} is stale; run 'make diagrams'"
