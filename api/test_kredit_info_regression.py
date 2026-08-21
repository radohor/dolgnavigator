"""
Regression-тест Кредит Инфо на реальном отчёте (Постельный И.А.).
PDF не входит в комплект -- задайте BKI_KREDIT_INFO_FIXTURE.
"""
import os
import pytest

from kredit_info_adapter import get_text, parse_contracts, parse_applications, parse_queries

FIXTURE = os.environ.get("BKI_KREDIT_INFO_FIXTURE")

pytestmark = pytest.mark.skipif(
    not FIXTURE, reason="Set BKI_KREDIT_INFO_FIXTURE to run against the real Kredit Info PDF.",
)


def test_postelny_counts():
    text = get_text(FIXTURE)
    contracts = parse_contracts(text)
    apps = parse_applications(text)
    queries = parse_queries(text)

    assert len(contracts) == 2
    assert len({c["uid"] for c in contracts if c["uid"]}) == 2
    assert all(c["is_closed"] for c in contracts)  # оба договора закрыты в тестовом отчёте

    assert len(apps) == 13
    statuses = [a["status"] for a in apps]
    assert statuses.count("Отказ") == 8
    assert statuses.count("Одобрено") == 3
    assert statuses.count("Отозвано субъектом") == 1
    assert statuses.count("На рассмотрении") == 1
    assert all(not a["status_warning"] for a in apps)

    assert len(queries) == 14
