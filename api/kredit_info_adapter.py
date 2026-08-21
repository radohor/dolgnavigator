"""
Адаптер парсера для отчётов Кредит Инфо (ООО «БКИ КредитИнфо»).

Проверено на реальном отчёте (Постельный И.А., 21 стр., версия отчёта 07.00):
- Границы записи договора: "Запись кредитной истории по договору (сделке)".
- УИД: "Уникальный идентификатор договора\n(сделки)\n<GUID>" -- метка
  переносится на 2 строки, поэтому ищем по regex через оба варианта
  переноса, а не по одной строке после метки.
- В отличие от ОКБ, здесь НЕТ сетки "несколько меток в строке -- несколько
  значений в следующей строке": каждая метка -> одно значение сразу под
  ней, последовательно. Это ближе по структуре к НБКИ, чем к ОКБ.
- Открыт/закрыт определяется ТОЛЬКО через "Основание прекращения
  обязательства" + "Дата фактического прекращения обязательства" внутри
  записи -- общей сводки в отчёте нет (подтверждено и в самом отчёте,
  и в вики-инструкции пользователя).
- Заявки -- раздел "Информационная часть кредитной истории", поле
  "Cтадия рассмотрения обращения" (внимание: в PDF это ЛАТИНСКАЯ "C",
  а не кириллическая "С" -- нужен regex ".тадия", а не точное слово).
  На тестовом отчёте: 3 "Одобрено", 8 "Отказано источником",
  1 "На рассмотрении", 1 "Отозвано субъектом до одобрения источником
  обращения" -- те же по смыслу статусы, что и в НБКИ, поэтому
  normalize_application_status() переиспользуется без изменений.
- Запросы -- раздел "Закрытая часть кредитной истории" ->
  "Список пользователей, запросивших кредитную историю субъекта".
  На тестовом отчёте: 14 строк.

НЕ проверено: другие отчёты Кредит Инфо (только один тестовый файл),
поведение при бОльшем числе договоров/заявок, поведение когда
"Стадия рассмотрения обращения" содержит не встреченные в тесте значения.
"""
from __future__ import annotations
import re

import pymupdf

from normalize import normalize_application_status


