"""Замер панели источников для этапа 2 (STAGE2.md).

Панель нельзя выбрать по памяти: у изданий разный объём, разная глубина ленты и
разная надёжность разметки дат. `--probe` отвечает на четыре вопроса по каждому
кандидату — отдаёт ли лента что-нибудь, сколько материалов в ней помещается,
какой отрезок времени она покрывает и парсятся ли даты.

Адреса лент угадывать нельзя, поэтому на каждый источник заведён список кандидатов:
проверяются по очереди, в отчёт идёт первый работающий. Не найденный адрес — тоже
результат замера, а не повод тихо выкинуть источник из панели.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

# Кандидаты в панель из config.yaml. Несколько адресов на источник: у разных CMS
# лента лежит по разным путям, и какой живой — выясняется замером.
CANDIDATES: dict[str, list[str]] = {
    "tengrinews": ["https://tengrinews.kz/rss/", "https://tengrinews.kz/rss.rss"],
    "informburo": ["https://informburo.kz/rss", "https://informburo.kz/rss/all.rss"],
    "vlast": ["https://vlast.kz/rss", "https://vlast.kz/feed/"],
    "azattyq": ["https://rus.azattyq.org/api/zrqiteuuir", "https://rus.azattyq.org/rss"],
    "ulysmedia": ["https://ulysmedia.kz/rss/", "https://ulysmedia.kz/feed/"],
}

UA = "freedom-monitor/1.0 (research; contact via repository)"


@dataclass
class Probe:
    name: str
    url: str | None = None
    status: str = "не проверялся"
    n_items: int = 0
    n_dated: int = 0
    oldest: datetime | None = None
    newest: datetime | None = None
    # результат по каждому кандидату: адреса падают по-разному, и «последняя попытка»
    # скрывает диагноз — блокировка это, отсутствие ленты или неполадка на стороне сайта
    tried: list[tuple[str, str]] = field(default_factory=list)

    @property
    def span_hours(self) -> float | None:
        if not (self.oldest and self.newest):
            return None
        return (self.newest - self.oldest).total_seconds() / 3600

    @property
    def per_day(self) -> float | None:
        """Материалов в сутки по глубине ленты. Ниже ~5 источник почти не влияет
        на дневную выборку; лента короче суток означает риск пропустить события
        при однократном ежедневном прогоне."""
        span = self.span_hours
        if not span or span < 1:
            return None
        return self.n_items / (span / 24)

    def ok(self) -> bool:
        return self.status == "ok"


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:  # RFC 822 — обычный формат pubDate в RSS
        return parsedate_to_datetime(text).astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _items(root: ET.Element) -> list[ET.Element]:
    """RSS кладёт материалы в <item>, Atom — в <entry>."""
    found = root.iter("item")
    items = list(found)
    if items:
        return items
    return [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "entry"]


def _item_date(item: ET.Element) -> datetime | None:
    for tag in ("pubDate", "published", "updated", "date"):
        for el in item.iter():
            if el.tag.rsplit("}", 1)[-1] == tag:
                dt = _parse_date(el.text)
                if dt:
                    return dt
    return None


def probe_source(name: str, urls: list[str], timeout: int = 15,
                 session: requests.Session | None = None) -> Probe:
    sess = session or requests.Session()
    result = Probe(name=name)
    for url in urls:
        try:
            resp = sess.get(url, timeout=timeout, headers={"User-Agent": UA})
        except requests.RequestException as exc:
            result.tried.append((url, f"сеть: {type(exc).__name__}"))
            continue
        if resp.status_code != 200:
            hint = " (блокировка)" if resp.status_code in (401, 403, 429) else ""
            result.tried.append((url, f"HTTP {resp.status_code}{hint}"))
            continue
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            body = "пустой ответ" if not resp.content else "не XML (вероятно, HTML-страница)"
            result.tried.append((url, body))
            continue

        items = _items(root)
        result.tried.append((url, "ok"))
        dates = [d for d in (_item_date(i) for i in items) if d]
        result.url, result.status = url, "ok"
        result.n_items, result.n_dated = len(items), len(dates)
        if dates:
            result.oldest, result.newest = min(dates), max(dates)
        return result
    result.status = result.tried[-1][1] if result.tried else "нет кандидатов"
    return result


def probe_all(candidates: dict[str, list[str]] | None = None, pause: float = 0.5) -> list[Probe]:
    sess = requests.Session()
    out = []
    for name, urls in (candidates or CANDIDATES).items():
        out.append(probe_source(name, urls, session=sess))
        time.sleep(pause)
    return out


def format_report(probes: list[Probe]) -> str:
    lines = [
        "Панель источников (замер, не выбор)",
        "",
        "| Источник | Статус | Материалов | С датой | Глубина ленты | ≈ в сутки |",
        "|---|---|---|---|---|---|",
    ]
    for p in probes:
        span = "—" if p.span_hours is None else f"{p.span_hours:.0f} ч"
        rate = "—" if p.per_day is None else f"{p.per_day:.0f}"
        lines.append(f"| {p.name} | {p.status} | {p.n_items or '—'} | "
                     f"{p.n_dated or '—'} | {span} | {rate} |")

    working = [p for p in probes if p.ok()]
    lines.append("")
    lines.append(f"Отвечают: {len(working)} из {len(probes)}.")

    problems = []
    for p in probes:
        if not p.ok():
            detail = "; ".join(f"{u} → {st}" for u, st in p.tried)
            problems.append(f"{p.name}: {detail}")
        elif p.n_dated < p.n_items:
            problems.append(f"{p.name}: дата не разобрана у {p.n_items - p.n_dated} из {p.n_items}")
        elif p.span_hours is not None and p.span_hours < 24:
            problems.append(f"{p.name}: лента короче суток ({p.span_hours:.0f} ч) — "
                            "однократный дневной прогон будет терять материалы")
    if problems:
        lines.append("")
        lines.append("Требует решения:")
        lines.extend(f"- {x}" for x in problems)
    return "\n".join(lines)
