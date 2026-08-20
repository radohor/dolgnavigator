"""
Общие утилиты для детерминированного извлечения данных из PDF-отчётов БКИ.

Два способа получения текста используются намеренно по-разному:

- get_pages_text() — «порядок чтения» (PyMuPDF get_text() без -layout).
  Для отчётов, сгенерированных из HTML-таблиц (Скоринг Бюро — Producer/Creator
  wkhtmltopdf/Qt), реальный порядок текстовых объектов в PDF соответствует
  порядку строк исходной HTML-таблицы: одно поле — одна строка вывода.
  Это и объясняет, почему в analyze-bki.js Скоринг уже стабильно
  распознавался как 98/98 договоров и 559/559 запросов — для табличных
  разделов простое построчное чтение уже работает.

- get_layout_text() — текст с сохранением визуальных колонок, через
  pdfplumber (extract_text(layout=True), чистый Python поверх pdfminer.six).
  Нужен там, где данные оформлены как форма «строка заголовков колонок /
  строка значений» (карточки договоров ОКБ), а не как линейный DOM-порядок.
  В этом случае колонку значения нужно находить не по порядку следования,
  а по символьной x-позиции — реальный порядок чтения для такой формы
  НЕ даёт однозначного сопоставления «заголовок -> значение» при
  многострочных значениях.

  ВАЖНО ДЛЯ ДЕПЛОЯ НА VERCEL: изначально здесь стоял вызов бинарника
  `pdftotext -layout` (Poppler) через subprocess. Он даёт идентичный
  результат локально, но Poppler — это НЕ pip-пакет, а системная утилита,
  которой нет в Vercel Python Runtime по умолчанию (и её пришлось бы
  отдельно собирать и включать в бандл функции, как для AWS Lambda).
  pdfplumber даёт то же самое выравнивание колонок как чистая Python-
  библиотека (pdfminer.six), поэтому для деплоя без лишней возни
  используется именно она — см. get_layout_text() ниже.

Проверять, какой способ подходит конкретному источнику/секции, нужно
на реальном образце — секции внутри одного отчёта тоже могут быть
устроены по-разному (см. пример: сводная таблица договоров и
детальные карточки договоров ОКБ используют разное визуальное
оформление одних и тех же данных).
"""
from __future__ import annotations
import re
from dataclasses import dataclass

import pymupdf
import pdfplumber


def get_pages_text(pdf_path: str) -> list[str]:
    """Текст каждой страницы в порядке чтения (без сохранения колонок)."""
    doc = pymupdf.open(pdf_path)
    return [page.get_text() for page in doc]


def get_full_text(pdf_path: str) -> str:
    return "\n".join(get_pages_text(pdf_path))


def get_layout_text(pdf_path: str, first: int | None = None, last: int | None = None) -> str:
    """
    Текст с сохранением визуальных колонок -- чистый Python, без Poppler.
    first/last -- 1-based номера страниц (как у pdftotext -f/-l), включительно.
    """
    with pdfplumber.open(pdf_path) as pdf:
        pages = pdf.pages
        start = (first - 1) if first else 0
        end = last if last else len(pages)
        return "\f".join(p.extract_text(layout=True) or "" for p in pages[start:end])


def find_section(text: str, start_marker: str, end_marker: str | None = None,
                  occurrence: int = -1) -> str:
    """
    Возвращает текст раздела между start_marker и end_marker.

    ВАЖНО: у большинства отчётов БКИ в начале документа есть оглавление,
    которое дублирует названия всех разделов. occurrence=-1 (по умолчанию)
    берёт ПОСЛЕДНЕЕ вхождение маркера — то есть реальный раздел, а не
    строку оглавления. Если известно точное число вхождений заранее,
    можно передать конкретный индекс.
    """
    positions = [m.start() for m in re.finditer(re.escape(start_marker), text)]
    if not positions:
        raise ValueError(f"Маркер начала раздела не найден: {start_marker!r}")
    start = positions[occurrence]
    if end_marker is None:
        return text[start:]
    end = text.find(end_marker, start + len(start_marker))
    if end == -1:
        return text[start:]
    return text[start:end]


def split_label_value_row(label_line: str, value_line: str, labels: list[str]) -> dict[str, str]:
    """
    Разбирает пару «строка заголовков колонок» / «строка значений» из
    layout-текста, используя символьную x-позицию каждого заголовка
    как границу колонки. Работает только для ОДНОСТРОЧНЫХ значений —
    для многострочных значений (частый случай в карточках ОКБ) этого
    недостаточно, см. extract_labeled_block() в okb_adapter.py, который
    берёт диапазон строк, а не одну строку.
    """
    positions = []
    for label in labels:
        idx = label_line.find(label)
        if idx == -1:
            continue
        positions.append((idx, label))
    positions.sort()
    result = {}
    for i, (start_col, label) in enumerate(positions):
        end_col = positions[i + 1][0] if i + 1 < len(positions) else len(value_line)
        result[label] = value_line[start_col:end_col].strip()
    return result


DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
AMOUNT_RE = re.compile(r"^[\d\s.,]+\s*(RUB|р\.)$")
GUID_TAIL_RE = re.compile(r"^[0-9a-f]{4,}-[0-9a-f]$", re.I)
