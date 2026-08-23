"""Рендер dashboard.html: инжект данных в шаблон через маркеры.

/*__DATA__*/      -> const DATA = {...};
<!--__PICTURE__--> -> HTML «картины страны» (markdown -> html на этапе сборки)
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

import markdown

from . import aggregate, market

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "dashboard_template.html"
OUTPUT = ROOT / "dashboard.html"
PICTURE = ROOT / "data" / "picture.md"
RELIABILITY = ROOT / "data" / "reliability" / "report.json"


def build_data(events: list[aggregate.Event], cfg: dict) -> dict:
    mcfg = cfg["aggregation"]["monthly"]
    rows = aggregate.monthly_flow(events, mcfg["normalizer"], cfg["aggregation"]["flow_clip"])
    rows = aggregate.add_levels(rows, mcfg["alpha"])

    ev_payload = [
        {
            "id": e.id, "date": e.date, "month": e.month, "title": e.title,
            "summary": e.summary, "category": e.category, "region": e.region,
            "scores": e.scores.model_dump(), "escape": list(e.escape),
            "press": e.press_pressure, "sig": e.significance,
            "rationale": e.rationale, "origin": e.origin,
        }
        for e in sorted(events, key=lambda x: x.date, reverse=True)
    ]

    quality = aggregate.data_quality(events, now_month=datetime.now().strftime('%Y-%m'))
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "disclaimer": cfg["dashboard"]["disclaimer"],
        "alpha_month": mcfg["alpha"],
        "prompt_version": series_version(events),
        "monthly": rows,
        "events": ev_payload,
        "market": market.load_series(),
        "quality": {
            "n_events": quality["n_events"],
            "zero_share": {k: round(v, 2) for k, v in quality["zero_share"].items()},
            "sig3": quality["sig3"],
            "thin_months": quality["thin_months"],
            "escape_share": round(quality["escape_share"], 3),
            "escape_counts": quality["escape_counts"],
            "all_zero_share": round(quality["all_zero_share"], 3),
            "effective_n": quality["effective_n"],
            "effective_n_axis": quality["effective_n_axis"],
            "grounded_share": round(quality["grounded_share"], 3),
        },
        "reliability": load_reliability(),
    }


def series_version(events: list[aggregate.Event]) -> str:
    """Версия ряда — самый поздний применённый пере-скоринг, а не самая частая версия:
    большинство событий патч не трогает, и мода показала бы исходную 1.0."""
    def key(v: str) -> tuple:
        return tuple(int(p) if p.isdigit() else -1 for p in v.split("."))
    versions = {e.prompt_version for e in events}
    return max(versions, key=key) if versions else "—"


def load_reliability() -> dict | None:
    """Снимок измеренной надёжности. Не пересчитывается против текущего ряда:
    после адъюдикации это дало бы искусственные 100% (RELIABILITY.md)."""
    if not RELIABILITY.exists():
        return None
    rep = json.loads(RELIABILITY.read_text(encoding="utf-8"))
    return {
        "n_common": rep["n_common"],
        "coverage": round(rep.get("coverage", 0), 3),
        "axes": {a: {"exact": round(s["exact"], 3), "alpha": s["alpha"]}
                 for a, s in rep["axes"].items()},
        "significance": round(rep["significance"]["exact"], 3),
        "note": rep.get("note", ""),
    }


def render(events: list[aggregate.Event], cfg: dict) -> Path:
    template = TEMPLATE.read_text(encoding="utf-8")
    # "</" экранируется, чтобы содержимое событий не могло закрыть <script>
    data_js = json.dumps(build_data(events, cfg), ensure_ascii=False).replace("</", "<\\/")

    picture_html = ""
    if PICTURE.exists():
        picture_html = markdown.markdown(PICTURE.read_text(encoding="utf-8"), extensions=["tables"])

    html = template.replace("/*__DATA__*/", "const DATA = " + data_js + ";")
    html = html.replace("<!--__PICTURE__-->", picture_html)
    OUTPUT.write_text(html, encoding="utf-8")
    return OUTPUT
