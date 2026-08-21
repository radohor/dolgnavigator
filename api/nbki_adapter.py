from __future__ import annotations

import re
import datetime
from pathlib import Path
from typing import Optional

import pymupdf

from normalize import normalize_application_status, normalize_contract_state

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



def _money_number(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    raw = value.upper().replace("RUB", "").replace(" ", "").strip()
    if raw in {"", "Н/Д"}:
        return None
    # NBKI mixes 181891,24 and 181,891.00.
    if "," in raw and "." in raw:
        if raw.rfind(".") > raw.rfind(","):
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        parts = raw.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _format_money_rub(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    if abs(value - round(value)) < 0.005:
        return f"RUB {int(round(value))}"
    return f"RUB {value:.2f}".replace(".", ",")


def _modern_deal_header(chunk: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Modern NBKI contract table is emitted column-first by PyMuPDF:
      Date of deal / Type / Product / Card / Amount / Planned end
      then the row values.
    Parse by table position, not by free text proximity.
    """
    m = re.search(
        r"Дата сделки\s*\n"
        r"Тип сделки\s*\n"
        r"Вид займа \(кредита\)\s*\n"
        r"(?:Использование\s*\nплатежной карты\s*\n)?"
        r"Сумма и валюта\s*\n"
        r"Дата прекращения\s*\nпо условиям сделки\s*\n"
        r"(\d{2}-\d{2}-\d{4})\s*\n"
        r"[\s\S]{0,220}?"
        r"([0-9][0-9\s.,]*)\s+RUB\s*\n"
        r"(\d{2}-\d{2}-\d{4}|Н/Д)",
        chunk,
        re.I
    )
    if not m:
        return None, None, None
    return _clean(m.group(1)), _clean(m.group(2) + " RUB"), _clean(m.group(3))


def _modern_actual_end(chunk: str) -> tuple[Optional[str], Optional[str]]:
    """
    Modern NBKI termination table.

    Labels end with:
      "Прекращение обязательства"
      "Дата фактического прекращения"
    After that PyMuPDF emits row values. We stop before the next table heading
    ("Возникновение обязательства..."), take the last date in that value block
    as the actual termination date, and retain the textual termination basis.
    """
    lines = [x.strip() for x in chunk.splitlines()]
    for i in range(len(lines) - 1):
        if lines[i] == "Дата фактического" and lines[i + 1] == "прекращения":
            values = []
            for x in lines[i + 2:i + 45]:
                if x.startswith("Возникновение обязательства"):
                    break
                values.append(x)

            dates = [x for x in values if re.fullmatch(r"\d{2}-\d{2}-\d{4}", x)]
            if not dates:
                return None, None
            actual = dates[-1]

            # The basis is the non-boolean/non-date text immediately before
            # the actual date. Join wrapped lines.
            basis_parts = []
            for x in values:
                if x == actual:
                    break
                if not x or x in {"Да", "Нет", "Н/Д"}:
                    continue
                if re.fullmatch(r"\d{2}-\d{2}-\d{4}", x):
                    continue
                basis_parts.append(x)

            basis = _clean(" ".join(basis_parts[-3:])) if basis_parts else None
            return actual, basis
    return None, None


def _historical_max_debt(chunk: str) -> Optional[float]:
    """
    Historical maximum debt from modern NBKI 'Задолженность' table.
    This is NOT the original credit limit and is stored separately.
    """
    start = chunk.find("\nЗадолженность")
    if start < 0:
        return None
    end = chunk.find("\nПросроченная задолженность", start + 1)
    section = chunk[start:end if end > start else min(len(chunk), start + 12000)]

    values = []
    # Typical NBKI row: date, calculation flag, total debt, principal, interest...
    for m in re.finditer(
        r"(?m)^(\d{2}-\d{2}-\d{4})\s*\n(?:Да|Нет|Н/Д)\s*\n([0-9][0-9.,]*)\s*$",
        section
    ):
        v = _money_number(m.group(2))
        if v is not None and v >= 0:
            values.append(v)
    return max(values) if values else None


def _first_overdue_candidate(chunk: str, start_date: Optional[str]) -> Optional[str]:
    """
    Conservative candidate from the modern NBKI overdue-debt table.
    Kept as a candidate field and is NOT fed into legal crisis analysis yet.
    """
    pos = chunk.find("\nПросроченная задолженность")
    if pos < 0:
        return None
    section = chunk[pos:]
    stop = section.find("\nУсловия платежей")
    if stop > 0:
        section = section[:stop]

    candidates = []
    for m in re.finditer(
        r"(?m)^(\d{2}-\d{2}-\d{4})\s*\n(?:Да|Нет)\s*\n([0-9][0-9.,]*)\s*$",
        section
    ):
        d = m.group(1)
        amount = _money_number(m.group(2))
        if amount is None or amount <= 0:
            continue
        if start_date and d == start_date:
            continue
        try:
            dt = datetime.datetime.strptime(d, "%d-%m-%Y")
        except ValueError:
            continue
        candidates.append((dt, d))

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]

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

        # Old NBKI format has explicit label/value fields.
        start_date = _field(chunk, "Открыт:")
        amount = _field(chunk, "Размер/лимит:")
        status = _field(chunk, "Статус:")
        status_date = _field(chunk, "Дата статуса:")
        current_debt = _field(chunk, "Задолж-сть:")
        overdue = _field(chunk, "Просрочено:")
        # "Третий вариант" карточки (см. /areas/dolgnavigator.md): реальная
        # дата закрытия здесь лежит в ЭТОМ поле, а не в "Открыт"/"Статус".
        # Раньше не извлекалось вообще -- договор молча считался открытым
        # (найдено на реальном отчёте Солтаевой: 4-й договор,
        # АО "Россельхозбанк", "Потребит.кредит").
        fact_full_execution = _field(chunk, "Факт.исполн.в полн.объеме:")
        planned_end = None
        actual_end = None
        termination_basis = None
        historical_max = None
        overdue_candidate = None
        format_name = "legacy" if start_date or amount or status else "modern"

        if format_name == "modern":
            modern_start, modern_amount, planned_end = _modern_deal_header(chunk)
            start_date = modern_start or start_date
            amount = modern_amount or amount

            actual_end, termination_basis = _modern_actual_end(chunk)
            historical_max = _historical_max_debt(chunk)
            overdue_candidate = _first_overdue_candidate(chunk, start_date)

            if actual_end:
                status = "Обязательство прекращено"
                status_date = actual_end

            # Some modern reports print a concise debt aggregate.
            if not current_debt:
                mm = re.search(r"(?m)^Задолженность:\s*([^\n]+)$", chunk)
                if mm:
                    current_debt = _clean(mm.group(1))
            if not overdue:
                mm = re.search(r"(?m)^Просроченная задолженность:\s*([^\n]+)$", chunk)
                if mm:
                    overdue = _clean(mm.group(1))

        contract_state, state_warning = normalize_contract_state(
            actual_end_date=actual_end,
            status_text=status,
            fact_full_execution_date=fact_full_execution,
            termination_basis=termination_basis,
        )

        reported_amount_num = _money_number(amount)
        amount_quality = "reported"
        if reported_amount_num == 0:
            # Zero is a valid reported value (especially old cards), but it is not
            # useful as an inferred original limit.
            amount_quality = "reported_zero"
        elif reported_amount_num is None:
            amount_quality = "missing"

        out.append({
            "index": int(h.group(1)),
            "contract_id": contract_id or uid or f"nbki-{h.group(1)}",
            "uid": uid,
            "creditor": creditor,
            "product": product,
            "amount": amount,
            "amount_quality": amount_quality,
            "start_date": start_date,
            "planned_end_date": planned_end,
            "actual_end_date": actual_end,
            "fact_full_execution_date": fact_full_execution,
            "status": status,
            "status_date": status_date,
            "termination_basis": termination_basis,
            "state": contract_state.value,
            "state_warning": state_warning,
            "current_debt": current_debt,
            "current_overdue_amount": overdue,
            "historical_max_debt": _format_money_rub(historical_max),
            "first_overdue_date_candidate": overdue_candidate,
            "first_overdue_date_candidate_confidence": "medium" if overdue_candidate else None,
            "format": format_name,
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
        raw_status = _field(block, "Стадия рассмотрения обращения:")
        refusal_date = _field(block, "Дата отказа:")
        method = _field(block, "Способ обращения:")
        reason = _field(block, "Код причины отказа:")
        approved_amount = _field(block, "Одобренная сумма:")

        # НЕ берём raw_status напрямую: в современных карточках стадия
        # может быть "Н/Д" одновременно с заполненными датой/причиной
        # отказа -- сама стадия недостоверна, факты рядом достоверны.
        # См. normalize.py -- эта же функция используется для legacy-блока
        # ниже и для будущих форматов, чтобы не чинить это точечно каждый раз.
        status, status_warning = normalize_application_status(
            raw_stage=raw_status, refusal_date=refusal_date, refusal_reason=reason,
            approval_decision=approved_amount,
        )

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
            "status": status.value,
            "status_raw": raw_status,
            "status_warning": status_warning,
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
        status_obj, status_warning = normalize_application_status(
            raw_stage=None, refusal_date=refusal_date if refusal_date != "Н/Д" else None,
            approval_decision=decision if decision and decision != "Н/Д" else None,
        )
        status = status_obj.value if (decision or refusal_date) else None
        out.append({
            "application_date": date,
            "uid": None,
            "application_id": number,
            "creditor": creditor,
            "amount": amount,
            "status": status,
            "status_warning": status_warning,
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

    zero_amount_contracts = sum(
        1 for c in contracts if c.get("amount_quality") == "reported_zero"
    )
    overdue_candidates = sum(
        1 for c in contracts if c.get("first_overdue_date_candidate")
    )

    uid_counts = {}
    for c in contracts:
        uid = (c.get("uid") or "").strip().lower()
        if uid:
            uid_counts[uid] = uid_counts.get(uid, 0) + 1
    duplicate_uid_groups = sum(1 for n in uid_counts.values() if n > 1)
    duplicate_uid_rows = sum(n - 1 for n in uid_counts.values() if n > 1)

    return {
        "contracts": contracts,
        "applications": applications,
        "queries": queries,
        "summary_counts": summary,
        "contract_qc": {
            "reported_zero_amounts": zero_amount_contracts,
            "overdue_date_candidates": overdue_candidates,
            "duplicate_uid_groups": duplicate_uid_groups,
            "duplicate_uid_rows": duplicate_uid_rows,
        },
        "warnings": warnings,
    }


def open_contract_count(contracts: list[dict]) -> int:
    """
    Число ОТКРЫТЫХ обязательств после дедупликации по УИД -- не по сырым
    карточкам. Один УИД может встречаться несколько раз (переуступка,
    старый+новый формат одного кредита) -- это одно обязательство, и его
    состояние определяется по канонической записи (закрытой, если хоть
    одна запись в цепочке закрыта; иначе -- первой доступной).

    Найдено на реальном отчёте Натальи: карточка первоначального
    кредитора (АО "ТБАНК") после полной переуступки долга не содержит
    СВОИХ сигналов закрытия вообще -- переуступка отражена только на
    стороне принявшего долг (ООО "ПКО "Феникс"). Подсчёт по сырым
    карточкам давал 1 открытый вместо 0 по сводке НБКИ; после дедупа
    по УИД -- 0, как и должно быть.
    """
    by_uid: dict[str, list[dict]] = {}
    no_uid: list[dict] = []
    for c in contracts:
        if c.get("uid"):
            by_uid.setdefault(c["uid"], []).append(c)
        else:
            no_uid.append(c)

    open_count = 0
    for group in by_uid.values():
        canonical = next((c for c in group if c.get("state") == "Закрыт"), group[0])
        if canonical.get("state") == "Открыт":
            open_count += 1
    open_count += sum(1 for c in no_uid if c.get("state") == "Открыт")
    return open_count
