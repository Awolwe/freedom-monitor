"""Надёжность кодирования: согласие двух независимых проходов по одним событиям.

Без этой проверки Flow/Level — одно мнение, отрисованное графиком. Метрика —
альфа Криппендорфа (ordinal для осей −3…+3 и значимости, nominal для булевых тегов),
плюс сырые проценты совпадений, которые читаются без знания статистики.

Реализация — чистый Python (numpy в проекте нет). Случай «два кодировщика,
без пропусков»: каждая единица даёт два упорядоченных наблюдения.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from .aggregate import AXES, MECHANISMS, Event, load_events

# альфа на редких категориях неустойчива; ниже этого числа «сработавших» единиц
# число не показывается как оценка
MIN_VARIANT_UNITS = 5


# --- альфа Криппендорфа ----------------------------------------------------

def _delta2_nominal(c, k, _marg, _order) -> float:
    return 0.0 if c == k else 1.0


def _delta2_interval(c, k, _marg, _order) -> float:
    return float((c - k) ** 2)


def _delta2_ordinal(c, k, marg: dict, order: list) -> float:
    """δ²(c,k) = ( Σ_{g от c до k} n_g − (n_c + n_k)/2 )² по порядку значений."""
    i, j = order.index(c), order.index(k)
    if i > j:
        i, j = j, i
    between = sum(marg[order[g]] for g in range(i, j + 1))
    return (between - (marg[order[i]] + marg[order[j]]) / 2) ** 2


_METRICS = {"nominal": _delta2_nominal, "interval": _delta2_interval, "ordinal": _delta2_ordinal}


def krippendorff_alpha(pairs: list[tuple], metric: str = "ordinal") -> float | None:
    """pairs — [(оценка A, оценка B)] по одним и тем же единицам.

    Возвращает None, если все оценки одинаковы (De = 0: разногласие невозможно,
    альфа не определена — это не «идеальное согласие»).
    """
    if not pairs:
        return None
    delta2 = _METRICS[metric]

    coincidence: Counter = Counter()
    for a, b in pairs:
        coincidence[(a, b)] += 1  # 2 кодировщика ⇒ вклад пары = 1/(m−1) = 1
        coincidence[(b, a)] += 1

    marg: Counter = Counter()
    for (c, _k), cnt in coincidence.items():
        marg[c] += cnt
    order = sorted(marg)
    n = sum(marg.values())
    if len(order) < 2:
        return None

    do = sum(cnt * delta2(c, k, marg, order) for (c, k), cnt in coincidence.items()) / n
    de = sum(
        marg[c] * marg[k] * delta2(c, k, marg, order)
        for c in order for k in order
    ) / (n * (n - 1))
    if de == 0:
        return None
    return 1 - do / de


# --- сравнение проходов ----------------------------------------------------

def _axis_stats(pairs: list[tuple[int, int]]) -> dict:
    n = len(pairs)
    exact = sum(1 for a, b in pairs if a == b)
    within1 = sum(1 for a, b in pairs if abs(a - b) <= 1)
    # согласие по знаку среди единиц, где хотя бы один проход дал ненулевую оценку
    signed = [(a, b) for a, b in pairs if a != 0 or b != 0]
    sign_ok = sum(1 for a, b in signed if (a > 0) == (b > 0) and (a < 0) == (b < 0))
    # «поймал ли второй проход сигнал вообще» — ключевое для осей с 90% нулей
    both_nonzero = sum(1 for a, b in pairs if a != 0 and b != 0)
    return {
        "n": n,
        "exact": exact / n if n else 0.0,
        "within1": within1 / n if n else 0.0,
        "alpha": krippendorff_alpha(pairs, "ordinal"),
        "n_signal": len(signed),
        "sign_agreement": sign_ok / len(signed) if signed else None,
        "both_nonzero": both_nonzero,
    }


def _tag_stats(pairs: list[tuple[bool, bool]]) -> dict:
    both = sum(1 for a, b in pairs if a and b)
    only_a = sum(1 for a, b in pairs if a and not b)
    only_b = sum(1 for a, b in pairs if b and not a)
    union = both + only_a + only_b
    variant_units = union
    return {
        "n": len(pairs),
        "both": both,
        "only_a": only_a,
        "only_b": only_b,
        "jaccard": both / union if union else None,
        "alpha": krippendorff_alpha([(int(a), int(b)) for a, b in pairs], "nominal"),
        "stable": variant_units >= MIN_VARIANT_UNITS,
    }


def compare(pass_a: list[Event], pass_b: list[Event]) -> dict:
    a_by_id = {e.id: e for e in pass_a}
    b_by_id = {e.id: e for e in pass_b}
    common = sorted(set(a_by_id) & set(b_by_id))
    if not common:
        raise ValueError("у проходов нет общих id — нечего сравнивать")

    axes = {}
    for axis in AXES:
        axes[axis] = _axis_stats(
            [(getattr(a_by_id[i].scores, axis), getattr(b_by_id[i].scores, axis)) for i in common]
        )

    tags = {}
    for mech in MECHANISMS:
        tags[mech] = _tag_stats([(mech in a_by_id[i].escape, mech in b_by_id[i].escape) for i in common])
    tags["press_pressure"] = _tag_stats(
        [(a_by_id[i].press_pressure, b_by_id[i].press_pressure) for i in common]
    )

    sig_pairs = [(a_by_id[i].significance, b_by_id[i].significance) for i in common]

    return {
        "n_common": len(common),
        "only_in_a": sorted(set(a_by_id) - set(b_by_id)),
        "only_in_b": sorted(set(b_by_id) - set(a_by_id)),
        "axes": axes,
        "tags": tags,
        "significance": {
            "exact": sum(1 for a, b in sig_pairs if a == b) / len(sig_pairs),
            "within1": sum(1 for a, b in sig_pairs if abs(a - b) <= 1) / len(sig_pairs),
            "alpha": krippendorff_alpha(sig_pairs, "ordinal"),
        },
    }


def compare_files(base: Path, path_a: Path, path_b: Path) -> dict:
    a, err_a = load_events(path_a)
    b, err_b = load_events(path_b)
    if err_a or err_b:
        raise ValueError(f"невалидные проходы: {(err_a + err_b)[:3]}")
    report = compare(a, b)
    if base.exists():
        corpus, _ = load_events(base)
        report["corpus_n"] = len(corpus)
        report["coverage"] = report["n_common"] / max(len(corpus), 1)
    report["files"] = {"a": path_a.name, "b": path_b.name}
    return report


# --- вывод -----------------------------------------------------------------

def _fmt_alpha(v: float | None) -> str:
    return "—" if v is None else f"{v:+.2f}"


def format_report(rep: dict, markdown: bool = False) -> str:
    h = "## " if markdown else ""
    lines = [f"{h}Надёжность кодирования"]
    files = rep.get("files", {})
    lines.append(
        f"Двойное кодирование: {rep['n_common']} событий"
        + (f" из {rep['corpus_n']} ({rep['coverage']:.0%} корпуса)" if "corpus_n" in rep else "")
        + (f" · проходы: {files.get('a')} vs {files.get('b')}" if files else "")
    )
    lines.append("")
    lines.append("| Ось | точное | ±1 | знак | альфа (ordinal) | оба ≠0 |")
    lines.append("|---|---|---|---|---|---|")
    for axis, s in rep["axes"].items():
        sign = "—" if s["sign_agreement"] is None else f"{s['sign_agreement']:.0%}"
        lines.append(
            f"| {axis} | {s['exact']:.0%} | {s['within1']:.0%} | {sign} "
            f"({s['n_signal']} ед.) | {_fmt_alpha(s['alpha'])} | {s['both_nonzero']} |"
        )
    sig = rep["significance"]
    lines.append(
        f"| significance | {sig['exact']:.0%} | {sig['within1']:.0%} | — "
        f"| {_fmt_alpha(sig['alpha'])} | — |"
    )
    lines.append("")
    lines.append("| Тег | оба | только A | только B | Jaccard | альфа (nominal) |")
    lines.append("|---|---|---|---|---|---|")
    for tag, s in rep["tags"].items():
        jac = "—" if s["jaccard"] is None else f"{s['jaccard']:.0%}"
        alpha = _fmt_alpha(s["alpha"]) + ("" if s["stable"] else " ⚠")
        lines.append(f"| {tag} | {s['both']} | {s['only_a']} | {s['only_b']} | {jac} | {alpha} |")
    lines.append("")
    lines.append(
        f"⚠ — меньше {MIN_VARIANT_UNITS} событий, где тег поставил хоть один проход: "
        "альфа на такой базе неустойчива, читать как сырые счётчики."
    )
    return "\n".join(lines)
