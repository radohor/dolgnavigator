from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pymupdf

DATE_RE = re.compile(r"\b(\d{2}-\d{2}-\d{4})\b")
GUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-[0-9a-f])", re.I)
CONTRACT_HEAD_RE = re.compile(r"(?m)^\s*(\d+)\.\s+(.+?)\s*$")

HEADER_LINES = {
    "Кредитный отчет для субъекта", "ID запроса", "Пользователь", "Предоставлен",
    "При наличии вопросов обращайтесь в НБКИ: 8-495-221-78-37, 8-800-600-64-04",
}


def get_text(pdf_path: str) -> str:
    doc = pymupdf.open(pdf_path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _clean(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = s.replace("\ufffe", "").replace("\xad", "")
    s = re.sub(r"\s+", " ", s).strip()
    return None if s in {"", "Н/Д"} else s


def _field(block: str, label: str, max_chars: int = 500) -> Optional[str]:
    m = re.search(re.escape(label) + r"\s*([^\n]+)", block, re.I)
    return _clean(m.group(1)) if m else None


def _multiline_field(block: str, label: str, stop_labels: tuple[str, ...], max_lines: int = 8) -> Optional[str]:
    lines = block.splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith(label.lower()):
            first = line.split(":", 1)[1].strip() if ":" in line else ""
            parts = [first] if first else []
            for nxt in lines[i+1:i+1+max_lines]:
                st = nxt.strip()
                if not st:
                    continue
                if any(st.lower().startswith(x.lower()) for x in stop_labels):
                    break
                if st in HEADER_LINES or st.startswith("System version:") or st.startswith("Стр. "):
                    continue
                parts.append(st)
            return _clean(" ".join(parts))
    return None


def _first_guid_after(block: str, label_pattern: str) -> Optional[str]:
    m = re.search(label_pattern, block, re.I)
    if not m:
        return None
    tail = block[m.end():m.end()+500].replace("\ufffe", "").replace("\n", "")
    tail = re.sub(r"\s+", "", tail)
    g = GUID_RE.search(tail)
    return g.group(1).lower() if g else None


def _parse_money(value: Optional[str]) -> Optional[str]:
    return _clean(value)


def _summary_counts(text: str) -> dict:
    # Summary page presents query and application totals as:
    # Всего ... Последний <queries> ... Обращений ... <applications> ...
    head = text[:20000]
    result = {"queries": None, "applications": None}
    m = re.search(
        r"Всего\s+За последний месяц\s+За последние 3 месяца\s+За последние 12 месяцев\s+Последний\s+"
        r"(\d+)\s+\d+\s+\d+\s+\d+\s+\d{2}-\d{2}-\d{4}\s+Обращений\s+"
        r"Первая дата подачи\s+Последняя дата подачи\s+Одобрено\s+(\d+)",
        head, re.S
    )
    if m:
        result["queries"] = int(m.group(1))
        result["applications"] = int(m.group(2))
    return result


def parse_contracts(text: str) -> list[dict]:
    start = text.find("Обязательства и их исполнение")
    end = text.find("Информационная часть", start + 1)
    if start < 0:
        return []
    section = text[start:end if end > start else None]
    heads = list(CONTRACT_HEAD_RE.finditer(section))
    out = []

    for i, h in enumerate(heads):
        chunk = section[h.start(): heads[i+1].start() if i+1 < len(heads) else len(section)]
        title = _clean(h.group(2)) or ""
        parts = [p.strip() for p in title.split(" - ")]
        creditor = parts[0] if parts else title
        product = " - ".join(parts[1:]) if len(parts) > 1 else None

        uid = _first_guid_after(chunk, r"УИ[Дд]\s+договора(?:\s*\(сделки\))?\s*:")
        contract_id = _field(chunk, "Номер договора:")

        # Modern NBKI blocks: date/amount are in a table after "Дата сделки".
        start_date = _field(chunk, "Открыт:")
        amount = _field(chunk, "Размер/лимит:")
        status = _field(chunk, "Статус:")
        current_debt = _field(chunk, "Задолж-сть:")
        overdue = _field(chunk, "Просрочено:")

        if not start_date:
            marker = chunk.find("Дата сделки")
            if marker >= 0:
                tail = chunk[marker:marker+1800]
                dm = DATE_RE.search(tail)
                if dm:
                    start_date = dm.group(1)

        if not amount:
            # In modern table choose money immediately before RUB; first value is contract amount.
            marker = chunk.find("Сумма и валюта")
            if marker >= 0:
                tail = chunk[marker:marker+1800]
                mm = re.search(r"([0-9][0-9\s.,]*)\s*RUB\b", tail)
                if mm:
                    amount = _clean(mm.group(1) + " RUB")

        # Modern blocks may have a concise aggregate debt heading on later pages.
        if not current_debt:
            mm = re.search(r"(?m)^Задолженность:\s*([^\n]+)$", chunk)
            if mm:
                current_debt = _clean(mm.group(1))
        if not overdue:
            mm = re.search(r"(?m)^Просроченная задолженность:\s*([^\n]+)$", chunk)
            if mm:
                overdue = _clean(mm.group(1))

        out.append({
            "index": int(h.group(1)),
            "contract_id": contract_id or uid or f"nbki-{h.group(1)}",
            "uid": uid,
            "creditor": creditor,
            "product": product,
            "amount": amount,
            "start_date": start_date,
            "status": status,
            "current_debt": current_debt,
            "current_overdue_amount": overdue,
            "source_title": title,
        })
    return out


def _modern_application_blocks(text: str) -> list[dict]:
    """
    New NBKI format.

    Important: in NBKI the application date and creditor are located AFTER the
    application UID, while requested amount / participation are located BEFORE it.
    Therefore a symmetric window around UID is unsafe: it can pick fields from the
    next application. We first isolate one complete application block from the
    nearest preceding "Вид участия:" to the next "Вид участия:".
    """
    matches = list(re.finditer(r"УИ[Дд]\s+обращения:\s*", text, re.I))
    out = []

    for m in matches:
        # Start of the current application.
        left = text.rfind("\nВид участия:", max(0, m.start() - 4000), m.start())
        if left < 0:
            left = max(0, m.start() - 2500)
        else:
            left += 1

        # Start of the next application.
        right = text.find("\nВид участия:", m.end())
        if right < 0 or right - m.start() > 9000:
            right = min(len(text), m.start() + 7000)

        block = text[left:right]

        uid = _first_guid_after(block, r"УИ[Дд]\s+обращения:\s*")
        app_date = _field(block, "Дата обращения:")
        amount = _field(block, "Запрошенная сумма:")
        status = _field(block, "Стадия рассмотрения обращения:")
        refusal_date = _field(block, "Дата отказа:")
        method = _field(block, "Способ обращения:")
        reason = _field(block, "Код причины отказа:")

        creditor = _multiline_field(
            block,
            "Полное наименование:",
            ("Сокращенное наименование:", "Иное наименование:", "Идентификатор LEI:",
             "Дата создания:", "Гос.рег.номер:", "Регистрационный номер:"),
            max_lines=7
        )

        out.append({
            "application_date": app_date,
            "uid": uid,
            "creditor": creditor,
            "amount": amount,
            "status": status,
            "refusal_date": refusal_date,
            "method": method,
            "refusal_reason": reason,
            "format": "modern",
        })
    return out

def _legacy_application_blocks(text: str) -> list[dict]:
    # Old NBKI format: "Заявка" + number/date, no application UID.
    start = text.find("Информационная часть")
    if start < 0:
        return []
    section = text[start:]
    ms = list(re.finditer(r"(?m)^Заявка\s*$", section))
    out = []
    for i, m in enumerate(ms):
        chunk = section[m.start(): ms[i+1].start() if i+1 < len(ms) else min(len(section), m.start()+6000)]
        # Do not treat text after inquiries section as applications.
        if "Код запроса" in chunk[:300] or "Запрошенные сведения:" in chunk[:300]:
            continue
        date = _field(chunk, "Дата заявки:")
        if not date:
            continue
        number = _multiline_field(chunk, "Номер заявки:", ("Дата заявки:",), max_lines=2)
        decision = _field(chunk, "Решение об одобрении:")
        refusal_date = _field(chunk, "Дата отказа:")
        amount = _multiline_field(chunk, "Сумма отклоненной заявки:", ("Дата отказа:",), max_lines=2)
        method = _multiline_field(chunk, "Способ подачи заявки:", ("Одобрение", "Решение об одобрении:"), max_lines=2)
        creditor = _multiline_field(
            chunk, "Полное наименование:",
            ("Гос.рег.номер:", "ИНН:", "Заявка", "Код запроса"),
            max_lines=8
        )
        status = None
        if decision and decision != "Н/Д":
            status = decision
        elif refusal_date and refusal_date != "Н/Д":
            status = "Отказ"
        out.append({
            "application_date": date,
            "uid": None,
            "application_id": number,
            "creditor": creditor,
            "amount": amount,
            "status": status,
            "refusal_date": refusal_date,
            "method": method,
            "format": "legacy",
        })
    return out


def parse_applications(text: str) -> list[dict]:
    return _modern_application_blocks(text) + _legacy_application_blocks(text)


def _new_query_blocks(text: str) -> list[dict]:
    ms = list(re.finditer(r"(?m)^Запрошенные сведения:\s*", text))
    out = []
    for i, m in enumerate(ms):
        chunk = text[m.start(): ms[i+1].start() if i+1 < len(ms) else min(len(text), m.start()+5000)]
        qdate = _field(chunk, "Запрошено:") or _field(chunk, "Предоставлено:")
        requester = _multiline_field(
            chunk, "Полное наименование:",
            ("Сокращенное наименование:", "Иное наименование:", "Идентификатор LEI:",
             "Цель:", "Сумма:", "Регистрационный номер:"),
            max_lines=7
        )
        out.append({
            "query_date": qdate,
            "requester": requester,
            "purpose": _field(chunk, "Цель:"),
            "amount": _field(chunk, "Сумма:"),
            "requested_info": _field(chunk, "Запрошенные сведения:"),
            "user_kind": _field(chunk, "Код пользователя:"),
            "format": "modern",
        })
    return out


def _legacy_query_blocks(text: str) -> list[dict]:
    ms = list(re.finditer(r"(?m)^Код запроса\s*$", text))
    out = []
    for i, m in enumerate(ms):
        chunk = text[m.start(): ms[i+1].start() if i+1 < len(ms) else min(len(text), m.start()+5000)]
        qdate = _field(chunk, "Дата запроса:")
        requester = _multiline_field(
            chunk, "Полное наименование:",
            ("Сокращ. наименование:", "Фирмен.наименование:", "Наимен.на языке РФ:",
             "Гос.рег.номер:", "ИНН:", "Дата запроса:"),
            max_lines=8
        )
        if qdate:
            out.append({
                "query_date": qdate,
                "requester": requester,
                "purpose": None,
                "amount": None,
                "requested_info": "Кредитный отчет",
                "user_kind": None,
                "format": "legacy",
            })
    return out


def parse_queries(text: str) -> list[dict]:
    return _new_query_blocks(text) + _legacy_query_blocks(text)


def parse_nbki(pdf_path: str) -> dict:
    text = get_text(pdf_path)
    contracts = parse_contracts(text)
    applications = parse_applications(text)
    queries = parse_queries(text)
    summary = _summary_counts(text)

    warnings = []
    if summary.get("applications") is not None and summary["applications"] != len(applications):
        warnings.append(
            f"НБКИ сообщает {summary['applications']} обращений, детально распознано {len(applications)}. "
            "Разница сохранена как контроль качества и не подменяется вымышленными записями."
        )
    if summary.get("queries") is not None and summary["queries"] != len(queries):
        warnings.append(
            f"НБКИ сообщает {summary['queries']} запросов, детально распознано {len(queries)}."
        )

    return {
        "contracts": contracts,
        "applications": applications,
        "queries": queries,
        "summary_counts": summary,
        "warnings": warnings,
    }
