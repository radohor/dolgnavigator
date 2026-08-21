"""
Юнит-тесты normalize.py -- полностью синтетические, PDF не нужны.
Отдельно от test_regression.py (который требует реальные фикстуры).
"""
from normalize import normalize_application_status, ApplicationStatus, Amount, build_creditor_history


def test_modern_nbki_refusal_with_na_stage():
    """Ровно кейс из находки: 15 карточек, Стадия = Н/Д, но дата+причина отказа есть."""
    status, warning = normalize_application_status(
        raw_stage="Н/Д", refusal_date="12.11.2024", refusal_reason="КМ-01",
    )
    assert status == ApplicationStatus.REFUSED
    assert warning is None  # Н/Д в UNRELIABLE_RAW_STAGES -- это ожидаемо, не расхождение


def test_legacy_nbki_refusal_no_stage_field():
    status, warning = normalize_application_status(raw_stage=None, refusal_date="01.01.2020")
    assert status == ApplicationStatus.REFUSED


def test_approved_takes_priority_over_missing_stage():
    status, warning = normalize_application_status(raw_stage=None, approval_decision="Да")
    assert status == ApplicationStatus.APPROVED


def test_known_stage_passthrough():
    status, warning = normalize_application_status(raw_stage="На рассмотрении")
    assert status == ApplicationStatus.PENDING
    assert warning is None


def test_unknown_stage_flagged_not_silently_dropped():
    status, warning = normalize_application_status(raw_stage="Экзотический статус БКИ")
    assert status == ApplicationStatus.OTHER
    assert warning is not None


def test_contradictory_source_flagged():
    """Стадия говорит 'Одобрено', но есть дата отказа -- источник противоречив."""
    status, warning = normalize_application_status(
        raw_stage="Одобрено", refusal_date="12.11.2024",
    )
    assert status == ApplicationStatus.REFUSED
    assert warning is not None and "противоречив" in warning


def test_amount_explicit_zero_vs_missing():
    assert Amount.from_raw("0").kind == "explicit_zero"
    assert Amount.from_raw("RUB 0").kind == "explicit_zero"
    assert Amount.from_raw(None).kind == "missing"
    assert Amount.from_raw("").kind == "missing"
    assert Amount.from_raw("-").kind == "missing"
    assert Amount.from_raw("15 000.00").kind == "value"
    assert Amount.from_raw("15 000.00").value == 15000.00


def test_amount_display_distinguishes_zero_from_missing():
    assert Amount.from_raw("0").display() != Amount.from_raw(None).display()


def test_creditor_history_cession_t_bank_to_feniks():
    """Синтетический аналог находки Т-Банк -> Феникс: один UID, смена кредитора."""
    records = [
        {"uid": "abc-1", "creditor": "АО \"ТБанк\"", "start_date": "01.01.2022"},
        {"uid": "abc-1", "creditor": "ООО \"Феникс\"", "start_date": "15.06.2023"},
    ]
    history = build_creditor_history(records)
    assert len(history["abc-1"]) == 2
    assert history["abc-1"][0].creditor == "АО \"ТБанк\""
    assert history["abc-1"][0].event == "originated"
    assert history["abc-1"][1].creditor == "ООО \"Феникс\""
    assert history["abc-1"][1].event == "assigned"
    assert history["abc-1"][0].to_date == "15.06.2023"  # переход зафиксирован


def test_creditor_history_single_record_no_history_needed():
    records = [{"uid": "x-1", "creditor": "Банк А", "start_date": "01.01.2022"}]
    history = build_creditor_history(records)
    assert len(history["x-1"]) == 1
    assert history["x-1"][0].event == "originated"


def test_nbki_modern_block_synthetic_reproduction():
    """
    Регресс-тест на СИНТЕТИЧЕСКОМ тексте, воспроизводящем структуру
    находки (Стадия=Н/Д при заполненных дате/причине отказа), прогнанный
    через реальную _modern_application_blocks(). НЕ заменяет прогон на
    настоящем НБКИ.pdf/Наталья.pdf -- тех файлов нет, этот тест только
    подтверждает, что путь кода исправлен для конкретной структуры полей.
    """
    from nbki_adapter import _modern_application_blocks

    text = """
Вид участия: Заемщик
Дата обращения: 12.11.2024
Запрошенная сумма: 50000
Способ обращения: Дистанционно
УИД обращения: 01efa164-8f04-14d0-900c-a52383bb5539-4
Стадия рассмотрения обращения: Н/Д
Дата отказа: 13.11.2024
Код причины отказа: КМ-01
Полное наименование: ООО МКК ТЕСТ
Сокращенное наименование: МКК ТЕСТ
Вид участия: Заемщик
Дата обращения: 01.10.2024
Запрошенная сумма: 100000
Способ обращения: Дистанционно
УИД обращения: 772a21d5-90ba-18bc-aafa-042b7aa88a99-f
Стадия рассмотрения обращения: Одобрено
Полное наименование: ПАО СБЕРБАНК
Сокращенное наименование: СБЕРБАНК
"""
    apps = _modern_application_blocks(text)
    assert len(apps) == 2
    assert apps[0]["status"] == "Отказ"
    assert apps[1]["status"] == "Одобрено"


def test_contract_state_modern_closure_date():
    from normalize import normalize_contract_state, ContractState
    state, warning = normalize_contract_state(actual_end_date="09-06-2025")
    assert state == ContractState.CLOSED
    assert warning is None


def test_contract_state_legacy_status_text_closed():
    """Третий вариант карточки НБКИ: 'Статус: Счет закрыт' без прекращения-даты в других полях."""
    from normalize import normalize_contract_state, ContractState
    state, warning = normalize_contract_state(status_text="Счет закрыт")
    assert state == ContractState.CLOSED


def test_contract_state_legacy_fact_execution_date():
    """
    Ровно кейс Солтаевой: договор №4 (АО Россельхозбанк, Потребит.кредит) --
    'Факт.исполн.в полн.объеме: 17-02-2021' -- поле, которое раньше не
    извлекалось вообще.
    """
    from normalize import normalize_contract_state, ContractState
    state, warning = normalize_contract_state(fact_full_execution_date="17-02-2021")
    assert state == ContractState.CLOSED


def test_contract_state_open_no_signals():
    from normalize import normalize_contract_state, ContractState
    state, warning = normalize_contract_state()
    assert state == ContractState.OPEN


def test_contract_state_cession_without_explicit_date_flags_warning():
    """Переуступка без явной даты закрытия -- закрыт, но с явным warning, не молча."""
    from normalize import normalize_contract_state, ContractState
    state, warning = normalize_contract_state(acquirer_of_rights="ООО «ПКО «Феникс»")
    assert state == ContractState.CLOSED
    assert warning is not None
