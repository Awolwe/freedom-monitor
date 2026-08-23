"""Тесты замера панели источников. Сеть не трогаем — сессия подставная."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import requests

from fm.probe import Probe, format_report, probe_source

RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <item><title>раз</title><pubDate>Sat, 16 Aug 2026 10:00:00 +0000</pubDate></item>
  <item><title>два</title><pubDate>Fri, 15 Aug 2026 10:00:00 +0000</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>раз</title><published>2026-08-16T10:00:00Z</published></entry>
  <entry><title>два</title><published>2026-08-14T10:00:00Z</published></entry>
</feed>"""

NO_DATES = """<?xml version="1.0"?><rss><channel>
  <item><title>раз</title></item><item><title>два</title></item>
</channel></rss>"""


class FakeResponse:
    def __init__(self, status=200, body=b""):
        self.status_code = status
        self.content = body


class FakeSession:
    """Отдаёт заранее заданный ответ на каждый адрес; неизвестный адрес — 404."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append(url)
        r = self.routes.get(url, FakeResponse(404, "<html>нет</html>".encode()))
        if isinstance(r, Exception):
            raise r
        return r


def test_parses_rss_and_measures_depth():
    sess = FakeSession({"http://a/rss": FakeResponse(200, RSS.encode())})
    p = probe_source("a", ["http://a/rss"], session=sess)
    assert p.ok() and p.n_items == 2 and p.n_dated == 2
    assert p.span_hours == pytest.approx(24.0)
    assert p.per_day == pytest.approx(2.0)


def test_parses_atom_entries_too():
    sess = FakeSession({"http://a/feed": FakeResponse(200, ATOM.encode())})
    p = probe_source("a", ["http://a/feed"], session=sess)
    assert p.ok() and p.n_items == 2 and p.n_dated == 2


def test_falls_through_to_second_candidate():
    sess = FakeSession({
        "http://a/1": FakeResponse(404, b"<html/>"),
        "http://a/2": FakeResponse(200, RSS.encode()),
    })
    p = probe_source("a", ["http://a/1", "http://a/2"], session=sess)
    assert p.ok() and p.url == "http://a/2"
    assert sess.calls == ["http://a/1", "http://a/2"]


def test_stops_at_first_working_candidate():
    sess = FakeSession({
        "http://a/1": FakeResponse(200, RSS.encode()),
        "http://a/2": FakeResponse(200, ATOM.encode()),
    })
    probe_source("a", ["http://a/1", "http://a/2"], session=sess)
    assert sess.calls == ["http://a/1"], "второй адрес дёргать незачем"


def test_records_status_per_candidate_not_just_the_last():
    """Диагноз теряется, если помнить только последнюю попытку: блокировка и
    отсутствие ленты требуют разных действий."""
    sess = FakeSession({
        "http://a/1": FakeResponse(403, b"<html>forbidden</html>"),
        "http://a/2": FakeResponse(404, b"<html/>"),
    })
    p = probe_source("a", ["http://a/1", "http://a/2"], session=sess)
    assert not p.ok()
    statuses = dict(p.tried)
    assert "блокировка" in statuses["http://a/1"]
    assert "404" in statuses["http://a/2"]


def test_network_failure_is_recorded_not_raised():
    sess = FakeSession({"http://a/1": requests.ConnectTimeout()})
    p = probe_source("a", ["http://a/1"], session=sess)
    assert not p.ok() and "ConnectTimeout" in p.tried[0][1]


def test_html_response_is_not_mistaken_for_a_feed():
    sess = FakeSession({"http://a/1": FakeResponse(200, b"<!DOCTYPE html><html><body>hi")})
    p = probe_source("a", ["http://a/1"], session=sess)
    assert not p.ok() and "не XML" in p.tried[0][1]


def test_empty_body_is_distinguished_from_html():
    sess = FakeSession({"http://a/1": FakeResponse(200, b"")})
    p = probe_source("a", ["http://a/1"], session=sess)
    assert p.tried[0][1] == "пустой ответ"


def test_feed_without_dates_is_flagged():
    sess = FakeSession({"http://a/1": FakeResponse(200, NO_DATES.encode())})
    p = probe_source("a", ["http://a/1"], session=sess)
    assert p.ok() and p.n_items == 2 and p.n_dated == 0
    assert p.span_hours is None and p.per_day is None
    assert "дата не разобрана" in format_report([p])


def test_short_feed_is_flagged_as_risky_for_daily_run():
    p = Probe(name="a", status="ok", n_items=30, n_dated=30,
              oldest=datetime(2026, 8, 16, 0, tzinfo=timezone.utc),
              newest=datetime(2026, 8, 16, 6, tzinfo=timezone.utc))
    assert "короче суток" in format_report([p])


def test_report_counts_working_sources():
    good = Probe(name="ok1", status="ok", n_items=10, n_dated=10,
                 oldest=datetime(2026, 8, 14, tzinfo=timezone.utc),
                 newest=datetime(2026, 8, 16, tzinfo=timezone.utc))
    bad = Probe(name="bad", status="HTTP 403", tried=[("http://x", "HTTP 403 (блокировка)")])
    text = format_report([good, bad])
    assert "Отвечают: 1 из 2" in text
    assert "http://x → HTTP 403" in text
