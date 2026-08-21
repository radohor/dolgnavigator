"""
Regression-тест НБКИ на реальных фикстурах Солтаевой и Натальи.

Контрольные числа взяты из NBKI_V22_3_REGRESSION.json (уже подтверждены
ранее, до правки статуса заявок в этой сессии) -- этот тест НЕ придумывает
новые ожидания, а проверяет, что фикс normalize_application_status()
не сдвинул то, что уже было проверено.

ВНИМАНИЕ: raw_contracts / applications / queries НИЖЕ -- это числа ДО
исправления статуса заявок. Возможно, что после фикса количество
применений с status == "Отказ" увеличится на 15 (как описано в находке) --
это ОЖИДАЕМОЕ изменение состава/значений внутри записей, а не изменение
их числа. Отдельно проверяем это через application_refused_count ниже.

Запуск:
    BKI_NBKI_SOLTAEVA_FIXTURE=/path/to/НБКИ.pdf \
    BKI_NBKI_NATALIA_FIXTURE=/path/to/Наталья.pdf \
    python3 -m pytest test_nbki_regression.py -v
"""
import os
import pytest

from nbki_adapter import parse_nbki

SOLTAEVA_PDF = os.environ.get("BKI_NBKI_SOLTAEVA_FIXTURE")
NATALIA_PDF = os.environ.get("BKI_NBKI_NATALIA_FIXTURE")

pytestmark = pytest.mark.skipif(
    not (SOLTAEVA_PDF and NATALIA_PDF),
    reason="Set BKI_NBKI_SOLTAEVA_FIXTURE and BKI_NBKI_NATALIA_FIXTURE to run against real NBKI PDFs.",
)


def test_soltaeva_counts_unchanged_by_status_fix():
    result = parse_nbki(SOLTAEVA_PDF)
    assert len(result["contracts"]) == 4
    assert len(result["applications"]) == 19
    assert len(result["queries"]) == 16
    assert result["contract_qc"]["reported_zero_amounts"] == 2
    assert result["contract_qc"]["overdue_date_candidates"] == 2


def test_soltaeva_application_refusal_count_after_status_fix():
    """
    Сама находка: сводка НБКИ = 19 обращений / 2 одобренных, значит
    ожидаемое число отказов = 17 (19 - 2), а не то меньшее число,
    что было до фикса из-за игнорирования Дата отказа/Код причины.
    """
    result = parse_nbki(SOLTAEVA_PDF)
    apps = result["applications"]
    refused = sum(1 for a in apps if a["status"] == "Отказ")
    approved = sum(1 for a in apps if a["status"] == "Одобрено")
    unresolved = sum(1 for a in apps if a["status"] is None or a.get("status_warning"))
    assert approved == 2, f"ожидалось 2 одобренных, получено {approved}"
    assert refused == 17, (
        f"ожидалось 17 отказов (19 обращений - 2 одобрения), получено {refused}; "
        f"unresolved={unresolved} -- если > 0, часть статусов не нормализовалась"
    )


def test_natalia_counts_unchanged_by_status_fix():
    result = parse_nbki(NATALIA_PDF)
    assert len(result["contracts"]) == 11
    contracts = result["contracts"]
    non_null_uids = {(c.get("uid") or "").lower() for c in contracts if c.get("uid")}
    null_uid_count = sum(1 for c in contracts if not c.get("uid"))
    # Обязательство без УИД нельзя дедуплицировать по УИД -- оно остаётся
    # отдельным обязательством само по себе, а не "теряется". Найдено
    # на реальных данных Натальи: карточка №10 (Банк ВТБ) без УИД --
    # без этого учёта unique-count ошибочно занижался на 1.
    unique_obligations = len(non_null_uids) + null_uid_count
    assert unique_obligations == 10, (
        f"ожидалось 10 уникальных обязательств "
        f"({len(non_null_uids)} уникальных УИД + {null_uid_count} без УИД)"
    )
    assert len(result["applications"]) == 45
    assert len(result["queries"]) == 99
    assert result["contract_qc"]["duplicate_uid_groups"] == 1
    assert result["contract_qc"]["duplicate_uid_rows"] == 1


def test_natalia_creditor_cession_still_detected():
    """Т-Банк -> Феникс: одна и та же УИД, разные кредиторы в истории."""
    result = parse_nbki(NATALIA_PDF)
    target_uid = "6868270f-371f-1a46-b9e0-e1876e128cfa-a"
    matching = [c for c in result["contracts"] if (c.get("uid") or "").lower() == target_uid]
    assert len(matching) == 2
    creditors = {c["creditor"] for c in matching}
    assert any("Феникс" in c for c in creditors)
    assert any("ТБАНК" in c.upper() or "Т-БАНК" in c.upper() for c in creditors)


def test_soltaeva_open_contracts_match_bureau_summary():
    """
    QC-правило: расчётное число открытых обязательств (после дедупа по
    УИД) должно совпасть со сводкой самого НБКИ ("Открытых: 0").
    """
    from nbki_adapter import open_contract_count
    result = parse_nbki(SOLTAEVA_PDF)
    open_count = open_contract_count(result["contracts"])
    assert open_count == 0, f"сводка НБКИ говорит 'Открытых: 0', получено {open_count}"


def test_natalia_open_contracts_match_bureau_summary():
    """
    Тот же QC, но на кейсе с переуступкой: карточка первоначального
    кредитора (АО "ТБАНК") сама по себе не даёт сигнала закрытия --
    он есть только у принявшего долг (ООО "ПКО "Феникс"). Подсчёт по
    сырым карточкам без дедупа по УИД даёт 1 вместо 0.
    """
    from nbki_adapter import open_contract_count
    result = parse_nbki(NATALIA_PDF)
    open_count = open_contract_count(result["contracts"])
    assert open_count == 0, f"сводка НБКИ говорит 'Открытых: 0', получено {open_count}"
