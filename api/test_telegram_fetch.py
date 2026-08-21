"""
Тесты telegram_fetch.py на моках HTTP -- никакого реального обращения
к Telegram. Проверяет ЛОГИКУ (обработка ошибок, лимиты, сборка URL),
а не факт, что реальный Telegram API отвечает так, как я предполагаю
по документации. Перед продакшеном нужен один ручной прогон с реальным
ботом и реальным file_id.
"""
import os
import pytest

from telegram_fetch import (
    get_file_path, download_to_tempfile, fetch_pdf_by_telegram_file_id,
    TelegramFetchError, TELEGRAM_MAX_BOT_FILE_BYTES,
)


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")


def test_get_file_path_happy_path():
    def fake_http_get(url):
        assert "test-token" in url
        return {"ok": True, "result": {"file_path": "documents/file_1.pdf", "file_size": 1000}}
    path = get_file_path("abc123", _http_get=fake_http_get)
    assert path == "documents/file_1.pdf"


def test_get_file_path_rejects_oversized_file():
    def fake_http_get(url):
        return {"ok": True, "result": {"file_path": "documents/big.pdf",
                                        "file_size": TELEGRAM_MAX_BOT_FILE_BYTES + 1}}
    with pytest.raises(TelegramFetchError, match="превышает лимит"):
        get_file_path("abc123", _http_get=fake_http_get)


def test_get_file_path_surfaces_telegram_error():
    def fake_http_get(url):
        return {"ok": False, "description": "file not found"}
    with pytest.raises(TelegramFetchError, match="file not found"):
        get_file_path("bad-id", _http_get=fake_http_get)


def test_missing_bot_token_raises_clearly(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(TelegramFetchError, match="TELEGRAM_BOT_TOKEN"):
        get_file_path("abc123", _http_get=lambda url: {})


def test_download_rejects_non_pdf_content(tmp_path):
    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"not a pdf, just html error page")
    with pytest.raises(TelegramFetchError, match="не является PDF"):
        download_to_tempfile("documents/file_1.pdf", _download=fake_download)


def test_download_accepts_real_pdf_header(tmp_path):
    def fake_download(url, dest_path):
        with open(dest_path, "wb") as f:
            f.write(b"%PDF-1.7 rest of content...")
    path = download_to_tempfile("documents/file_1.pdf", _download=fake_download)
    assert os.path.exists(path)
    os.remove(path)


def test_full_flow_file_id_to_local_pdf():
    def fake_http_get(url):
        return {"ok": True, "result": {"file_path": "documents/report.pdf", "file_size": 8_600_000}}
    def fake_download(url, dest_path):
        assert "documents/report.pdf" in url
        with open(dest_path, "wb") as f:
            f.write(b"%PDF-1.4 synthetic large report placeholder")
    path = fetch_pdf_by_telegram_file_id("kalashnikova-file-id", _http_get=fake_http_get, _download=fake_download)
    assert os.path.exists(path)
    os.remove(path)
