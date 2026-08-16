"""Тесты пере-скоринга (METHODOLOGY.md §8): исходный ряд неприкосновенен."""
from __future__ import annotations

import json

import pytest

from fm.aggregate import Event, load_events
from fm.rescore import PatchError, apply_patch, load_patches, rescore, write_events


def mk(eid="e1", date="2024-03-15", **over) -> Event:
    base = dict(
        id=eid, date=date, month=date[:7], title=f"t-{eid}", category="политика",
        scores={"freedom_from": 0, "freedom_for": 0, "solidarity": 0, "hope": 0},
        significance=2, rationale="r",
    )
    base.update(over)
    return Event(**base)


def patchfile(tmp_path, lines: list[dict | str], name="p.jsonl"):
    p = tmp_path / name
    p.write_text(
        "\n".join(x if isinstance(x, str) else json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
        encoding="utf-8",
    )
    return p


# --- load_patches ----------------------------------------------------------

def test_load_patches_skips_blanks_and_comments(tmp_path):
    p = patchfile(tmp_path, [{"id": "a"}, "", "// заметка кодировщика", {"id": "b"}])
    assert [x["id"] for x in load_patches(p)] == ["a", "b"]


def test_duplicate_patch_id_rejected(tmp_path):
    p = patchfile(tmp_path, [{"id": "a"}, {"id": "a"}])
    with pytest.raises(PatchError, match="дубль"):
        load_patches(p)


def test_patch_without_id_rejected(tmp_path):
    with pytest.raises(PatchError, match="нет поля id"):
        load_patches(patchfile(tmp_path, [{"scores": {"hope": 1}}]))


def test_unknown_op_rejected(tmp_path):
    with pytest.raises(PatchError, match="неизвестный op"):
        load_patches(patchfile(tmp_path, [{"id": "a", "op": "merge"}]))


def test_unknown_axis_in_patch_rejected(tmp_path):
    """Опечатка в имени оси не должна тихо проходить как ничего не делающий патч."""
    with pytest.raises(PatchError, match="freedon_from"):
        load_patches(patchfile(tmp_path, [{"id": "a", "scores": {"freedon_from": -2}}]))


def test_malformed_json_reports_line_number(tmp_path):
    with pytest.raises(PatchError, match="p.jsonl:2"):
        load_patches(patchfile(tmp_path, [{"id": "a"}, "{не json"]))


# --- apply_patch -----------------------------------------------------------

def test_scores_patch_is_partial_untouched_axes_survive():
    ev = mk(scores={"freedom_from": -2, "freedom_for": 0, "solidarity": 0, "hope": -1})
    out = apply_patch(ev, {"id": "e1", "scores": {"solidarity": 2}}, "1.1")
    assert out.scores.solidarity == 2
    assert out.scores.freedom_from == -2 and out.scores.hope == -1


def test_escape_patch_replaces_list_not_merges():
    ev = mk(escape=["conformity"])
    out = apply_patch(ev, {"id": "e1", "escape": ["authoritarianism"]}, "1.1")
    assert out.escape == ["authoritarianism"]


def test_patch_can_clear_escape_with_empty_list():
    out = apply_patch(mk(escape=["conformity"]), {"id": "e1", "escape": []}, "1.1")
    assert out.escape == []


def test_apply_patch_bumps_prompt_version():
    assert apply_patch(mk(), {"id": "e1"}, "1.1").prompt_version == "1.1"


def test_apply_patch_does_not_mutate_source_event():
    ev = mk(scores={"freedom_from": -2, "freedom_for": 0, "solidarity": 0, "hope": 0})
    apply_patch(ev, {"id": "e1", "scores": {"freedom_from": 3}}, "1.1")
    assert ev.scores.freedom_from == -2 and ev.prompt_version == "1.0"


def test_patch_can_set_grounding_fields():
    out = apply_patch(
        mk(), {"id": "e1", "source_url": "https://example.kz/a", "verified": "confirmed"}, "1.1"
    )
    assert out.source_url == "https://example.kz/a" and out.verified == "confirmed"


def test_patch_to_unknown_field_rejected():
    with pytest.raises(PatchError, match="отсутствует в схеме"):
        apply_patch(mk(), {"id": "e1", "sentiment": 5}, "1.1")


def test_patch_producing_invalid_event_rejected():
    with pytest.raises(Exception):
        apply_patch(mk(), {"id": "e1", "scores": {"hope": 9}}, "1.1")


def test_patch_changing_date_must_keep_month_consistent():
    """month↔date проверяется и на пере-скоринге, а не только при первичной загрузке."""
    with pytest.raises(Exception, match="не совпадает"):
        apply_patch(mk(date="2024-03-15"), {"id": "e1", "date": "2024-05-02"}, "1.1")
    ok = apply_patch(mk(date="2024-03-15"), {"id": "e1", "date": "2024-05-02", "month": "2024-05"}, "1.1")
    assert ok.date == "2024-05-02"


# --- rescore ---------------------------------------------------------------

def test_unpatched_events_pass_through_with_original_version():
    out, rep = rescore([mk("a"), mk("b")], [{"id": "a", "scores": {"hope": 1}}], "1.1")
    versions = {e.id: e.prompt_version for e in out}
    assert versions == {"a": "1.1", "b": "1.0"}
    assert rep.patched == 1 and rep.unchanged == 1


def test_drop_removes_event_and_records_note():
    out, rep = rescore([mk("a"), mk("b")], [{"id": "a", "op": "drop", "note": "не подтвердилось"}], "1.1")
    assert [e.id for e in out] == ["b"]
    assert rep.dropped == 1 and "не подтвердилось" in rep.notes[0]


def test_patch_for_unknown_id_is_reported_not_silently_ignored():
    _, rep = rescore([mk("a")], [{"id": "ghost", "scores": {"hope": 1}}], "1.1")
    assert rep.unknown_ids == ["ghost"]


def test_no_op_patch_is_flagged():
    """Патч, который ничего не меняет, — обычно опечатка в id или уже применённое решение."""
    _, rep = rescore([mk("a", scores={"freedom_from": 0, "freedom_for": 0, "solidarity": 0, "hope": 1})],
                     [{"id": "a", "scores": {"hope": 1}}], "1.1")
    assert rep.no_op == ["a"]


def test_add_appends_new_event_with_new_version():
    add = {"op": "add", "id": "new1", "date": "2025-06-23", "title": "амнистия",
           "category": "права и свободы",
           "scores": {"freedom_from": 2, "freedom_for": 0, "solidarity": 0, "hope": 1},
           "significance": 3, "rationale": "r", "origin": "web_research"}
    out, rep = rescore([mk("a")], [add], "1.4")
    assert rep.added == 1 and rep.patched == 0
    new = next(e for e in out if e.id == "new1")
    assert new.month == "2025-06", "month выводится из date, если не задан"
    assert new.prompt_version == "1.4"


def test_add_for_existing_id_rejected():
    add = {"op": "add", "id": "a", "date": "2024-03-15", "title": "t", "category": "политика",
           "scores": {"freedom_from": 0, "freedom_for": 0, "solidarity": 0, "hope": 0},
           "significance": 1}
    with pytest.raises(PatchError, match="уже существующего"):
        rescore([mk("a")], [add], "1.4")


def test_add_with_invalid_payload_rejected():
    add = {"op": "add", "id": "new1", "date": "2025-06-23", "title": "t", "category": "спорт",
           "scores": {"freedom_from": 0, "freedom_for": 0, "solidarity": 0, "hope": 0},
           "significance": 1}
    with pytest.raises(Exception):
        rescore([mk("a")], [add], "1.4")


def test_add_is_not_counted_as_unknown_id():
    add = {"op": "add", "id": "new1", "date": "2025-06-23", "title": "t", "category": "политика",
           "scores": {"freedom_from": 0, "freedom_for": 0, "solidarity": 0, "hope": 0},
           "significance": 1}
    _, rep = rescore([mk("a")], [add], "1.4")
    assert rep.unknown_ids == []


def test_rescore_leaves_input_list_and_events_untouched():
    events = [mk("a")]
    rescore(events, [{"id": "a", "op": "drop"}], "1.1")
    assert len(events) == 1 and events[0].prompt_version == "1.0"


# --- запись ----------------------------------------------------------------

def test_write_events_sorts_by_date_and_roundtrips(tmp_path):
    out = tmp_path / "out.jsonl"
    write_events([mk("b", "2024-05-01"), mk("a", "2024-03-01")], out)
    reloaded, errors = load_events(out)
    assert errors == []
    assert [e.id for e in reloaded] == ["a", "b"]


def test_write_events_is_atomic_no_tmp_left_behind(tmp_path):
    out = tmp_path / "out.jsonl"
    write_events([mk("a")], out)
    assert list(tmp_path.glob("*.tmp")) == []
