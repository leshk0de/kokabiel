"""The Grafana dashboard and metrics doc must stay in sync with the collector."""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DASH = REPO / "grafana" / "kokabiel-probe.json"
LUA = REPO / "scripts" / "files" / "prometheus-collectors" / "kokabiel.lua"
DOC = REPO / "docs" / "metrics.md"
NAME = re.compile(r"kokabiel_[a-z_]+")


def _panels(d):
    for p in d["panels"]:
        yield p
        yield from p.get("panels", [])


def test_dashboard_valid_json():
    d = json.loads(DASH.read_text())
    assert d["schemaVersion"] >= 39
    names = {v["name"] for v in d["templating"]["list"]}
    assert "probe" in names
    probe = next(v for v in d["templating"]["list"] if v["name"] == "probe")
    assert "label_values(kokabiel_capture_link_up, probe)" in json.dumps(probe)
    exprs = [t["expr"] for p in _panels(d) for t in p.get("targets", [])]
    assert exprs
    assert all('probe=~"$probe"' in e for e in exprs)


def test_dashboard_metrics_exist():
    d = json.loads(DASH.read_text())
    known = set(NAME.findall(LUA.read_text()))
    used = {m for p in _panels(d) for t in p.get("targets", [])
            for m in NAME.findall(t["expr"])}
    assert used and used <= known, used - known


def test_metrics_doc_lists_all_metrics():
    known = set(re.findall(r'"(kokabiel_[a-z_]+)"', LUA.read_text()))
    assert known
    doc = DOC.read_text()
    assert [m for m in sorted(known) if m not in doc] == []
