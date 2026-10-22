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



def _task_block(rel, name):
    text = (REPO / rel).read_text()
    start = text.index(f"- name: {name}")
    nxt = text.find("\n- name:", start + 1)
    nxt2 = text.find("\n    - name:", start + 1)
    ends = [e for e in (nxt, nxt2) if e != -1]
    return text[start:min(ends) if ends else len(text)]


def test_status_task_retries_then_fails():
    t = _task_block("ansible/roles/probe/tasks/main.yml", "Check probe health")
    for want in ("until: probe_status.rc == 0", "retries: 5", "delay: 2",
                 "failed_when: probe_status.rc != 0"):
        assert want in t, t


def test_capture_requires_limit_first():
    text = (REPO / "ansible/capture.yml").read_text()
    first = text.index("tasks:")
    t = _task_block("ansible/capture.yml", "Require an explicit probe (-l)")
    assert text.index("- name: Require an explicit probe (-l)") < text.index("- name: Run the capture")
    assert text.index("- name:", first) == text.index("- name: Require an explicit probe (-l)")
    assert "ansible_limit is defined" in t and "-l <probe>" in t
