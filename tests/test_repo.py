"""Repo hygiene guards."""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_source_dirs_not_gitignored():
    files = [
        str(p.relative_to(REPO))
        for top in ("scripts", "ansible/files")
        if (REPO / top).is_dir()
        for p in (REPO / top).rglob("*")
        if p.is_file()
    ]
    assert files, "expected files under scripts/"
    r = subprocess.run(["git", "check-ignore", "--no-index", *files], cwd=REPO,
                       capture_output=True, text=True)
    assert r.stdout.strip() == "", f"gitignored source files:\n{r.stdout}"
