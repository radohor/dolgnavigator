"""
Приём PDF из Telegram без прохождения через тело HTTP-запроса функции --
обход лимита тела Vercel Functions (~4,5 МБ), который не решается
поднятием MAX_FILE_BYTES в коде (см. архитектурное ревью v22.4).

Telegram Bot API отдаёт файлы до 20 МБ по прямой ссылке -- этого
достаточно для отчёта Калашниковой (~8,6 МБ). Схема:

1. Бот получает документ, у Telegram есть file_id (несколько байт).
2. Клиент (фронтенд бота / Mini App) шлёт в /api/parse-bki НЕ сам файл,
   а {"telegram_file_id": "..."} -- тело запроса крошечное, лимит
   Vercel вообще не задействован.
3. Функция сама обращается к Telegram (getFile -> file_path -> прямое
   скачивание) и передаёт полученный путь в parse_pdf() как обычно.

НЕ проверено end-to-end с реальным ботом (нет токена/окружения в этой
сессии) -- логика скачивания протестирована отдельно на моках
(test_telegram_fetch.py), а не на реальном Telegram API.
"""
from __future__ import annotations
import os
import tempfile
import urllib.request
import urllib.error

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_FILE_BASE = "https://api.telegram.org/file"
TELEGRAM_MAX_BOT_FILE_BYTES = 20_000_000  # официальный лимит Telegram Bot API


class TelegramFetchError(ValueError):
    pass


def _bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise TelegramFetchError("TELEGRAM_BOT_TOKEN не задан в окружении функции.")
    return token


def get_file_path(file_id: str, *, _http_get=None) -> str:
    """
    Telegram getFile: по file_id возвращает временный file_path
    (действителен ограниченное время, поэтому скачивание должно
    происходить сразу же, без сохранения file_path впрок).
    """
    http_get = _http_get or _real_http_get_json
    token = _bot_token()
    url = f"{TELEGRAM_API_BASE}/bot{token}/getFile?file_id={file_id}"
    data = http_get(url)
    if not data.get("ok"):
        raise TelegramFetchError(f"Telegram getFile отклонён: {data.get('description', 'без описания')}")
    result = data["result"]
    file_size = result.get("file_size")
    if file_size and file_size > TELEGRAM_MAX_BOT_FILE_BYTES:
        raise TelegramFetchError(
            f"Файл {file_size} байт превышает лимит Telegram Bot API "
            f"({TELEGRAM_MAX_BOT_FILE_BYTES} байт) -- боту такой файл недоступен."
        )
    file_path = result.get("file_path")
    if not file_path:
        raise TelegramFetchError("Telegram не вернул file_path.")
    return file_path


def download_to_tempfile(file_path: str, *, _download=None) -> str:
    """Скачивает файл по file_path в /tmp и возвращает локальный путь."""
    download = _download or _real_download
    token = _bot_token()
    url = f"{TELEGRAM_FILE_BASE}/bot{token}/{file_path}"
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
    download(url, tmp_path)
    with open(tmp_path, "rb") as f:
        head = f.read(4)
    if head != b"%PDF":
        os.remove(tmp_path)
        raise TelegramFetchError("Скачанный файл не является PDF (не начинается с %PDF).")
    return tmp_path


def fetch_pdf_by_telegram_file_id(file_id: str, *, _http_get=None, _download=None) -> str:
    """Полный путь: file_id -> локальный путь к PDF на диске функции."""
    file_path = get_file_path(file_id, _http_get=_http_get)
    return download_to_tempfile(file_path, _download=_download)


def _real_http_get_json(url: str) -> dict:
    import json
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _real_download(url: str, dest_path: str) -> None:
    urllib.request.urlretrieve(url, dest_path)
