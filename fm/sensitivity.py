"""Чувствительность траектории к параметрам агрегации (METHODOLOGY §4).

Кривая на дашборде висит на четырёх решениях, которые не выведены из данных:
нормировщик, α сглаживания, порог клиппинга и веса значимости. Вопрос не
«какие значения правильные», а «меняется ли ответ, если взять другие разумные».

Три уровня устойчивости, от слабого к сильному:

* **форма** — совпадают ли подъёмы и спады (ранговая корреляция рядов);
* **знак** — согласны ли конфигурации, что месяц выше или ниже нуля;
* **величина** — насколько расходится итоговый уровень.

Форма может держаться при разъезжающейся величине: это нормально и означает,
что читать нужно отклонения от собственного базлайна, а не абсолютные значения
(того же требует METHODOLOGY §5). А вот разъезжающийся **знак** — это уже
«дашборд показывает выбор параметра, а не страну».
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from .aggregate import AXES, Event, add_levels, ema, monthly_flow

# Схемы весов значимости: как значимость 1/2/3 превращается в вес слагаемого.
WEIGHT_SCHEMES: dict[str, dict[int, float]] = {
    "линейная (текущая)": {1: 1, 2: 2, 3: 3},
    "равные": {1: 1, 2: 1, 3: 1},
    "крутая": {1: 1, 2: 2, 3: 4},
    "пологая": {1: 1, 2: 1.5, 3: 2},
}


@dataclass(frozen=True)
class Config:
    normalizer: float
    alpha: float
    clip: float
    weights: str

    def label(self) -> str:
        return f"N={self.normalizer:g} α={self.alpha:g} clip={self.clip:g} веса={self.weights}"


def flow_with_weights(events: list[Event], cfg: Config) -> list[dict]:
    """monthly_flow с произвольной схемой весов. При линейной схеме совпадает
    с fm.aggregate.monthly_flow — это проверяется тестом."""
    w = WEIGHT_SCHEMES[cfg.weights]
    months = sorted({e.month for e in events})
    rows = []
    for m in months:
        evs = [e for e in events if e.month == m]
        row = {"month": m, "n_events": len(evs)}
        for a in AXES:
            raw = sum(getattr(e.scores, a) * w[e.significance] for e in evs)
            row[a] = max(-cfg.clip, min(cfg.clip, raw / cfg.normalizer))
        rows.append(row)
    return rows


def levels(events: list[Event], cfg: Config) -> dict[str, list[float]]:
    rows = add_levels(flow_with_weights(events, cfg), cfg.alpha)
    return {a: [r[f"level_{a}"] for r in rows] for a in AXES}


# --- статистика без numpy --------------------------------------------------

def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):  # средние ранги для связок
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float | None:
    """Ранговая корреляция: совпадает ли форма кривых. None, если ряд без разброса."""
    if len(a) != len(b) or len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return None if da == 0 or db == 0 else num / (da * db)


def _sign(v: float, eps: float = 1e-9) -> int:
    return 0 if abs(v) < eps else (1 if v > 0 else -1)


# --- анализ ----------------------------------------------------------------

def grid(normalizers, alphas, clips, weights) -> list[Config]:
    return [Config(n, al, c, w) for n, al, c, w in product(normalizers, alphas, clips, weights)]


def analyse(events: list[Event], configs: list[Config], baseline: Config) -> dict:
    series = {c: levels(events, c) for c in configs}
    months = sorted({e.month for e in events})
    base = series[baseline]

    per_axis = {}
    for axis in AXES:
        shapes = [s for c in configs if c != baseline
                  for s in [spearman(base[axis], series[c][axis])] if s is not None]
        finals = [series[c][axis][-1] for c in configs]

        # согласие по знаку помесячно: доля месяцев, где все конфигурации сошлись
        agree, flipped = 0, []
        for i, m in enumerate(months):
            signs = {_sign(series[c][axis][i]) for c in configs}
            if len(signs) == 1:
                agree += 1
            elif {1, -1} <= signs:  # не просто «ноль или плюс», а настоящий разворот
                flipped.append(m)

        per_axis[axis] = {
            "shape_min": min(shapes) if shapes else None,
            "shape_median": sorted(shapes)[len(shapes) // 2] if shapes else None,
            "final_baseline": base[axis][-1],
            "final_min": min(finals),
            "final_max": max(finals),
            "sign_agreement": agree / len(months),
            "sign_flips": flipped,
        }

    return {
        "n_configs": len(configs),
        "n_months": len(months),
        "baseline": baseline.label(),
        "axes": per_axis,
        "clip_binds": clip_binds(events, configs),
    }


def clip_binds(events: list[Event], configs: list[Config]) -> dict:
    """Срабатывает ли клиппинг хоть раз. Если нет — параметр мёртвый."""
    hits = {}
    for c in configs:
        n = sum(1 for row in flow_with_weights(events, c)
                for a in AXES if abs(row[a]) >= c.clip - 1e-9)
        if n:
            hits[c.label()] = n
    return hits


def format_report(rep: dict, markdown: bool = False) -> str:
    h = "## " if markdown else ""
    pct = lambda v: f"{v * 100:.0f}%"
    lines = [
        f"{h}Чувствительность к параметрам агрегации",
        f"Конфигураций: {rep['n_configs']}, месяцев: {rep['n_months']}. "
        f"База: {rep['baseline']}.",
        "",
        "| Ось | форма (ранг. корр., мин / медиана) | итог базы | итог по сетке | согласие знака | развороты |",
        "|---|---|---|---|---|---|",
    ]
    for axis, s in rep["axes"].items():
        shape = "—" if s["shape_min"] is None else f"{s['shape_min']:+.2f} / {s['shape_median']:+.2f}"
        flips = "нет" if not s["sign_flips"] else f"{len(s['sign_flips'])} мес."
        lines.append(
            f"| {axis} | {shape} | {s['final_baseline']:+.2f} | "
            f"{s['final_min']:+.2f} … {s['final_max']:+.2f} | "
            f"{pct(s['sign_agreement'])} | {flips} |"
        )
    lines.append("")
    if rep["clip_binds"]:
        lines.append(f"Клиппинг срабатывает в {len(rep['clip_binds'])} конфигурациях.")
    else:
        lines.append("**Клиппинг не срабатывает ни разу** — параметр `flow_clip` на этих данных "
                     "не влияет ни на что.")
    return "\n".join(lines)
