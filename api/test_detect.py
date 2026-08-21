"""
Тесты detect.py. Юнит-часть (sniff-функции) полностью синтетическая.
Интеграционная часть использует реальные PDF Куватова, которые есть
в этой сессии -- единственные два формата, которые я могу подтвердить
end-to-end прямо сейчас.
"""
import os
import pytest

from detect import (
    detect, FormatMatch, _sniff_nbki, _sniff_scoring_legacy,
    _sniff_scoring_2026, _sniff_okb,
)

OKB_PDF = "/mnt/user-data/uploads/0__Кредитный_отчет_ОКБ_от_20_12_2024_г__Куватов_АТ_-_253_листа.pdf"
SCORING_PDF = "/mnt/user-data/uploads/0_1__Кредитный_отчет_Скоринг_бюро_от_20_12_2024_г__Куватов_АТ_-_136_листов.pdf"


# ---- юнит: sniff-функции на синтетических пробах ----

def test_sniff_nbki_positive():
    assert _sniff_nbki("КРЕДИТНЫЙ ОТЧЕТ ДЛЯ СУБЪЕКТА ... ОБРАЩАЙТЕСЬ В НБКИ ...")


def test_sniff_nbki_negative_missing_second_marker():
    assert not _sniff_nbki("КРЕДИТНЫЙ ОТЧЕТ ДЛЯ СУБЪЕКТА без второго маркера")


def test_sniff_scoring_2026_requires_two_of_three_markers():
    assert _sniff_scoring_2026("ДЕЙСТВУЮЩИЕ КРЕДИТЫ ... ЗАКРЫТЫЕ КРЕДИТЫ ...")
    assert not _sniff_scoring_2026("ДЕЙСТВУЮЩИЕ КРЕДИТЫ ... больше ничего")


def test_sniffs_are_mutually_exclusive_on_synthetic_probes():
    """Ни один синтетический маркер одного бюро не должен триггерить другой sniff."""
    nbki_probe = "КРЕДИТНЫЙ ОТЧЕТ ДЛЯ СУБЪЕКТА ОБРАЩАЙТЕСЬ В НБКИ"
    assert _sniff_nbki(nbki_probe)
    assert not _sniff_scoring_legacy(nbki_probe)
    assert not _sniff_okb(nbki_probe)
    assert not _sniff_scoring_2026(nbki_probe)


def test_unsupported_format_raises_loudly_not_silently():
    """detect() не должен тихо возвращать 'ближайший' формат."""
    import detect as detect_module
    original = detect_module.probe_text
    detect_module.probe_text = lambda path, pages=12: "случайный текст без маркеров БКИ"
    try:
        with pytest.raises(ValueError, match="не распознан"):
            detect_module.detect("неважно.pdf")
    finally:
        detect_module.probe_text = original


# ---- интеграция: реальные PDF Куватова ----

@pytest.mark.skipif(not os.path.exists(OKB_PDF), reason="фикстура ОКБ Куватова недоступна")
def test_detect_real_okb_kuvatov():
    assert detect(OKB_PDF) == FormatMatch("okb", "v1")


@pytest.mark.skipif(not os.path.exists(SCORING_PDF), reason="фикстура Скоринг Куватова недоступна")
def test_detect_real_scoring_kuvatov():
    assert detect(SCORING_PDF) == FormatMatch("scoring", "legacy")


@pytest.mark.skipif(not os.path.exists(SCORING_PDF), reason="фикстура Скоринг Куватова недоступна")
def test_experimental_scoring_2026_does_not_false_positive_on_legacy():
    """
    Экспериментальный sniff scoring_2026 не должен случайно сработать
    на legacy-отчёте Куватова -- иначе с include_experimental=True
    легаси-файлы уйдут не в тот адаптер.
    """
    result = detect(SCORING_PDF, include_experimental=True)
    assert result == FormatMatch("scoring", "legacy")


KREDIT_INFO_PDF = "/mnt/user-data/uploads/Кредит_Инфо.pdf"


@pytest.mark.skipif(not os.path.exists(KREDIT_INFO_PDF), reason="фикстура Кредит Инфо недоступна")
def test_detect_real_kredit_info():
    assert detect(KREDIT_INFO_PDF) == FormatMatch("kredit_info", "v1")
