"""Freedom Monitor — оркестрация.

Этап 1 (ретроспектива):
    python run.py --rebuild-dashboard      # пересборка дашборда из data/
    python run.py --validate               # только валидация events-файла
    python run.py --market                 # дотянуть историю USD/KZT из API НБ РК
    python run.py --rescore PATCH.jsonl --out data/events_v1.1.jsonl --version 1.1
    python run.py --reliability A.jsonl B.jsonl   # согласие двух проходов кодирования

Активный events-файл берётся из config.yaml (`data.events_file`); переопределяется `--events`.

Этап 2 (живой пайплайн) добавит: --probe, --dry-run, полный прогон, --calibrate. См. STAGE2.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# При перенаправлении вывода Windows отдаёт stdout в кодировке ANSI (cp1251),
# и печать «→» или «≠» роняет прогон. Консоль Windows и так работает в utf-8
# (PEP 528), поэтому признак «нужно чинить» — кодировка, а не isatty():
# NUL на Windows отвечает isatty() == True и мимо такой проверки проходит.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and (_stream.encoding or "").lower() != "utf-8":
        _stream.reconfigure(encoding="utf-8", errors="replace")

from fm import aggregate, dashboard, market, reliability, rescore  # noqa: E402

DEFAULT_EVENTS = ROOT / "data" / "events_backfill.jsonl"


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def events_path(cfg: dict, override: str | None) -> Path:
    if override:
        return Path(override)
    configured = (cfg.get("data") or {}).get("events_file")
    return ROOT / configured if configured else DEFAULT_EVENTS


def validate(path: Path) -> list[aggregate.Event]:
    if not path.exists():
        sys.exit(f"Нет файла событий: {path}")
    events, errors = aggregate.load_events(path)
    if errors:
        print(f"ОШИБКИ ВАЛИДАЦИИ ({len(errors)}):")
        for e in errors[:30]:
            print("  -", e)
        sys.exit(1)
    q = aggregate.data_quality(events)
    print(f"Файл: {path.name}")
    print(f"События: {q['n_events']}, месяцев: {len(q['months'])} "
          f"({q['months'][0]}..{q['months'][-1]}), significance-3: {q['sig3']}")
    print("Доля нулей по осям:", {k: round(v, 2) for k, v in q["zero_share"].items()},
          "(кодбук §0.2: 0.60-0.80 для freedom_from и hope, 0.85-0.92 для freedom_for и solidarity)")
    print(f"Событий без единой ненулевой оси: {q['all_zero_share']:.0%}")
    print(f"Теги escape: {q['escape_share']:.0%} событий (кодбук §3 ждёт 0.05-0.10),"
          f" счётчики {q['escape_counts']}")
    print(f"С источником (URL): {q['grounded_share']:.0%}, статусы {q['verified_counts']}")
    if q["thin_months"]:
        print("Тонкие месяцы (<3 событий):", ", ".join(q["thin_months"]))
    return events


def cmd_rescore(args, cfg: dict) -> None:
    src = events_path(cfg, args.events)
    events, errors = aggregate.load_events(src)
    if errors:
        sys.exit(f"Исходный файл не валиден ({len(errors)} ошибок), пере-скоринг отменён")
    patches = rescore.load_patches(Path(args.rescore))
    out_events, rep = rescore.rescore(events, patches, args.version)
    if rep.unknown_ids:
        sys.exit(f"Патчи для несуществующих id ({len(rep.unknown_ids)}): {rep.unknown_ids[:5]}")
    if rep.no_op:
        print(f"ВНИМАНИЕ: патчи без эффекта ({len(rep.no_op)}): {rep.no_op[:5]}")
    out = rescore.write_events(out_events, Path(args.out))
    print(f"{src.name} → {out.name} (prompt_version {args.version})")
    print(rep.summary())
    for note in rep.notes:
        print("  ", note)
    print()
    validate(out)


def cmd_reliability(args, cfg: dict) -> None:
    base = events_path(cfg, args.events)
    report = reliability.compare_files(base, Path(args.reliability[0]), Path(args.reliability[1]))
    print(reliability.format_report(report))

    # Снимок в фиксированном месте: дашборд показывает измеренные цифры, а не пересчитывает
    # их против текущего ряда — после адъюдикации это дало бы искусственные 100% (RELIABILITY.md).
    snapshot = ROOT / "data" / "reliability" / "report.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nСнимок: {snapshot.relative_to(ROOT)}")

    if args.reliability_out:
        Path(args.reliability_out).write_text(
            reliability.format_report(report, markdown=True), encoding="utf-8"
        )
        print(f"Отчёт: {args.reliability_out}")


def main() -> None:
    p = argparse.ArgumentParser(description="Freedom Monitor")
    p.add_argument("--rebuild-dashboard", action="store_true")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--market", action="store_true")
    p.add_argument("--events", metavar="FILE", help="events-файл вместо указанного в config.yaml")
    p.add_argument("--rescore", metavar="PATCH", help="применить patch-файл (METHODOLOGY §8)")
    p.add_argument("--out", metavar="FILE", help="куда писать результат --rescore")
    p.add_argument("--version", default="", metavar="V", help="новый prompt_version для --rescore")
    p.add_argument("--reliability", nargs=2, metavar=("PASS_A", "PASS_B"),
                   help="согласие двух независимых проходов кодирования")
    p.add_argument("--reliability-out", metavar="FILE", help="markdown-отчёт надёжности")
    args = p.parse_args()

    cfg = load_config()

    if args.market:
        series = market.update_history()
        print(f"USD/KZT: {len(series)} точек")
        return

    if args.rescore:
        if not args.out or not args.version:
            sys.exit("--rescore требует --out FILE и --version V (METHODOLOGY §8)")
        return cmd_rescore(args, cfg)

    if args.reliability:
        return cmd_reliability(args, cfg)

    events = validate(events_path(cfg, args.events))
    if args.validate:
        return

    # по умолчанию (и при --rebuild-dashboard) — пересборка
    out = dashboard.render(events, cfg)
    print(f"Дашборд: {out}")


if __name__ == "__main__":
    main()