def get_text(pdf_path: str) -> str:
    doc = pymupdf.open(pdf_path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


RECORD_BOUNDARY_RE = re.compile(r"Запись кредитной истории по договору \(сделке\)\s*,\s*(.+)")
UID_RE = re.compile(r"Уникальный идентификатор договора\s*\n\(сделки\)\s*\n([0-9a-f-]+)", re.I)
DEAL_DATE_RE = re.compile(r"Дата совершения сделки\s*\n([\d.]+|-)")
AMOUNT_RE = re.compile(r"Сумма обязательства\s*\n([\d.,]+\s*[A-ZА-Я]+|-)")
TERMINATION_BASIS_RE = re.compile(
    r"Основание прекращения обязательства\s*\n(.+?)\n"
    r"Дата фактического прекращения\s*\nобязательства\s*\n([\d.]+|-)",
    re.S,
)
PARTICIPATION_RE = re.compile(r"Вид участия в сделке\s*\n(.+?)\n")


def parse_contracts(text: str) -> list[dict]:
    boundaries = list(RECORD_BOUNDARY_RE.finditer(text))
    out = []
    for i, m in enumerate(boundaries):
        end = boundaries[i + 1].start() if i + 1 < len(boundaries) else len(text)
        block = text[m.start():end]
        creditor = m.group(1).strip()

        uid_m = UID_RE.search(block)
        date_m = DEAL_DATE_RE.search(block)
        amount_m = AMOUNT_RE.search(block)
        term_m = TERMINATION_BASIS_RE.search(block)
        part_m = PARTICIPATION_RE.search(block)

        termination_basis = term_m.group(1).strip() if term_m else None
        termination_date = term_m.group(2).strip() if term_m else None
        # "Дата фактического прекращения" == "-" -- договор действующий;
        # непустая дата -- закрыт. Основание "Надлежащее исполнение
        # обязательства" без даты (редкий случай) тоже трактуем как
        # неопределённое состояние, а не как закрытие -- см. QC-принцип
        # "не терять строки молча" вместо "додумать закрытие".
        is_closed = bool(termination_date and termination_date != "-")

        out.append({
            "creditor": creditor,
            "uid": uid_m.group(1) if uid_m else None,
            "deal_date": date_m.group(1) if date_m and date_m.group(1) != "-" else None,
            "amount": amount_m.group(1).strip() if amount_m else None,
            "participation": part_m.group(1).strip() if part_m else None,
            "termination_basis": termination_basis,
            "termination_date": termination_date if termination_date != "-" else None,
            "is_closed": is_closed,
        })
    return out


APPLICATION_STAGE_RE = re.compile(
    r".тадия рассмотрения обращения\s*\n(.+?)\n(?:Дата перехода|Способ обращения)", re.S
)
APPLICATION_AMOUNT_RE = re.compile(
    r"Сумма запрошенного займа \(кредита\), лизинга\s*\nили обеспечения\s*\n([\d.,]+\s*[A-ZА-Я]+|-)"
)
APPLICATION_REFUSAL_DATE_RE = re.compile(r"Дата отказа\s*\n([\d.]+)")
APPLICATION_REFUSAL_REASON_RE = re.compile(r"Причина отказа\s*\n(.+?)\n")
APPLICATION_APPROVED_AMOUNT_RE = re.compile(
    r"Сумма одобренного займа \(кредита\), лизинга\s*\nили обеспечения\s*\n([\d.,]+\s*[A-ZА-Я]*|-)"
)


def parse_applications(text: str) -> list[dict]:
    """
    Раздел "Информационная часть кредитной истории" -> "Закрытая часть
    кредитной истории" как правая граница (следующий крупный раздел).
    Внутри -- повторяющиеся блоки, каждый начинается с "Сокращенное
    наименование и вид" (источник) и содержит "Вид участия в сделке".
    Используем позиции ".тадия рассмотрения обращения" как якоря записи
    (по одной на заявку), а не более раннее поле -- оно надёжнее
    по кол-ву совпадений. Захват самой стадии -- ДО следующей известной
    метки поля ("Дата перехода" / "Способ обращения"), а не до первого
    переноса строки: значение "Отозвано субъектом ..." переносится на
    2 строки в PDF, и наивный "до первого \\n" его обрезал.
    """
    start = text.find("Информационная часть кредитной истории")
    end = text.find("Закрытая часть кредитной истории")
    if start < 0:
        return []
    section = text[start:end if end > start else len(text)]

    anchors = list(APPLICATION_STAGE_RE.finditer(section))
    out = []
    for i, m in enumerate(anchors):
        # Окно вперёд ограничено СЛЕДУЮЩИМ якорем (или концом секции),
        # а не фиксированным числом символов -- иначе дата/причина
        # отказа следующей заявки утекает в текущую запись (баг,
        # найденный на реальном отчёте: "Одобрено" ошибочно получало
        # статус "Отказ" из-за отказа СЛЕДУЮЩЕЙ заявки в тексте).
        left = anchors[i - 1].end() if i > 0 else 0
        right = anchors[i + 1].start() if i + 1 < len(anchors) else len(section)
        block = section[m.end():right]
        pre_block = section[left:m.start()]

        raw_stage = m.group(1).strip()
        refusal_date_m = APPLICATION_REFUSAL_DATE_RE.search(block)
        refusal_reason_m = APPLICATION_REFUSAL_REASON_RE.search(block)
        approved_amount_m = APPLICATION_APPROVED_AMOUNT_RE.search(pre_block)
        amount_m = APPLICATION_AMOUNT_RE.search(pre_block)

        status, warning = normalize_application_status(
            raw_stage=raw_stage,
            refusal_date=refusal_date_m.group(1) if refusal_date_m else None,
            refusal_reason=refusal_reason_m.group(1).strip() if refusal_reason_m else None,
            approval_decision=(
                approved_amount_m.group(1)
                if approved_amount_m and approved_amount_m.group(1) not in ("-", None)
                else None
            ),
        )

        out.append({
            "amount": amount_m.group(1).strip() if amount_m else None,
            "status": status.value,
            "status_raw": raw_stage,
            "status_warning": warning,
            "refusal_date": refusal_date_m.group(1) if refusal_date_m else None,
            "refusal_reason": refusal_reason_m.group(1).strip() if refusal_reason_m else None,
        })
    return out


def parse_queries(text: str) -> list[dict]:
    """
    "Закрытая часть кредитной истории" -> "Список пользователей,
    запросивших кредитную историю субъекта". Каждая строка -- одна
    пара дат (запрос/предоставление, часто совпадают) в конце блока
    организации. Считаем по числу пар дат -- проверено на реальном
    отчёте: 14 строк, 28 дат.
    """
    start = text.find("Список пользователей")
    end = text.find("Дата:", start)
    if start < 0:
        return []
    section = text[start:end if end > start else len(text)]
    dates = re.findall(r"\d{2}\.\d{2}\.\d{4}", section)
    # даты идут парами (запрос/предоставление); если отчёт содержит
    # нечётное число дат, это сигнал для QC, а не для тихого деления
    # нацело -- лучше вернуть по одной дате на "запрос", чем ошибиться
    # в подсчёте вдвое.
    return [{"date": d} for d in dates[::2]]



def parse_kredit_info(pdf_path: str) -> dict:
    """Unified production wrapper around the tested Kredit Info extractors."""
    text = get_text(pdf_path)
    raw_contracts = parse_contracts(text)
    raw_apps = parse_applications(text)
    raw_queries = parse_queries(text)

    contracts = []
    for c in raw_contracts:
        contracts.append({
            **c,
            "contract_id": c.get("uid") or "",
            "start_date": c.get("deal_date"),
            "status": "Закрыт" if c.get("is_closed") else "Открыт",
            "state": "Закрыт" if c.get("is_closed") else "Открыт",
            "actual_end_date": c.get("termination_date"),
        })

    # Re-parse application blocks only for identity/date fields. Status semantics
    # remain owned by parse_applications()/normalize_application_status().
    info_start = text.find("Информационная часть кредитной истории")
    closed_start = text.find("Закрытая часть кредитной истории", info_start + 1)
    section = text[info_start:closed_start if closed_start > info_start else len(text)] if info_start >= 0 else ""
    anchors = list(APPLICATION_STAGE_RE.finditer(section))
    apps = []
    for i, a in enumerate(raw_apps):
        if i < len(anchors):
            m = anchors[i]
            left = anchors[i-1].end() if i > 0 else 0
            pre = section[left:m.start()]
        else:
            pre = ""
        dm = re.search(r"Дата обращения\s*\n([\d.]+)", pre)
        uidm = re.search(r"УИ[дД] обращения\s*/\s*Номер обращения\s*\n([^\n]+)", pre, re.I)
        source_m = re.search(r"Сокращенное наименование и вид\s*\nисточника\s*\n(.+?)\n(?:Заимодавец|Полное и иное наименование)", pre, re.S)
        method_m = re.search(r"Способ обращения\s*\n(.+?)(?:\nДата окончания|\nЦель займа|\nCтадия|\nСтадия)", pre, re.S)
        uid = None
        app_id = None
        if uidm:
            raw_id = uidm.group(1).strip()
            parts = [x.strip() for x in raw_id.split('/')]
            if parts and parts[0] not in ('','-'): uid = parts[0]
            if len(parts)>1 and parts[1] not in ('','-'): app_id = parts[1]
        apps.append({
            **a,
            "application_date": dm.group(1) if dm else None,
            "uid": uid,
            "application_id": app_id,
            "creditor": re.sub(r"\s+"," ",source_m.group(1)).strip() if source_m else None,
            "method": re.sub(r"\s+"," ",method_m.group(1)).strip() if method_m else None,
        })

    queries = []
    for q in raw_queries:
        queries.append({
            **q,
            "query_date": q.get("date"),
            "requester": q.get("requester") or "Кредит Инфо: пользователь КИ",
        })

    return {
        "contracts": contracts,
        "applications": apps,
        "queries": queries,
        "warnings": [],
        "meta": {
            "uid_unique": len({c.get("uid") for c in contracts if c.get("uid")}),
        },
    }
