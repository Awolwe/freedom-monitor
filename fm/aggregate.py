"""Схема событий, валидация и агрегация Flow/Level (METHODOLOGY.md §4).

Метрики — чистые функции от events-файла: полный пересчёт при каждой сборке.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

AXES = ["freedom_from", "freedom_for", "solidarity", "hope"]
MECHANISMS = ["authoritarianism", "destructiveness", "conformity"]

Category = Literal["политика", "экономика", "общество", "права и свободы", "безопасность", "международное"]
Mechanism = Literal["authoritarianism", "destructiveness", "conformity"]


class Scores(BaseModel):
    freedom_from: int = Field(ge=-3, le=3)
    freedom_for: int = Field(ge=-3, le=3)
    solidarity: int = Field(ge=-3, le=3)
    hope: int = Field(ge=-3, le=3)


class Event(BaseModel):
    id: str
    date: str
    month: str
    title: str
    summary: str = ""
    category: Category
    region: str = "национальное"
    scores: Scores
    escape: list[Mechanism] = []
    press_pressure: bool = False
    significance: int = Field(ge=1, le=3)
    rationale: str = ""
    origin: str = "model_memory"
    prompt_version: str = "1.0"
    # заземление на источник (см. METHODOLOGY §5): проставляется проходом верификации
    source_url: str = ""
    verified: Literal["", "confirmed", "corrected", "unconfirmed"] = ""

    @field_validator("month")
    @classmethod
    def month_matches_date(cls, v: str, info):
        d = info.data.get("date", "")
        if d and not d.startswith(v):
            raise ValueError(f"month {v} не совпадает с date {d}")
        return v

    def nonzero_axes(self) -> list[str]:
        return [a for a in AXES if getattr(self.scores, a) != 0]


def load_events(path: Path) -> tuple[list[Event], list[str]]:
    """Читает JSONL; возвращает (валидные события, список ошибок)."""
    events, errors = [], []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                ev = Event.model_validate_json(line)
            except Exception as exc:
                errors.append(f"{path.name}:{n}: {exc}")
                continue
            if ev.nonzero_axes() and not ev.rationale.strip():
                errors.append(f"{path.name}:{n} ({ev.id}): ненулевые оценки без rationale")
            events.append(ev)
    return events, errors


def data_quality(events: list[Event], now_month: str = "") -> dict:
    """Диагностика распределения: доля нулей по осям, покрытие месяцев.

    `now_month` (YYYY-MM) — текущий месяц: он ещё идёт, поэтому малое число событий
    в нём не дефект покрытия, и в список тонких месяцев он не попадает.
    """
    n = len(events)
    zero_share = {a: sum(1 for e in events if getattr(e.scores, a) == 0) / max(n, 1) for a in AXES}
    months = sorted({e.month for e in events})
    per_month = {m: sum(1 for e in events if e.month == m) for m in months}
    return {
        "n_events": n,
        "zero_share": zero_share,
        "months": months,
        "per_month": per_month,
        "sig3": sum(1 for e in events if e.significance == 3),
        "thin_months": [m for m, c in per_month.items() if c < 3 and m != now_month],
        "current_month": now_month if now_month in per_month else "",
        # §3 ждёт 10-25% тегированных событий; §1.1 — 60-80% нулей по осям
        "escape_share": sum(1 for e in events if e.escape) / max(n, 1),
        "escape_counts": {m: sum(1 for e in events if m in e.escape) for m in MECHANISMS},
        "all_zero_share": sum(1 for e in events if not e.nonzero_axes()) / max(n, 1),
        # Реальная база измерения: событие без единой ненулевой оси не влияет ни на одну
        # метрику, а на конкретной оси работают только события с ненулевой оценкой по ней.
        # n_events заметно завышает то, на чём стоит кривая.
        "effective_n": sum(1 for e in events if e.nonzero_axes()),
        "effective_n_axis": {a: sum(1 for e in events if getattr(e.scores, a) != 0) for a in AXES},
        "grounded_share": sum(1 for e in events if e.source_url) / max(n, 1),
        "verified_counts": {
            v: sum(1 for e in events if e.verified == v)
            for v in ("confirmed", "corrected", "unconfirmed")
        },
    }


def monthly_flow(events: list[Event], normalizer: float = 12.0, clip: float = 5.0) -> list[dict]:
    """Flow_axis(месяц) = clip(Σ score×w / N_норм, ±clip) + счётчики тегов."""
    months = sorted({e.month for e in events})
    rows = []
    for m in months:
        evs = [e for e in events if e.month == m]
        row = {"month": m, "n_events": len(evs)}
        for a in AXES:
            raw = sum(getattr(e.scores, a) * e.significance for e in evs)
            row[a] = max(-clip, min(clip, raw / normalizer))
        for mech in MECHANISMS:
            row[f"esc_{mech}"] = sum(1 for e in evs if mech in e.escape)
        row["press_pressure_n"] = sum(1 for e in evs if e.press_pressure)
        rows.append(row)
    return rows


def inertia(flow_rows: list[dict]) -> dict[str, dict]:
    """Месяцы, где Level держится только памятью EMA: Flow = 0, а линия не в нуле.

    Такой месяц ничего не сообщает о поле — он показывает затухание прошлого.
    На разреженных осях таких месяцев большинство, и без пометки кривая читается
    как непрерывное наблюдение, которым не является. Требует add_levels.
    """
    out = {}
    for a in AXES:
        idle = [r["month"] for r in flow_rows if r[a] == 0 and abs(r.get(f"level_{a}", 0)) > 1e-9]
        # месяцы, где поле двинулось против сглаженной линии
        against = [r["month"] for r in flow_rows
                   if r[a] != 0 and r.get(f"level_{a}", 0) != 0
                   and (r[a] > 0) != (r[f"level_{a}"] > 0)]
        out[a] = {
            "idle_months": idle,
            "idle_share": len(idle) / max(len(flow_rows), 1),
            "against_months": against,
        }
    return out


def ema(values: list[float], alpha: float) -> list[float]:
    out, level = [], 0.0
    for v in values:
        level = (1 - alpha) * level + alpha * v
        out.append(round(level, 4))
    return out


def add_levels(flow_rows: list[dict], alpha: float = 0.4) -> list[dict]:
    for a in AXES:
        levels = ema([r[a] for r in flow_rows], alpha)
        for r, lv in zip(flow_rows, levels):
            r[f"level_{a}"] = lv
    return flow_rows
