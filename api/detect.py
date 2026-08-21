"""
Реестр detect(bureau, format_version) вместо одного if/elif в
parse-bki.py. Новый формат = новая функция sniff() в этом файле,
ноль правок в существующих sniff() и адаптерах.

Каждая запись реестра -- (bureau_code, format_version, sniff_fn).
Порядок важен: первое совпадение побеждает, поэтому более специфичные
проверки (current/2026) стоят раньше более общих (legacy), если между
ними возможно пересечение маркеров.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

import pymupdf


@dataclass(frozen=True)
class FormatMatch:
    bureau: str            # "okb" | "scoring" | "nbki"
    format_version: str    # "v1" | "legacy" | "2026" | "modern" | ...


def probe_text(pdf_path: str, pages: int = 12) -> str:
    doc = pymupdf.open(pdf_path)
    try:
        return "\n".join(doc[i].get_text() for i in range(min(pages, len(doc))))
    finally:
        doc.close()


def _sniff_nbki(probe_upper: str) -> bool:
    return ("КРЕДИТНЫЙ ОТЧЕТ ДЛЯ СУБЪЕКТА" in probe_upper
            and ("ОБРАЩАЙТЕСЬ В НБКИ" in probe_upper or "SYSTEM VERSION:" in probe_upper))


def _sniff_scoring_legacy(probe_upper: str) -> bool:
    return ("СВОДНАЯ ИНФОРМАЦИЯ ПО ДОГОВОРАМ ЗАЙМА" in probe_upper
            or ("ПОДРОБНАЯ ИНФОРМАЦИЯ ПО ДОГОВОРАМ" in probe_upper and "КОД ОТЧЕТА" in probe_upper))


def _sniff_scoring_2026(probe_upper: str) -> bool:
    """
    ЗАГЛУШКА -- маркеры ниже НЕ подтверждены на реальном PDF формата
    2026 (нет фикстуры Богачёва/Калашниковой). Не включать в продакшен-
    реестр, пока sniff() не проверен на настоящем файле: ложное
    совпадение здесь хуже, чем громкая ошибка "формат не распознан".
    Раздел-маркеры из брифа для ориентира при последующей проверке:
    "Действующие кредиты", "Закрытые кредиты", "Заявки на кредиты",
    "Кто интересовался кредитной историей".
    """
    markers = ("ДЕЙСТВУЮЩИЕ КРЕДИТЫ", "ЗАКРЫТЫЕ КРЕДИТЫ", "КТО ИНТЕРЕСОВАЛСЯ КРЕДИТНОЙ ИСТОРИЕЙ")
    return sum(1 for m in markers if m in probe_upper) >= 2


def _sniff_okb(probe_upper: str) -> bool:
    return ("КРЕДИСТОРИЯ" in probe_upper or "ОБЪЕДИНЕННОГО КРЕДИТНОГО БЮРО" in probe_upper
            or 'АО «ОКБ»' in probe_upper or 'АО "ОКБ"' in probe_upper)


def _sniff_kredit_info(probe_upper: str) -> bool:
    """
    Проверено на реальном отчёте (Постельный И.А., 21 стр.): колонтитул
    "ООО «БКИ КРЕДИТИНФО»" повторяется на каждой странице. Не experimental --
    полный цикл detect -> parse_contracts/applications/queries пройден
    на реальных данных, см. test_kredit_info_regression.py.
    """
    return "БКИ КРЕДИТИНФО" in probe_upper or "BKI-CI.RU" in probe_upper


# Порядок: специфичные/новые форматы перед общими. scoring_2026 помечен
# experimental=True (не проверен на реальном файле) и не участвует в
# обычном detect(), только при include_experimental=True.
_REGISTRY: list[tuple[FormatMatch, Callable[[str], bool], bool]] = [
    (FormatMatch("nbki", "unified"), _sniff_nbki, False),
    (FormatMatch("scoring", "2026"), _sniff_scoring_2026, True),   # experimental
    (FormatMatch("scoring", "legacy"), _sniff_scoring_legacy, False),
    (FormatMatch("okb", "v1"), _sniff_okb, False),
    (FormatMatch("kredit_info", "v1"), _sniff_kredit_info, False),
]


def detect(pdf_path: str, include_experimental: bool = False) -> FormatMatch:
    probe_upper = probe_text(pdf_path).upper()

    for match, sniff, experimental in _REGISTRY:
        if experimental and not include_experimental:
            continue
        if sniff(probe_upper):
            return match

    raise ValueError(
        "Формат БКИ не распознан. Поддерживаются НБКИ, ОКБ/Кредистория и "
        "Скоринг Бюро (legacy). Если это новый формат -- нужен sniff() "
        "в detect.py и фикстура для регресс-теста, а не подгонка "
        "существующего адаптера."
    )
