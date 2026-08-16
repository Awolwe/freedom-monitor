"""Тесты сборки данных дашборда."""
from __future__ import annotations

import json

import pytest

from fm import dashboard
from fm.aggregate import Event


def mk(eid="e1", date="2024-03-15", version="1.0", **over) -> Event:
    base = dict(
        id=eid, date=date, month=date[:7], title="t", category="политика",
        scores={"freedom_from": 0, "freedom_for": 0, "solidarity": 0, "hope": 0},
        significance=2, rationale="r", prompt_version=version,
    )
    base.update(over)
    return Event(**base)


# --- версия ряда -----------------------------------------------------------

def test_series_version_is_latest_patch_not_the_most_common():
    """Патч трогает меньшинство событий: мода показала бы исходную версию."""
    events = [mk(f"a{i}", version="1.0") for i in range(200)] + \
             [mk(f"b{i}", version="1.4") for i in range(11)]
    assert dashboard.series_version(events) == "1.4"


def test_series_version_orders_numerically_not_lexically():
    assert dashboard.series_version([mk("a", version="1.9"), mk("b", version="1.10")]) == "1.10"


def test_series_version_survives_non_numeric_version():
    assert dashboard.series_version([mk("a", version="1.1-passB"), mk("b", version="1.2")]) == "1.2"


def test_series_version_on_empty_input():
    assert dashboard.series_version([]) == "—"


# --- надёжность ------------------------------------------------------------

def test_load_reliability_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "RELIABILITY", tmp_path / "nope.json")
    assert dashboard.load_reliability() is None


def test_load_reliability_reads_snapshot_and_flags_same_model(tmp_path, monkeypatch):
    snap = tmp_path / "report.json"
    snap.write_text(json.dumps({
        "n_common": 40, "coverage": 0.15,
        "axes": {"hope": {"exact": 0.925, "alpha": 0.88}},
    }), encoding="utf-8")
    monkeypatch.setattr(dashboard, "RELIABILITY", snap)
    rel = dashboard.load_reliability()
    assert rel["n_common"] == 40 and rel["axes"]["hope"]["exact"] == 0.925
    assert rel["same_model"] is True, "дашборд обязан подписать, что это самосогласованность"


# --- build_data ------------------------------------------------------------

@pytest.fixture
def cfg():
    return {
        "aggregation": {"monthly": {"normalizer": 12, "alpha": 0.4}, "flow_clip": 5},
        "dashboard": {"title": "t", "disclaimer": "d", "feed_limit": 600},
    }


def test_build_data_exposes_quality_fields_the_footer_reads(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "RELIABILITY", tmp_path / "nope.json")
    grounded = mk("a", escape=["conformity"], source_url="https://x.kz", verified="confirmed")
    data = dashboard.build_data([grounded, mk("b")], cfg)
    q = data["quality"]
    for field in ("n_events", "zero_share", "sig3", "thin_months",
                  "escape_share", "escape_counts", "all_zero_share", "grounded_share"):
        assert field in q, f"подвал дашборда читает quality.{field}"
    assert q["escape_share"] == 0.5 and q["grounded_share"] == 0.5
    assert q["escape_counts"]["conformity"] == 1


def test_build_data_events_are_newest_first(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard, "RELIABILITY", tmp_path / "nope.json")
    data = dashboard.build_data([mk("old", "2024-01-01"), mk("new", "2024-09-01")], cfg)
    assert [e["id"] for e in data["events"]] == ["new", "old"]


def test_render_escapes_closing_script_tag(cfg, tmp_path, monkeypatch):
    """Текст события не должен уметь закрыть <script> в собранном дашборде."""
    monkeypatch.setattr(dashboard, "RELIABILITY", tmp_path / "nope.json")
    tpl = tmp_path / "tpl.html"
    tpl.write_text("<script>/*__DATA__*/</script><!--__PICTURE__-->", encoding="utf-8")
    monkeypatch.setattr(dashboard, "TEMPLATE", tpl)
    monkeypatch.setattr(dashboard, "OUTPUT", tmp_path / "out.html")
    monkeypatch.setattr(dashboard, "PICTURE", tmp_path / "absent.md")
    out = dashboard.render([mk("a", title="</script><img onerror=alert(1)>")], cfg)
    html = out.read_text(encoding="utf-8")
    assert "</script><img" not in html
    assert "<\\/script>" in html
