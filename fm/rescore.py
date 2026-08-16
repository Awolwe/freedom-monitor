"""Пере-скоринг через patch-файл (METHODOLOGY.md §8).

Правило §8: смена рубрики/якорей → инкремент `prompt_version`, пере-скоринг только
в **новый** файл. Исходный ряд не трогается — он остаётся аудит-следом.

Формат патча — JSONL, одна строка на событие:

    {"id": "kz-2024-03-15-a", "op": "patch", "escape": ["conformity"],
     "scores": {"solidarity": 2}, "rationale": "...", "note": "почему изменено"}
    {"id": "kz-2022-07-01-x", "op": "drop", "note": "не подтвердилось веб-проверкой"}
    {"id": "kz-2025-06-23-a", "op": "add", "date": "...", "title": "...", ...}

`scores` — частичный: перечисленные оси перезаписываются, остальные сохраняются.
`escape` — полная замена списка (теги не мержатся, чтобы патч был читаем как решение).
Любое поле схемы Event можно перезаписать напрямую (date, significance, source_url, ...).
`op: "add"` несёт полное событие — так пропуски корпуса попадают в тот же аудит-след,
что и правки, а не появляются молча.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .aggregate import AXES, Event

# поля, которые патч задаёт особым образом, а не прямой перезаписью
_SPECIAL = {"id", "op", "note", "scores"}


class PatchError(ValueError):
    pass


@dataclass
class RescoreReport:
    patched: int = 0
    dropped: int = 0
    added: int = 0
    unchanged: int = 0
    no_op: list[str] = field(default_factory=list)      # патч ничего не изменил
    unknown_ids: list[str] = field(default_factory=list)  # патч на несуществующее событие
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"патчей применено: {self.patched}, добавлено: {self.added}, "
            f"удалено: {self.dropped}, без изменений: {self.unchanged}"
        )


def load_patches(path: Path) -> list[dict]:
    patches, seen = [], set()
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                p = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PatchError(f"{path.name}:{n}: не JSON ({exc})") from exc
            pid = p.get("id")
            if not pid:
                raise PatchError(f"{path.name}:{n}: нет поля id")
            if pid in seen:
                raise PatchError(f"{path.name}:{n}: дубль патча для {pid}")
            if p.get("op", "patch") not in ("patch", "drop", "add"):
                raise PatchError(f"{path.name}:{n}: неизвестный op={p['op']!r}")
            bad_axes = set(p.get("scores", {})) - set(AXES)
            if bad_axes:
                raise PatchError(f"{path.name}:{n}: неизвестные оси {sorted(bad_axes)}")
            seen.add(pid)
            patches.append(p)
    return patches


def apply_patch(event: Event, patch: dict, version: str) -> Event:
    """Возвращает новое событие; исходное не мутируется."""
    data = event.model_dump()
    data["scores"] = {**data["scores"], **patch.get("scores", {})}
    for key, value in patch.items():
        if key in _SPECIAL:
            continue
        if key not in data:
            raise PatchError(f"{patch['id']}: поле {key!r} отсутствует в схеме Event")
        data[key] = value
    data["prompt_version"] = version
    return Event.model_validate(data)


def build_added(patch: dict, version: str) -> Event:
    data = {k: v for k, v in patch.items() if k not in ("op", "note")}
    data.setdefault("month", data.get("date", "")[:7])
    data["prompt_version"] = version
    return Event.model_validate(data)


def rescore(events: list[Event], patches: list[dict], version: str) -> tuple[list[Event], RescoreReport]:
    existing = {e.id for e in events}
    adds = [p for p in patches if p.get("op") == "add"]
    by_id = {p["id"]: p for p in patches if p.get("op") != "add"}
    rep = RescoreReport()
    rep.unknown_ids = sorted(set(by_id) - existing)
    for p in adds:
        if p["id"] in existing:
            raise PatchError(f"{p['id']}: op=add для уже существующего события")
    out: list[Event] = []

    for ev in events:
        patch = by_id.get(ev.id)
        if patch is None:
            out.append(ev)
            rep.unchanged += 1
            continue
        if patch.get("op") == "drop":
            rep.dropped += 1
            if patch.get("note"):
                rep.notes.append(f"drop {ev.id}: {patch['note']}")
            continue
        new_ev = apply_patch(ev, patch, version)
        if new_ev.model_dump(exclude={"prompt_version"}) == ev.model_dump(exclude={"prompt_version"}):
            rep.no_op.append(ev.id)
        out.append(new_ev)
        rep.patched += 1

    for p in adds:
        out.append(build_added(p, version))
        rep.added += 1
        if p.get("note"):
            rep.notes.append(f"add {p['id']}: {p['note']}")

    return out, rep


def write_events(events: list[Event], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for ev in sorted(events, key=lambda e: (e.date, e.id)):
            f.write(json.dumps(ev.model_dump(), ensure_ascii=False) + "\n")
    tmp.replace(path)
    return path
