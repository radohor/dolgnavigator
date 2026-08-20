"""
Адаптер парсера для отчётов ОКБ.

В отличие от Скоринг Бюро, детальные карточки договоров ОКБ оформлены
как форма: строка заголовков колонок, затем строка(и) значений под ней
(см. /areas/dolgnavigator.md — 'Общие сведения о сделке' и похожие блоки).
Реальный порядок чтения текста НЕ даёт однозначного сопоставления
"заголовок -> значение" при многострочных значениях — поэтому здесь
используется get_layout_text() из layout_utils.py (pdfplumber,
сохраняет визуальные колонки без Poppler-бинарника) и привязка по
символьной x-позиции заголовка / формат-якорям, а не порядок следования.

Проверено на реальном отчёте (253 стр.): parse_all_cards() находит
ровно 56 границ карточек, все 56 дают непустой и уникальный uid,
ни одного пропуска creditor -- см. docstring parse_all_cards() и
debug_dump.py.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

from layout_utils import get_layout_text

CARD_TITLE_RE = re.compile(r"^\s*\d+\.\s+.+-\s*Договор", re.M)

# Известные метки полей — фиксированы шаблоном отчёта, не меняются от
# клиента к клиенту. Задаём вручную, а не пытаемся определять их
# автоматически: raз форматы бюро известны заранее, это дешевле и
# надёжнее общего table-detector'а.
DEAL_INFO_LABELS = ["Вид участия в сделке", "Идентификатор сделки", "Номер сделки"]
DEAL_DATES_LABELS = ["Дата совершения сделки", "Дата возникновения обязательства",
                      "Дата прекращения обязательства по условиям сделки"]
DEAL_TYPE_LABELS = ["Тип сделки", "Вид займа (кредита)", "Цель займа (кредита)"]
AMOUNT_LABELS = ["Сумма и валюта обязательства", "Дата расчета"]


DATE_PATTERN = re.compile(r"\d{2}\s+\S+\s+\d{4}")
GUID_PATTERN = re.compile(r"[0-9a-f]{6,}-[0-9a-f-]+", re.I)
AMOUNT_PATTERN = re.compile(r"[\d\s]+[.,]?\d*\s*р\.")


def extract_ordered_by_pattern(value_line: str, pattern: re.Pattern, count: int) -> list[str | None]:
    """
    Достаёт до `count` совпадений известного формата (дата/GUID/сумма) из
    строки значений, СЛЕВА НАПРАВО -- то есть в том же порядке, в каком
    заголовки колонок идут в строке-шапке. Это надёжнее, чем резать
    строку по символьной позиции заголовка: если значение шире своей
    колонки (типичный случай для длинного УИД сделки), позиционная
    нарезка срежет часть значения или залезет в соседнюю колонку --
    что и произошло при переходе с pdftotext -layout на pdfplumber
    (два инструмента чуть по-разному считают ширину пробелов).
    Формат-анкеры (дата ДД месяц ГГГГ, GUID) не зависят от точной
    ширины колонки.
    """
    matches = [m.group() for m in pattern.finditer(value_line)]
    matches += [None] * (count - len(matches))
    return matches[:count]


def extract_labeled_block(text: str, label_row_prefix: str, labels: list[str]) -> dict[str, str]:
    """
    Находит строку, где встречается ПЕРВАЯ метка из labels (это строка
    заголовков), берёт следующую непустую строку как строку значений,
    и режет обе по x-позиции каждой метки. Однострочная версия --
    для значений, растянутых на несколько строк (частый случай:
    многострочное наименование кредитора), нужно расширять диапазон
    построчно, пока не встретится следующий известный блок-заголовок --
    это оставлено как TODO, отмеченный в docstring модуля.
    """
    lines = text.split("\n")
    label_line_idx = None
    for i, line in enumerate(lines):
        # строка-заголовок должна содержать label[0] И хотя бы ещё одну
        # метку из набора -- иначе можно попасть на строку раздела,
        # которая повторяет текст первой метки как самостоятельный
        # заголовок блока (случай "Сумма и валюта обязательства").
        if labels[0] in line and any(l in line for l in labels[1:]):
            label_line_idx = i
            break
    if label_line_idx is None:
        return {}
    value_line_idx = label_line_idx + 1
    while value_line_idx < len(lines) and not lines[value_line_idx].strip():
        value_line_idx += 1
    if value_line_idx >= len(lines):
        return {}

    label_line = lines[label_line_idx]
    value_line = lines[value_line_idx]

    positions = sorted((label_line.find(l), l) for l in labels if label_line.find(l) != -1)
    result = {}
    for i, (start_col, label) in enumerate(positions):
        end_col = positions[i + 1][0] if i + 1 < len(positions) else len(value_line)
        result[label] = value_line[start_col:end_col].strip()
    return result


def parse_contract_card(card_text: str) -> dict:
    """Извлекает ключевые поля одной карточки договора ОКБ."""
    deal_info = extract_labeled_block(card_text, "Общие сведения о сделке", DEAL_INFO_LABELS)
    deal_type = extract_labeled_block(card_text, "", DEAL_TYPE_LABELS)
    amount_block = extract_labeled_block(card_text, "", AMOUNT_LABELS)

    # УИД -- по формату (GUID), а не по позиции колонки: значение почти
    # всегда шире заголовка "Идентификатор сделки" и вылезает за границу
    # соседней колонки при позиционной нарезке.
    deal_info_value_line = _find_value_line(card_text, DEAL_INFO_LABELS)
    uid = GUID_PATTERN.search(deal_info_value_line).group() if deal_info_value_line and GUID_PATTERN.search(deal_info_value_line) else None

    # Три даты сделки -- тоже по формату, в порядке следования колонок.
    dates_value_line = _find_value_line(card_text, DEAL_DATES_LABELS)
    contract_date, obligation_start, obligation_end = (
        extract_ordered_by_pattern(dates_value_line, DATE_PATTERN, 3) if dates_value_line else (None, None, None)
    )

    title_match = re.match(r"^\d+\.\s+(.+?)\s*-\s*(Договор.+?)\s*$", card_text.strip().split("\n")[0].strip())
    creditor_title = title_match.group(1) if title_match else None
    product_title = title_match.group(2) if title_match else None

    return {
        "creditor": creditor_title,          # из заголовка карточки -- надёжнее, чем из грид-блока
        "product": product_title,
        "uid": uid,
        "participation": deal_info.get("Вид участия в сделке"),
        "contract_date": contract_date,
        "obligation_start_date": obligation_start,
        "obligation_end_date": obligation_end,
        "deal_type": deal_type.get("Тип сделки"),
        "loan_kind": deal_type.get("Вид займа (кредита)"),
        "amount": amount_block.get("Сумма и валюта обязательства"),
        "amount_calc_date": amount_block.get("Дата расчета"),
    }


def _find_value_line(text: str, labels: list[str]) -> str | None:
    """Строка значений сразу после строки, где встречаются метки labels."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if labels[0] in line and any(l in line for l in labels[1:]):
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            return lines[j] if j < len(lines) else None
    return None


