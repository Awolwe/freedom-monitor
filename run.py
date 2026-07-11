"""Freedom Monitor — оркестрация.

Этап 1 (ретроспектива):
    python run.py --rebuild-dashboard      # пересборка дашборда из data/
    python run.py --validate               # только валидация events-файла
    python run.py --market                 # дотянуть историю USD/KZT из API НБ РК

Этап 2 (живой пайплайн) добавит: --probe, --dry-run, полный прогон, --rescore, --calibrate.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fm import aggregate, dashboard, market  # noqa: E402

EVENTS_FILE = ROOT / "data" / "events_backfill.jsonl"


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate() -> list[aggregate.Event]:
    if not EVENTS_FILE.exists():
        sys.exit(f"Нет файла событий: {EVENTS_FILE}")
    events, errors = aggregate.load_events(EVENTS_FILE)
    if errors:
        print(f"ОШИБКИ ВАЛИДАЦИИ ({len(errors)}):")
        for e in errors[:30]:
            print("  -", e)
        sys.exit(1)
    q = aggregate.data_quality(events)
    print(f"События: {q['n_events']}, месяцев: {len(q['months'])} "
          f"({q['months'][0]}..{q['months'][-1]}), significance-3: {q['sig3']}")
    print("Доля нулей по осям:", {k: round(v, 2) for k, v in q["zero_share"].items()})
    if q["thin_months"]:
        print("Тонкие месяцы (<3 событий):", ", ".join(q["thin_months"]))
    return events


def main() -> None:
    p = argparse.ArgumentParser(description="Freedom Monitor")
    p.add_argument("--rebuild-dashboard", action="store_true")
    p.add_argument("--validate", action="store_true")
    p.add_argument("--market", action="store_true")
    args = p.parse_args()

    cfg = load_config()

    if args.market:
        series = market.update_history()
        print(f"USD/KZT: {len(series)} точек")
        return

    events = validate()
    if args.validate:
        return

    # по умолчанию (и при --rebuild-dashboard) — пересборка
    out = dashboard.render(events, cfg)
    print(f"Дашборд: {out}")


if __name__ == "__main__":
    main()
