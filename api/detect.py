from __future__ import annotations
from dataclasses import dataclass
import pymupdf

@dataclass(frozen=True)
class FormatMatch:
    bureau: str
    format_version: str

def probe_text(pdf_path: str, pages: int = 12) -> str:
    doc = pymupdf.open(pdf_path)
    try:
        return "\n".join(doc[i].get_text() for i in range(min(pages, len(doc))))
    finally:
        doc.close()

def _sniff_nbki(t):
    return "КРЕДИТНЫЙ ОТЧЕТ ДЛЯ СУБЪЕКТА" in t and ("ОБРАЩАЙТЕСЬ В НБКИ" in t or "SYSTEM VERSION:" in t)

def _sniff_scoring_current(t):
    identity = "СКОРИНГ БЮРО" in t or "SCORING.RU" in t
    markers = ("РЕЙТИНГ И ПОРТРЕТ ЗАЁМЩИКА", "ДЕЙСТВУЮЩИЕ КРЕДИТЫ, ЗАЙМЫ, КАРТЫ", "ЗАЯВКИ НА КРЕДИТЫ, ЗАЙМЫ")
    return identity and sum(1 for m in markers if m in t) >= 2

def _sniff_scoring_legacy(t):
    return "СВОДНАЯ ИНФОРМАЦИЯ ПО ДОГОВОРАМ ЗАЙМА" in t or ("ПОДРОБНАЯ ИНФОРМАЦИЯ ПО ДОГОВОРАМ" in t and "КОД ОТЧЕТА" in t)

def _sniff_okb(t):
    return "КРЕДИСТОРИЯ" in t or "ОБЪЕДИНЕННОГО КРЕДИТНОГО БЮРО" in t or 'АО «ОКБ»' in t or 'АО "ОКБ"' in t

def _sniff_kredit_info(t):
    return "БКИ КРЕДИТИНФО" in t or "BKI-CI.RU" in t

REGISTRY = [
    (FormatMatch("nbki", "unified"), _sniff_nbki),
    (FormatMatch("scoring", "current_2026"), _sniff_scoring_current),
    (FormatMatch("scoring", "legacy"), _sniff_scoring_legacy),
    (FormatMatch("okb", "v1"), _sniff_okb),
    (FormatMatch("kredit_info", "v1"), _sniff_kredit_info),
]

def detect(pdf_path: str) -> FormatMatch:
    t = probe_text(pdf_path).upper()
    for match, sniff in REGISTRY:
        if sniff(t):
            return match
    raise ValueError("Формат БКИ не распознан. Требуется отдельный sniff() и regression fixture.")
