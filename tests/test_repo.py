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


def test_bootstrap_installs_module_deps():
    # python3-light lacks encodings.idna, decimal and logging, which
    # ansible-core module_utils import on every module run, and
    # multiprocessing, which async tasks (capture.yml) need.
    t = (REPO / "ansible/roles/bootstrap_python/tasks/main.yml").read_text()
    assert "import encodings.idna, decimal, logging, multiprocessing" in t, t
    pkgs = next(l for l in t.splitlines() if "pkgs=" in l)
    for pkg in ("python3-light", "python3-codecs", "python3-decimal",
                "python3-logging", "python3-multiprocessing"):
        assert pkg in pkgs, pkgs
    for mgr in ("apk add $pkgs", "opkg install $pkgs"):
        assert mgr in t, t


# Actions that run on the controller and never use the remote interpreter.
_CONTROLLER_ACTIONS = {"set_fact", "debug", "fail", "assert", "pause", "meta"}


def _module(task):
    keys = set(task) - {"name", "vars", "when", "register", "args", "loop",
                        "loop_control", "no_log", "changed_when", "failed_when",
                        "delegate_to", "become", "check_mode", "run_once",
                        "tags", "until", "retries", "delay", "notify"}
    return next(iter(keys)).rsplit(".", 1)[-1] if keys else ""


def _remote_tasks_with_controller_python(tasks, inherited=False):
    for t in tasks or []:
        v = str((t.get("vars") or {}).get("ansible_python_interpreter", ""))
        local_py = inherited or "ansible_playbook_python" in v
        if "block" in t:
            for part in ("block", "rescue", "always"):
                yield from _remote_tasks_with_controller_python(t.get(part), local_py)
        elif (local_py and t.get("delegate_to") != "localhost"
              and _module(t) not in _CONTROLLER_ACTIONS):
            yield t.get("name")


def test_controller_python_only_on_localhost_tasks():
    # The probe has no copy of the controller's Python; a remote module that
    # inherits ansible_playbook_python fails with "module interpreter ... not
    # found" (first live run: passwd root inside the passphrase block).
    import yaml
    bad = []
    files = sorted((REPO / "ansible/roles").glob("*/tasks/*.yml"))
    files += sorted((REPO / "ansible").glob("*.yml"))
    for f in files:
        doc = yaml.safe_load(f.read_text()) or []
        plays = [p for p in doc if isinstance(p, dict) and "tasks" in p]
        task_lists = [p["tasks"] for p in plays] if plays else [doc]
        for tasks in task_lists:
            bad += [f"{f.relative_to(REPO)}: {n}"
                    for n in _remote_tasks_with_controller_python(tasks)]
    assert not bad, bad
