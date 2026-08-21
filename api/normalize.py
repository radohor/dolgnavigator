"""
Общий слой нормализации -- поверх сырых полей, извлечённых
bureau/format-specific адаптерами (nbki_adapter, scoring_*_adapter,
okb_*_adapter). Ничего здесь не знает про PDF, regex по документу или
структуру конкретного бюро -- только логика "как привести уже
извлечённые факты к единому статусу/сумме/истории кредитора".

Мотивация (см. /areas/dolgnavigator.md, ревью архитектуры v22.4):
поле "Стадия рассмотрения обращения" в современных карточках НБКИ может
быть "Н/Д" одновременно с явно заполненными "Дата отказа" и "Код причины
отказа" -- то есть само поле-стадия недостоверно, а факты рядом с ним
достоверны. Раньше это решалось (или не решалось) внутри каждого
адаптера по отдельности; здесь -- один раз, для всех текущих и будущих
адаптеров.

ВАЖНО: normalize_application_status() протестирована юнит-тестами на
синтетических данных (test_normalize.py) -- она не требует PDF и не
зависит от конкретного бюро. Но её включение в nbki_adapter.py НЕ
проверено на реальном НБКИ.pdf / Наталья.pdf -- этих файлов у меня нет.
Прогоните тест-регрессию с реальными фикстурами перед деплоем.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class ApplicationStatus(str, Enum):
    REFUSED = "Отказ"
    APPROVED = "Одобрено"
    ISSUED = "Выдано"
    PENDING = "На рассмотрении"
    WITHDRAWN = "Отозвано субъектом"  # субъект сам отозвал заявку до решения источника -- не отказ кредитора
    OTHER = "Прочее"


# Значения "Стадия рассмотрения обращения" / аналогичных полей, которые
# считаем достоверными сами по себе (не нужно ничего перепроверять).
KNOWN_RAW_STAGES = {
    "Одобрено": ApplicationStatus.APPROVED,
    "Выдано": ApplicationStatus.ISSUED,
    "На рассмотрении": ApplicationStatus.PENDING,
    "Отказ": ApplicationStatus.REFUSED,
    "Аннулирована": ApplicationStatus.OTHER,
    # современный НБКИ: сделка состоялась, заявка одобрена по факту
    # заключения договора -- найдено на реальном отчёте Солтаевой
    # (2 одобренных по сводке НБКИ, без этого маппинга терялось одно).
    "Договор заключен": ApplicationStatus.APPROVED,
    # Кредит Инфо: субъект сам отозвал заявку до решения источника --
    # это не отказ кредитора, отдельный статус. Полная фраза длиннее
    # и переносится на 2 строки в PDF ("... обращения\nили отказа от
    # совершения сделки по обращению"), поэтому сверяем по префиксу
    # в normalize_application_status(), а не точным словарём здесь.
}

_WITHDRAWN_PREFIX = "Отозвано субъектом"

# Значения, которые сами по себе НИЧЕГО не говорят о статусе и требуют
# смотреть на сопутствующие факты (дата отказа, причина, решение) --
# это и есть "Н/Д" из находки по НБКИ.
UNRELIABLE_RAW_STAGES = {"Н/Д", "N/A", "", None}


def normalize_application_status(
    raw_stage: str | None,
    refusal_date: str | None = None,
    refusal_reason: str | None = None,
    approval_decision: str | None = None,
) -> tuple[ApplicationStatus, str | None]:
    """
    Возвращает (нормализованный_статус, warning_или_None).

    Приоритет ФАКТОВ над декларируемой стадией:
    1. Если сырая стадия говорит, что субъект сам отозвал заявку --
       это отдельный терминальный статус, не отказ и не "прочее".
    2. Если есть дата отказа ИЛИ код причины отказа -- это отказ,
       независимо от того, что написано в поле "стадия".
    3. Если есть решение об одобрении -- это одобрение.
    4. Если сырая стадия входит в известный закрытый перечень -- берём
       её как есть.
    5. Иначе -- "Прочее" + warning, чтобы расхождение было видно в QC,
       а не терялось молча (см. QC-требование в архитектурном ревью).
    """
    if raw_stage and raw_stage.strip().startswith(_WITHDRAWN_PREFIX):
        return ApplicationStatus.WITHDRAWN, None

    if refusal_date or refusal_reason:
        if raw_stage not in UNRELIABLE_RAW_STAGES and KNOWN_RAW_STAGES.get(raw_stage) not in (
            None, ApplicationStatus.REFUSED
        ):
            # сырая стадия говорит одно, факты -- другое: это не молчаливый
            # выбор в пользу фактов, а видимое расхождение источника.
            return ApplicationStatus.REFUSED, (
                f"источник противоречив: стадия={raw_stage!r}, но указаны "
                f"дата/причина отказа -- принят статус 'Отказ' по фактам"
            )
        return ApplicationStatus.REFUSED, None

    if approval_decision:
        return ApplicationStatus.APPROVED, None

    if raw_stage in KNOWN_RAW_STAGES:
        return KNOWN_RAW_STAGES[raw_stage], None

    return ApplicationStatus.OTHER, f"статус не нормализован: сырая стадия={raw_stage!r}"


class ContractState(str, Enum):
    OPEN = "Открыт"
    CLOSED = "Закрыт"
    OTHER = "Прочее"


# Текстовые статусы, однозначно указывающие на закрытие -- встречаются
# в "легаси"/третьем варианте карточек НБКИ (см. /areas/dolgnavigator.md:
# третий вариант карточки, поле "Статус"). Пополняется по мере находок
# на реальных отчётах, а не гадается заранее.
KNOWN_CLOSED_STATUS_TEXT = {"Счет закрыт", "Обязательство прекращено"}


def normalize_contract_state(
    actual_end_date: str | None = None,
    status_text: str | None = None,
    fact_full_execution_date: str | None = None,
    termination_basis: str | None = None,
    termination_date: str | None = None,
    acquirer_of_rights: str | None = None,
) -> tuple[ContractState, str | None]:
    """
    Единая точка определения открыт/закрыт для ЛЮБОГО бюро -- аналог
    normalize_application_status(), но для состояния договора.

    Найдено на реальных данных (Солтаева, НБКИ): один и тот же признак
    "закрыт" в одном отчёте выражается РАЗНЫМИ полями в зависимости от
    варианта карточки:
    - "современный" вариант -> Дата фактического прекращения обязательства
    - "легаси"/третий вариант -> Факт.исполн.в полн.объеме (ДРУГОЕ поле,
      которое раньше не извлекалось вообще -- то есть договор молча
      считался открытым)
    - Кредит Инфо -> Основание прекращения обязательства + Дата
      фактического прекращения обязательства (без общей сводки)

    Приоритет: любая явная дата закрытия ИЛИ известный закрытый текстовый
    статус -> CLOSED. Наличие приобретателя прав (переуступка) без явной
    даты закрытия -- тоже CLOSED, но с warning: это негласный сигнал,
    который легко пропустить (см. предупреждение из вики про переуступку --
    "Прекращение обязательства" и "Дата фактического прекращения" могут
    стоять Н/Д, а кредит фактически закрыт).
    """
    closure_dates = [d for d in (actual_end_date, fact_full_execution_date, termination_date) if d]
    if closure_dates:
        return ContractState.CLOSED, None

    if status_text in KNOWN_CLOSED_STATUS_TEXT:
        return ContractState.CLOSED, None

    if acquirer_of_rights:
        return ContractState.CLOSED, (
            "нет явной даты закрытия, но указан приобретатель прав кредитора "
            "(переуступка) -- договор закрыт по факту, дата закрытия не извлечена"
        )

    if termination_basis and not closure_dates:
        return ContractState.OTHER, (
            f"указано основание прекращения ({termination_basis!r}), но нет даты -- "
            f"состояние не определено однозначно"
        )

    return ContractState.OPEN, None


@dataclass
class CreditorEvent:
    creditor: str
    from_date: str | None
    to_date: str | None
    event: str  # "originated" | "assigned"


@dataclass
class Amount:
    """
    Явный тип суммы: различает "источник прямо указал 0" и "поле
    отсутствует" -- см. README v22.4 про "Размер/лимит: RUB 0" в НБКИ.
    Раньше это чинилось точечно в одном адаптере; теперь -- общий тип,
    который любой адаптер может переиспользовать без повторной реализации.
    """
    kind: str  # "explicit_zero" | "missing" | "value"
    value: float | None = None

    @classmethod
    def from_raw(cls, raw: str | None) -> "Amount":
        if raw is None or not str(raw).strip():
            return cls(kind="missing")
        cleaned = str(raw).replace(" ", "").replace(",", ".")
        cleaned = "".join(ch for ch in cleaned if ch.isdigit() or ch == ".")
        if not cleaned:
            return cls(kind="missing")
        val = float(cleaned)
        if val == 0:
            return cls(kind="explicit_zero", value=0.0)
        return cls(kind="value", value=val)

    def display(self) -> str:
        if self.kind == "missing":
            return "сумма не определена"
        if self.kind == "explicit_zero":
            return "лимит 0 ₽ по источнику"
        return f"{self.value:,.2f} ₽".replace(",", " ")


def build_creditor_history(records: list[dict], uid_key: str = "uid",
                            creditor_key: str = "creditor", date_key: str = "start_date") -> dict[str, list[CreditorEvent]]:
    """
    Группирует записи по UID (один UID = одно обязательство) и строит
    историю смены кредитора при уступке права требования -- НЕ отбрасывая
    более ранние записи как "дубли", а сохраняя их как события истории.
    Ожидает уже отсортированные по дате записи одного бюро.
    """
    by_uid: dict[str, list[dict]] = {}
    for r in records:
        uid = r.get(uid_key)
        if not uid:
            continue
        by_uid.setdefault(uid, []).append(r)

    result: dict[str, list[CreditorEvent]] = {}
    for uid, group in by_uid.items():
        group_sorted = sorted(group, key=lambda r: r.get(date_key) or "")
        events = []
        prev_creditor = None
        for i, r in enumerate(group_sorted):
            creditor = r.get(creditor_key)
            event_type = "originated" if i == 0 else "assigned"
            to_date = group_sorted[i + 1].get(date_key) if i + 1 < len(group_sorted) else None
            events.append(CreditorEvent(
                creditor=creditor, from_date=r.get(date_key), to_date=to_date, event=event_type,
            ))
            prev_creditor = creditor
        result[uid] = events
    return result
