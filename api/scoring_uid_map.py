"""
Извлечение УИД (GUID-формат) из детальных карточек договоров Скоринг
Бюро и сопоставление с contract_id из сводной таблицы.

Зачем отдельно от scoring_adapter.py: сводная таблица
(parse_contracts_summary) содержит только числовой ID договора
("ID договора"), а не сам УИД -- УИД в GUID-формате есть только в
детальных карточках ("ПОДРОБНАЯ ИНФОРМАЦИЯ ПО ДОГОВОРАМ"), рядом с
тем же ID договора. Проверено на реальном отчёте: 98 из 98 сопоставлений
contract_id -> УИД, что и позволяет затем сверить УИД между ОКБ и
Скоринг Бюро (см. merge_uids() и debug_dump.py).
"""
from __future__ import annotations
import re

from layout_utils import get_full_text, find_section

CONTRACT_ID_TO_UID_RE = re.compile(
    r"ID договора\s+(\d{15,20}).{0,200}?"
    r"Уникальный идентификатор договора\s*\n?\(УИД\)?\s*([0-9a-f-]{6,})\s*\n?([0-9a-f-]{4,})?",
    re.S | re.I,
)


def extract_scoring_uid_map(pdf_path: str) -> dict[str, str]:
    """contract_id -> УИД (полный, со склеенными частями при переносе строки)."""
    text = get_full_text(pdf_path)
    section = find_section(text, "ПОДРОБНАЯ ИНФОРМАЦИЯ ПО ДОГОВОРАМ",
                            "ИНФОРМАЦИОННАЯ ЧАСТЬ КРЕДИТНОЙ ИСТОРИИ", occurrence=-1)
    result = {}
    for m in CONTRACT_ID_TO_UID_RE.finditer(section):
        contract_id, uid_part1, uid_part2 = m.groups()
        result[contract_id] = uid_part1 + (uid_part2 or "")
    return result


def merge_uids(okb_uids: set[str], scoring_uid_map: dict[str, str]) -> list[str]:
    """УИД, встречающиеся и в ОКБ, и в Скоринг Бюро (точное совпадение строки)."""
    return sorted(okb_uids & set(scoring_uid_map.values()))


if __name__ == "__main__":
    import sys
    m = extract_scoring_uid_map(sys.argv[1])
    print(f"contract_id -> УИД сопоставлений: {len(m)} (контроль: 98)")
