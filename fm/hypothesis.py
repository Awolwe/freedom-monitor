"""Проверяемость H1 (METHODOLOGY §6) и её мощность.

H1: пребывание в квадранте X+/Y− («среда освобождается, пространство возможностей
сжимается») предсказывает рост счётчиков механизмов бегства с лагом 1–3 месяца.

Здесь не проверка гипотезы, а вопрос раньше неё: **различима ли такая связь в принципе**
на рядах той длины и разреженности, которые есть. §6 назначает порог «≥12 месяцев живых
данных», но это число ниоткуда не выведено. Если для различимого эффекта нужно вчетверо
больше — это надо знать до того, как этап 2 построен.

Две методические детали, без которых ответ был бы неверным:

* **Нулевая модель — циклический сдвиг, а не перестановка.** И квадрант, и счётчики
  сильно автокоррелированы: квадрант держится сериями месяцев, всплески тегов идут
  кластерами. Случайная перестановка меток разрушает эту структуру и завышает значимость —
  тест начинает находить связь там, где есть только общая инерция обоих рядов. Сдвиг
  сохраняет автокорреляцию каждого ряда и рвёт только их взаимную привязку.
* **Счётчик тегов — редкое пуассоновское событие** (в ретро-ряде среднее около 0.33
  на месяц, ненулевых месяцев 10 из 55). Мощность считается симуляцией на пуассоновской
  модели, а не формулой для нормального приближения, которое здесь неприменимо.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .aggregate import AXES, MECHANISMS

LAGS = (1, 2, 3)


# --- извлечение рядов ------------------------------------------------------

def quadrant_series(rows: list[dict], x: str = "freedom_from", y: str = "freedom_for") -> list[int]:
    """1, если месяц в квадранте X+/Y−. Требует add_levels."""
    for axis in (x, y):
        if axis not in AXES:
            raise ValueError(f"неизвестная ось {axis!r}")
    return [int(r[f"level_{x}"] > 0 and r[f"level_{y}"] < 0) for r in rows]


def escape_series(rows: list[dict]) -> list[int]:
    return [sum(r[f"esc_{m}"] for m in MECHANISMS) for r in rows]


def lagged_outcome(counts: list[int], lags=LAGS) -> list[float | None]:
    """Сумма счётчиков в окне t+lags. None там, где окно выходит за ряд."""
    n = len(counts)
    out: list[float | None] = []
    for t in range(n):
        window = [counts[t + l] for l in lags if t + l < n]
        out.append(sum(window) if len(window) == len(lags) else None)
    return out


# --- тест ------------------------------------------------------------------

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def statistic(pred: list[int], outcome: list[float | None]) -> float | None:
    """Разность средних: окно после месяца в квадранте минус после месяца вне его."""
    a = [o for p, o in zip(pred, outcome) if o is not None and p == 1]
    b = [o for p, o in zip(pred, outcome) if o is not None and p == 0]
    return None if not a or not b else _mean(a) - _mean(b)


@dataclass
class TestResult:
    observed: float | None
    p_value: float | None
    n_usable: int
    n_predictor: int
    note: str = ""


def circular_shift_test(pred: list[int], outcome: list[float | None],
                        rng: random.Random) -> TestResult:
    """Нулевое распределение — все циклические сдвиги предиктора."""
    obs = statistic(pred, outcome)
    usable = sum(1 for o in outcome if o is not None)
    if obs is None:
        return TestResult(None, None, usable, sum(pred), "предиктор не разделяет месяцы")

    n = len(pred)
    null = []
    for k in range(1, n):  # сдвиг 0 — это сами данные, его исключаем
        shifted = pred[k:] + pred[:k]
        s = statistic(shifted, outcome)
        if s is not None:
            null.append(s)
    if not null:
        return TestResult(obs, None, usable, sum(pred), "нулевое распределение пусто")

    # односторонняя проверка: H1 предсказывает рост
    extreme = sum(1 for s in null if s >= obs)
    return TestResult(obs, (extreme + 1) / (len(null) + 1), usable, sum(pred))


# --- мощность --------------------------------------------------------------

def _poisson(lam: float, rng: random.Random) -> int:
    """Кнут. Для малых λ, которые здесь и нужны."""
    if lam <= 0:
        return 0
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def simulate_once(n_months: int, base_rate: float, effect: float,
                  quad_share: float, run_len: float, rng: random.Random) -> float | None:
    """Один синтетический ряд. effect — во сколько раз растёт частота тегов
    после месяца в квадранте (0 = гипотеза неверна). run_len задаёт серийность
    квадранта: реальный предиктор держится подряд, а не разбросан по месяцам."""
    pred, state = [], 0
    p_enter = quad_share / max(run_len * (1 - quad_share), 1e-9)
    p_exit = 1 / max(run_len, 1e-9)
    for _ in range(n_months):
        state = (rng.random() > p_exit) if state else (rng.random() < p_enter)
        pred.append(int(state))

    counts = []
    for t in range(n_months):
        triggered = any(t - l >= 0 and pred[t - l] for l in LAGS)
        counts.append(_poisson(base_rate * (1 + effect * triggered), rng))

    res = circular_shift_test(pred, lagged_outcome(counts), rng)
    return res.p_value


def power(n_months: int, base_rate: float, effect: float, quad_share: float,
          run_len: float, trials: int, seed: int, alpha: float = 0.05) -> float:
    rng = random.Random(seed)
    hits = tried = 0
    for _ in range(trials):
        p = simulate_once(n_months, base_rate, effect, quad_share, run_len, rng)
        if p is None:
            continue
        tried += 1
        hits += p < alpha
    return hits / tried if tried else 0.0


def months_needed(base_rate: float, effect: float, quad_share: float, run_len: float,
                  trials: int, seed: int, target: float = 0.8,
                  candidates=(12, 24, 36, 48, 60, 90, 120, 180, 240)) -> int | None:
    for n in candidates:
        if power(n, base_rate, effect, quad_share, run_len, trials, seed) >= target:
            return n
    return None
