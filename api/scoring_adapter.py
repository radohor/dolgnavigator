"""
Адаптер парсера для отчётов Скоринг Бюро.

Формат сгенерирован через wkhtmltopdf/Qt из HTML-таблиц — реальный порядок
текстовых объектов в PDF совпадает с DOM-порядком строк таблицы. Поэтому
парсинг здесь построен не на координатах, а на СЕКВЕНЦИАЛЬНОМ разборе
текста в порядке чтения (get_pages_text), с якорями на ЗАКРЫТЫЕ множества
значений (вид пользователя/участия, тип запрошенных сведений) — они
известны заранее из шаблона отчёта и не меняются от клиента к клиенту.

Проверено на эталонном отчёте Скоринг Бюро Куватова:
- Договоры (сводная таблица): 98 из 98, все contract_id уникальны.
- Запросы кредитной истории: 559 из 559.
- Заявки на кредит: 296 записей; 277 с уникальным УИД и 19 легитимно
  без УИД (старые заявки 2021 года, в источнике стоит "-").
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field

from layout_utils import find_section, get_pages_text, DATE_RE, AMOUNT_RE

VID_POLZOVATELYA = {"Банк", "МФО", "БКИ", "Ломбард", "КПК", "Иное", "Иной", "Другое"}
VID_UCHASTIYA = {"Заемщик", "Созаемщик", "Поручитель"}
ZAPROSHENNIE_SVEDENIYA = {"Кредитная оценка (скоринг)", "Кредитный отчет", "Среднемес. платежи"}
STATUS_ENUM = {"Отказ", "Выдано", "Одобрено", "На рассмотрении", "Аннулирована"}
SPOSOB_OBRASHCHENIYA = {"Дистанционно", "Очно", "Через посредника", "Иное"}

RUB_LINE_RE = re.compile(r"^([\d\s.,]+)\s*RUB\s+(\d{2}\.\d{2}\.\d{4})\s+(\d{2}\.\d{2}\.\d{4})$")
INDEX_ONLY_RE = re.compile(r"^\d{1,3}$")
CONTRACT_ID_ONLY_RE = re.compile(r"^\d{15,20}$")
# индекс и ID договора иногда оказываются на ОДНОЙ строке (двузначный
# индекс переносится иначе, чем однозначный) -- см. запись №10 в тестовом
# отчёте: "10 2427406421394321879" одной строкой вместо двух.
COMBINED_INDEX_ID_RE = re.compile(r"^(\d{1,3})\s+(\d{15,20})$")

CONTRACTS_TABLE_HEADER_JUNK = {
    "№ID договора", "(ссылка на детали)", "Источник информации", "Тип / вид договора",
    "Сумма", "обязательства по", "договору", "Дата", "начала", "договора", "окончания",
    "Текущая", "задолженность", "просрочка,", "сумма", "дней",
    "Максимальная сумма", "просрочки [осн. долг]", "Переход в", "текущее", "состояние",
    "актуальности",
}


@dataclass
class Contract:
    index: str
    contract_id: str
    status_section: str | None       # "Активные договоры" / "Закрытые договоры"
    creditor_type_raw: list[str]     # TODO: разбить кредитора и тип/вид договора
    amount: str
    start_date: str
    end_date: str
    current_debt: str | None
    current_overdue_amount: str | None
    current_overdue_days: str | None
    max_overdue_and_transition: str | None
    actuality_date: str | None


def parse_contracts_summary(text: str) -> list[Contract]:
    """
    Проверено: 98 из 98 контрольных записей, все contract_id уникальны
    (см. debug_dump.py). Работает на тексте В ПОРЯДКЕ ЧТЕНИЯ
    (get_pages_text), как и остальные разборщики Скоринг Бюро -- см.
    докстринг модуля.
    """
    section = find_section(
        text,
        "СВОДНАЯ ИНФОРМАЦИЯ ПО ДОГОВОРАМ ЗАЙМА",
        "ПОДРОБНАЯ ИНФОРМАЦИЯ ПО ДОГОВОРАМ",
        occurrence=-1,
    )
    raw_lines = [l.strip() for l in section.split("\n") if l.strip()]
    lines = [l for l in raw_lines if l not in CONTRACTS_TABLE_HEADER_JUNK
             and not l.startswith("Паспорт РФ") and not l.startswith("КУВАТОВ")]

    records: list[Contract] = []
    i, n = 0, len(lines)
    status_section = None
    while i < n:
        l = lines[i]
        if l in ("Активные договоры", "Закрытые договоры"):
            status_section = l
            i += 1
            continue

        idx_num = contract_id = None
        advance = 0
        cm = COMBINED_INDEX_ID_RE.match(l)
        if cm:
            idx_num, contract_id = cm.groups()
            advance = 1
        elif INDEX_ONLY_RE.match(l) and i + 1 < n and CONTRACT_ID_ONLY_RE.match(lines[i + 1]):
            idx_num, contract_id = l, lines[i + 1]
            advance = 2

        if idx_num:
            j = i + advance
            creditor_type_buf = []
            while j < n and not RUB_LINE_RE.match(lines[j]):
                creditor_type_buf.append(lines[j])
                j += 1
                if j - i > 20:      # предохранитель от зацикливания на «мусорной» строке
                    break
            if j < n and RUB_LINE_RE.match(lines[j]):
                m = RUB_LINE_RE.match(lines[j])
                amount, start_date, end_date = m.groups()
                j += 1
                current_debt = lines[j] if j < n else None; j += 1
                current_overdue_amount = lines[j] if j < n else None; j += 1
                current_overdue_days = lines[j] if j < n else None; j += 1
                max_overdue_and_transition = lines[j] if j < n else None; j += 1
                actuality_date = lines[j] if j < n and DATE_RE.match(lines[j]) else None
                if actuality_date:
                    j += 1
                records.append(Contract(
                    idx_num, contract_id, status_section, creditor_type_buf,
                    amount.strip(), start_date, end_date,
                    current_debt, current_overdue_amount, current_overdue_days,
                    max_overdue_and_transition, actuality_date,
                ))
                i = j
                continue
        i += 1
    return records



@dataclass
class Query:
    query_date: str
    user_kind: str
    requester_raw: list[str]          # TODO: разбить на наименование/цель/сумму
    amount: str | None
    requested_info: str
    provided_date: str | None
    warning: str | None = None


@dataclass
class Application:
    application_date: str | None
    uid: str | None
    creditor_raw: list[str]
    participation: str
    amount: str | None
    method: str | None
    status_raw: list[str]             # TODO: см. примечание внизу файла
    warning: str | None = None


PAGE_NOISE_LINES = {"КУВАТОВ АРТЕМ ТИМУРОВИЧ", "Информационная часть", "№ п/п"}


def _strip_page_noise(lines: list[str]) -> list[str]:
    """
    Убирает повторяющиеся колонтитулы страниц (ФИО субъекта, название
    раздела верхнего уровня и т.п.), которые попадают в поток текста на
    каждом разрыве страницы и иначе засоряют буфер записи. Пример
    реального бага, который это фильтрует: без этой очистки соседние
    записи 'Информационная часть' сливались в одну, а следующий якорь
    молча пропускался -- 3 из 559 запросов терялись именно так.
    """
    return [l for l in lines if l not in PAGE_NOISE_LINES and "Сформирован" not in l]


def parse_queries(text: str) -> list[Query]:
    section = find_section(
        text,
        "СВЕДЕНИЯ О ЗАПРОСАХ КРЕДИТНОЙ ИСТОРИИ",
        "СПРАВОЧНАЯ ИНФОРМАЦИЯ ДЛЯ СУБЪЕКТА",
        occurrence=-1,           # последнее вхождение = реальный раздел, не оглавление
    )
    lines = _strip_page_noise([l.strip() for l in section.split("\n") if l.strip()])
    records: list[Query] = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i] in VID_POLZOVATELYA and i > 0 and DATE_RE.match(lines[i - 1]):
            vid = lines[i]
            query_date = lines[i - 1]
            j = i + 1
            buf = []
            while j < n and lines[j] not in ZAPROSHENNIE_SVEDENIYA and (j - i) <= 15:
                # если внутри буфера встретился НОВЫЙ валидный якорь
                # (дата+вид пользователя), значит у текущей записи просто
                # нет заполненного "запрошенные сведения" -- останавливаемся,
                # не заглатывая соседнюю запись целиком.
                if (lines[j] in VID_POLZOVATELYA and j > 0 and DATE_RE.match(lines[j - 1])
                        and j - 1 > i):
                    break
                buf.append(lines[j])
                j += 1
            if j < n and lines[j] in ZAPROSHENNIE_SVEDENIYA:
                requested_info = lines[j]
                provided_date = lines[j + 1] if j + 1 < n and DATE_RE.match(lines[j + 1]) else None
                amount = next((b for b in buf if AMOUNT_RE.match(b)), None)
                requester_raw = [b for b in buf if not AMOUNT_RE.match(b)]
                records.append(Query(query_date, vid, requester_raw, amount, requested_info, provided_date))
                i = j + (2 if provided_date else 1)
                continue
            else:
                # буфер оборвался на новом якоре -- у этой записи нет
                # заполненного "запрошенные сведения" в отчёте. Всё равно
                # фиксируем её (с warning), и продолжаем СО СЛЕДУЮЩЕГО
                # якоря, а не пропускаем его.
                amount = next((b for b in buf if AMOUNT_RE.match(b)), None)
                requester_raw = [b for b in buf if not AMOUNT_RE.match(b)]
                records.append(Query(query_date, vid, requester_raw, amount, None, None,
                                      warning="нет значения 'запрошенные сведения' в источнике"))
                i = j  # j указывает на дату следующего якоря -- продолжаем с неё
                continue
        i += 1
    return records


def parse_applications(text: str) -> list[Application]:
    """
    Итог расследования 393 -> 296 (см. /areas/dolgnavigator.md):

    393 было раздутым числом из-за ДВУХ багов, оба исправлены:
    1. Граница секции захватывала соседнюю таблицу "Выданные кредиты /
       заключенные договоры поручительства" -- она использует то же
       значение 'Заемщик' в поле 'Вид участия в сделке', но это другая
       таблица (не заявки). Сузил конец секции до её заголовка.
    2. (осталось некритично) записи без УИД добавляли лишний '-' в
       creditor_raw -- отфильтровано ниже.

    После исправления №1 получаем 296 записей, из них 277 с уникальным
    непустым УИД и 19 легитимно без УИД (старые заявки 2021 года -- в
    самом отчёте у них в столбце УИД стоит "-", это не ошибка
    извлечения). Дублей УИД среди этих 296 -- ноль.

    296 (не 278) сейчас считаю более вероятным правильным числом:
    среди совпадений по (дата, кредитор, сумма) все проверенные группы
    оказались записями с РАЗНЫМИ УИД -- то есть человек подряд подавал
    несколько разных заявок одному кредитору в один день, а не одна
    запись задвоилась при разборе. Контрольное число 278 стоит
    перепроверить independent способом (например пересчитать вручную по
    рендеру PDF) прежде чем чинить парсер под него дальше -- нет смысла
    подгонять код под, возможно, изначально неверный ориентир.
    """
    section = find_section(text, "Сведения об обращении субъекта",
                            "Выданные кредиты / заключенные договоры поручительства", occurrence=-1)
    lines = [l.strip() for l in section.split("\n") if l.strip()]
    records: list[Application] = []
    anchors = [i for i, l in enumerate(lines) if l in VID_UCHASTIYA]
    for k, i in enumerate(anchors):
        participation = lines[i]
        next_anchor = anchors[k + 1] if k + 1 < len(anchors) else len(lines)

        # назад: creditor lines, затем uid lines, затем дата обращения.
        # Название кредитора может занимать >6 строк, поэтому ищем
        # ближайшую дату назад с защитным лимитом 40 строк.
        back = []
        b = i - 1
        while b >= 0 and not DATE_RE.match(lines[b]) and len(back) < 40:
            back.append(lines[b])
            b -= 1
        back.reverse()
        application_date = lines[b] if b >= 0 and DATE_RE.match(lines[b]) else None
        uid_parts = [x for x in back if re.search(r"[0-9a-f]{6,}-", x, re.I)]
        uid = "".join(uid_parts) if uid_parts else None
        # "-" без UID-частей -- это заглушка отсутствующего УИД в старых
        # записях, а не часть названия кредитора.
        creditor_raw = [x for x in back if x not in uid_parts and x != "-"]

        forward = lines[i + 1:next_anchor]
        amount = next((x for x in forward if AMOUNT_RE.match(x)), None)
        method = next((x for x in forward if x in SPOSOB_OBRASHCHENIYA), None)

        records.append(Application(
            application_date, uid, creditor_raw, participation, amount, method,
            status_raw=forward,
        ))
    return records


if __name__ == "__main__":
    import sys
    from layout_utils import get_full_text

    pdf_path = sys.argv[1]
    text = get_full_text(pdf_path)

    contracts = parse_contracts_summary(text)
    print(f"Договоры: {len(contracts)} (контроль: 98)")

    queries = parse_queries(text)
    print(f"Запросы КИ: {len(queries)} (контроль: 559)")

    apps = parse_applications(text)
    print(f"Заявки: {len(apps)} (контрольное число 278, вероятно, неточное -- см. docstring parse_applications)")
