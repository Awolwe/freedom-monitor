"""«Утренний взгляд» (макрофон): история официального курса USD/KZT из API НБ РК.

Ретро-режим: 1-е число каждого месяца с 2022-01 по текущий месяц + сегодняшняя дата.
Ошибки сети не блокируют сборку дашборда — модуль просто оставляет прежний файл.
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import requests

RATES_URL = "https://nationalbank.kz/rss/get_rates.cfm"
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "market_daily.jsonl"


def _fetch_usd(day: date, timeout: int = 20) -> float | None:
    """Официальный курс USD на дату (НБ РК публикует курс «на дату», фиксинг t-1)."""
    params = {"fdate": day.strftime("%d.%m.%Y")}
    resp = requests.get(RATES_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    for item in root.iter("item"):
        if (item.findtext("title") or "").strip() == "USD":
            return float(item.findtext("description").strip())
    return None


def _month_firsts(start: date, end: date) -> list[date]:
    days, cur = [], date(start.year, start.month, 1)
    while cur <= end:
        days.append(cur)
        cur = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return days


def load_series() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    with open(DATA_FILE, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def update_history(start: date = date(2022, 1, 1), end: date | None = None, pause: float = 0.4) -> list[dict]:
    """Дотягивает недостающие месячные точки; уже сохранённые не перезапрашивает."""
    end = end or date.today()
    have = {row["date"] for row in load_series()}
    targets = _month_firsts(start, end) + [end]
    rows = load_series()
    for day in targets:
        key = day.isoformat()
        if key in have:
            continue
        try:
            rate = _fetch_usd(day)
        except Exception as exc:  # сеть/парсинг — не блокер
            print(f"  [market] {key}: пропуск ({exc})")
            continue
        if rate is not None:
            rows.append({"date": key, "usdkzt_official": rate})
            have.add(key)
            print(f"  [market] {key}: {rate}")
        time.sleep(pause)
    rows.sort(key=lambda r: r["date"])
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(DATA_FILE)
    return rows


if __name__ == "__main__":
    series = update_history()
    print(f"Всего точек: {len(series)}")
