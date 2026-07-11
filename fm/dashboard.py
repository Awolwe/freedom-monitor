"""Рендер dashboard.html: инжект данных в шаблон через маркеры.

/*__DATA__*/      -> const DATA = {...};
<!--__PICTURE__--> -> HTML «картины страны» (markdown -> html на этапе сборки)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import markdown

from . import aggregate, market

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "dashboard_template.html"
OUTPUT = ROOT / "dashboard.html"
PICTURE = ROOT / "data" / "picture.md"


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

    quality = aggregate.data_quality(events)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "disclaimer": cfg["dashboard"]["disclaimer"],
        "alpha_month": mcfg["alpha"],
        "monthly": rows,
        "events": ev_payload,
        "market": market.load_series(),
        "quality": {
            "n_events": quality["n_events"],
            "zero_share": {k: round(v, 2) for k, v in quality["zero_share"].items()},
            "sig3": quality["sig3"],
            "thin_months": quality["thin_months"],
        },
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
