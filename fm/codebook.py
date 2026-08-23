"""Сборка кодбука для кодировщика из METHODOLOGY.md.

Кодировщик получает инструкцию по кодированию — и только её. Из текста вырезаны:

* **§6 (гипотеза)** — METHODOLOGY §6 прямо требует не показывать её кодировщику:
  защита от самоподтверждения. Это же правило действует для промпта скоринга в этапе 2.
* **абзацы с результатами прошлых проходов** — «доля тегов вышла 6%», «нулей столько-то».
  Второй кодировщик, прочитавший их, заякорится на цифру вместо кодбука, и замер
  надёжности превратится в замер внушаемости.

Вырезаются целые абзацы по ведущему тексту, а не по номерам строк: правка METHODOLOGY
не должна тихо ломать сборку.
"""
from __future__ import annotations

import re
from pathlib import Path

METHODOLOGY = Path(__file__).resolve().parent.parent / "METHODOLOGY.md"

# Абзацы-результаты: ведущий фрагмент строки, с которой абзац начинается.
_RESULT_PARAGRAPHS = (
    "**Калибровка по факту",
    "**Риск круговой логики",
    "**Замеры смещения",
)

# Предложение внутри §0.2, называющее измеренные доли нулей.
_MEASURED_ZERO_SHARE = re.compile(
    r"\n\s*Ожидаемая доля нулей уточнена по факту.*?(?=\n\n|\Z)", re.DOTALL
)


def _sections_up_to_aggregation(text: str) -> str:
    """§0-§3: рамка, оси, значимость, теги. Всё от §4 и ниже — не для кодировщика."""
    cut = text.find("\n## 4.")
    if cut == -1:
        raise ValueError("METHODOLOGY.md: не найден раздел «## 4.» — структура изменилась")
    return text[:cut]


def _drop_paragraph(text: str, lead: str) -> str:
    """Удаляет абзац, начинающийся с lead, вместе с его продолжением."""
    start = text.find(lead)
    if start == -1:
        return text
    end = text.find("\n\n", start)
    return text[:start] + (text[end + 2:] if end != -1 else "")


def coder_codebook(path: Path | None = None) -> str:
    text = (path or METHODOLOGY).read_text(encoding="utf-8")
    body = _sections_up_to_aggregation(text)
    for lead in _RESULT_PARAGRAPHS:
        body = _drop_paragraph(body, lead)
    body = _MEASURED_ZERO_SHARE.sub("", body)
    return body.rstrip() + "\n"


def leaks(codebook: str) -> list[str]:
    """Что не должно попасть кодировщику. Пустой список — можно отдавать."""
    found = []
    if "§6" in codebook or "гипотеза-предок" in codebook.lower():
        found.append("ссылка на предрегистрированную гипотезу")
    if re.search(r"дал\s+\*?\*?\d+%|вышл[оа]\s+\d+%", codebook):
        found.append("измеренная доля из прошлого прохода")
    for lead in _RESULT_PARAGRAPHS:
        if lead in codebook:
            found.append(f"абзац с результатами: {lead}")
    return found


if __name__ == "__main__":
    cb = coder_codebook()
    problems = leaks(cb)
    print(f"кодбук кодировщика: {len(cb)} символов, утечек: {len(problems)}")
    for p in problems:
        print("  -", p)