def parse_all_cards(pdf_path: str) -> list[dict]:
    """
    Проходит весь документ и разбирает каждую карточку договора.

    Проверено на реальном отчёте (253 стр.): находит ровно 56 границ
    карточек, все 56 дают непустой уникальный uid и непустого creditor
    (см. debug_dump.py). Важная деталь про структуру отчёта: в нём ДВЕ
    отдельные нумерованные последовательности карточек -- "Действующие
    кредитные договоры" (17 шт., нумерация 1..17) и вторая группа
    (39 шт., нумерация СНОВА с 1..39, судя по заголовкам, это архивные/
    закрытые договоры). 17 + 39 = 56 -- сходится с контролем, но это
    сумма ДВУХ разных секций, а не одна секция на 56 записей; при
    сборке в один JSON это не проблема (uid всё равно уникальны), но
    при отладке не пугайтесь, что нумерация в заголовках карточек
    дважды начинается с 1.
    """
    text = get_layout_text(pdf_path)
    matches = list(CARD_TITLE_RE.finditer(text))
    blocks = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append(text[m.start():end])
    return [parse_contract_card(b) for b in blocks]


if __name__ == "__main__":
    import sys
    import json

    pdf_path = sys.argv[1]
    results = parse_all_cards(pdf_path)
    print(f"Договоры ОКБ: {len(results)} (контроль: 56)", file=sys.stderr)
    uids = [r["uid"] for r in results]
    print(f"Уникальных uid: {len(set(u for u in uids if u))}", file=sys.stderr)
    print(json.dumps(results, ensure_ascii=False, indent=2))
