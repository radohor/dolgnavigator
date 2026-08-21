from __future__ import annotations

import json, os, sys, tempfile, hashlib, platform
from http.server import BaseHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pymupdf
from layout_utils import get_full_text, find_section
from okb_adapter import parse_all_cards
from scoring_adapter import parse_contracts_summary, parse_applications, parse_queries, VID_UCHASTIYA
from scoring_uid_map import extract_scoring_uid_map
from nbki_adapter import parse_nbki

MAX_FILE_BYTES = 4_300_000

def _probe_text(pdf_path: str, pages: int = 12) -> str:
    doc = pymupdf.open(pdf_path)
    return "\n".join(doc[i].get_text() for i in range(min(pages, len(doc))))

def detect_bureau(pdf_path: str) -> str:
    probe = _probe_text(pdf_path).upper()

    if ("КРЕДИТНЫЙ ОТЧЕТ ДЛЯ СУБЪЕКТА" in probe
        and ("ОБРАЩАЙТЕСЬ В НБКИ" in probe or "SYSTEM VERSION:" in probe)):
        return "nbki"

    if "СВОДНАЯ ИНФОРМАЦИЯ ПО ДОГОВОРАМ ЗАЙМА" in probe:
        return "scoring"
    if ("КРЕДИСТОРИЯ" in probe or "ОБЪЕДИНЕННОГО КРЕДИТНОГО БЮРО" in probe
        or 'АО «ОКБ»' in probe or 'АО "ОКБ"' in probe):
        return "okb"
    if "ПОДРОБНАЯ ИНФОРМАЦИЯ ПО ДОГОВОРАМ" in probe and "КОД ОТЧЕТА" in probe:
        return "scoring"
    raise ValueError("Формат БКИ не распознан. Поддерживаются НБКИ, ОКБ/Кредистория и Скоринг Бюро.")

def _clean_join(parts) -> str:
    if not parts:
        return ""
    if isinstance(parts, str):
        parts = [parts]
    return " ".join(str(x).strip() for x in parts if str(x).strip())

_PRODUCT_PREFIXES = (
    "НЕОБЕСПЕЧЕННЫЙ", "ОБЕСПЕЧЕННЫЙ", "МИКРОЗАЕМ", "МИКРОЗАЙМ",
    "ПОТРЕБИТЕЛЬСКИЙ КРЕДИТ", "КРЕДИТНАЯ КАРТА", "КРЕДИТНЫЙ ЛИМИТ",
    "АВТОКРЕДИТ", "ИПОТЕКА", "ЗАЕМ (КРЕДИТ)", "ЗАЙМ (КРЕДИТ)"
)

def _is_product_fragment(piece: str) -> bool:
    up = piece.upper().strip()
    return any(up.startswith(prefix) for prefix in _PRODUCT_PREFIXES)

def _scoring_creditor(parts) -> str:
    """
    creditor_type_raw begins with one or several fragments of the creditor's
    legal name and only then contains product/type fragments.
    Stop only on known PRODUCT prefixes. Words such as
    "МИКРОКРЕДИТНАЯ КОМПАНИЯ" are part of a legal name and must be retained.
    """
    if not parts:
        return ""
    if isinstance(parts, str):
        parts = [parts]

    kept = []
    for raw in parts:
        piece = str(raw).strip()
        if not piece:
            continue
        if kept and _is_product_fragment(piece):
            break
        kept.append(piece)

    return _clean_join(kept or parts[:1])

def _application_status(status_raw):
    allowed = ("Отказ", "Выдано", "Одобрено", "На рассмотрении", "Аннулирована")
    for item in status_raw or []:
        s = str(item).strip()
        if s in allowed:
            return s
    return None

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()

def _scoring_application_diagnostics(text: str, applications: list) -> dict:
    section = find_section(text, "Сведения об обращении субъекта", "Выданные кредиты / заключенные договоры поручительства", occurrence=-1)
    lines = [l.strip() for l in section.split("\n") if l.strip()]
    role_anchor_count = sum(1 for l in lines if l in VID_UCHASTIYA)
    uid_nonempty = sum(1 for a in applications if getattr(a, "uid", None))
    uid_empty = len(applications) - uid_nonempty
    return {
        "section_sha256": _sha256_text(section),
        "section_chars": len(section),
        "section_nonempty_lines": len(lines),
        "role_anchor_count": role_anchor_count,
        "applications_count": len(applications),
        "applications_uid_nonempty": uid_nonempty,
        "applications_uid_empty": uid_empty,
        "python_version": platform.python_version(),
        "pymupdf_version": getattr(pymupdf, "__version__", None),
    }

def parse_pdf(pdf_path: str) -> dict:
    bureau = detect_bureau(pdf_path)
    file_sha256 = _sha256_file(pdf_path)
    doc = pymupdf.open(pdf_path)
    page_count = len(doc)
    doc.close()

    if bureau == "nbki":
        parsed = parse_nbki(pdf_path)
        contracts = parsed["contracts"]
        applications = parsed["applications"]
        queries = parsed["queries"]
        return {
            "ok": True,
            "bureau": "НБКИ",
            "bureau_code": "nbki",
            "page_count": page_count,
            "counts": {
                "contracts": len(contracts),
                "applications": len(applications),
                "queries": len(queries),
            },
            "contracts": contracts,
            "applications": applications,
            "queries": queries,
            "meta": {
                "uid_nonempty": sum(1 for r in contracts if r.get("uid")),
                "uid_unique": len({r["uid"] for r in contracts if r.get("uid")}),
                "application_uid_nonempty": sum(1 for a in applications if a.get("uid")),
                "summary_counts": parsed.get("summary_counts", {}),
                "contract_qc": parsed.get("contract_qc", {}),
                "sections_verified": ["contracts", "queries", "applications-first-pass"],
            },
            "diagnostics": {
                "file_sha256": file_sha256,
                "python_version": platform.python_version(),
                "pymupdf_version": getattr(pymupdf, "__version__", None),
            },
            "warnings": parsed.get("warnings", []),
        }

    if bureau == "okb":
        raw = parse_all_cards(pdf_path)
        contracts = [{**c, "contract_id": c.get("uid") or ""} for c in raw]
        return {
            "ok": True, "bureau": "ОКБ / Кредистория", "bureau_code": "okb",
            "page_count": page_count,
            "counts": {"contracts": len(contracts), "applications": None, "queries": None},
            "contracts": contracts, "applications": [], "queries": [],
            "meta": {
                "uid_nonempty": sum(1 for r in contracts if r.get("uid")),
                "uid_unique": len({r["uid"] for r in contracts if r.get("uid")}),
                "sections_verified": ["contracts"],
            },
            "diagnostics": {"file_sha256": file_sha256, "python_version": platform.python_version(), "pymupdf_version": getattr(pymupdf, "__version__", None)},
            "warnings": ["В ОКБ этой версии верифицированы договоры; заявки и запросы ОКБ пока не включаются."]
        }

    text = get_full_text(pdf_path)
    cr = parse_contracts_summary(text)
    ar = parse_applications(text)
    qr = parse_queries(text)
    uid_map = extract_scoring_uid_map(pdf_path)
    app_diag = _scoring_application_diagnostics(text, ar)

    contracts = [{
        "index": c.index, "contract_id": c.contract_id, "uid": uid_map.get(c.contract_id),
        "status_section": c.status_section, "creditor": _scoring_creditor(c.creditor_type_raw),
        "creditor_type_raw": c.creditor_type_raw, "amount": c.amount,
        "start_date": c.start_date, "end_date": c.end_date,
        "current_debt": c.current_debt, "current_overdue_amount": c.current_overdue_amount,
        "current_overdue_days": c.current_overdue_days,
        "max_overdue_and_transition": c.max_overdue_and_transition,
        "actuality_date": c.actuality_date
    } for c in cr]

    applications = [{
        "application_date": a.application_date, "uid": a.uid,
        "creditor": _clean_join(a.creditor_raw), "creditor_raw": a.creditor_raw,
        "participation": a.participation, "amount": a.amount, "method": a.method,
        "status": _application_status(a.status_raw), "warning": a.warning
    } for a in ar]

    queries = [{
        "query_date": q.query_date, "user_kind": q.user_kind,
        "requester": q.requester_raw[0] if q.requester_raw else "",
        "requester_raw": q.requester_raw,
        "amount": q.amount,
        "purpose": " · ".join(q.requester_raw[1:]) if len(q.requester_raw) > 1 else "",
        "requested_info": q.requested_info, "provided_date": q.provided_date, "warning": q.warning
    } for q in qr]

    return {
        "ok": True, "bureau": "Скоринг Бюро", "bureau_code": "scoring",
        "page_count": page_count,
        "counts": {"contracts": len(contracts), "applications": len(applications), "queries": len(queries)},
        "contracts": contracts, "applications": applications, "queries": queries,
        "meta": {
            "contract_id_unique": len({c["contract_id"] for c in contracts}),
            "contract_uid_map_count": len(uid_map),
            "application_uid_nonempty": sum(1 for a in applications if a.get("uid")),
            "application_uid_empty": sum(1 for a in applications if not a.get("uid")),
            "application_uid_unique": len({a["uid"] for a in applications if a.get("uid")}),
            "sections_verified": ["contracts", "applications", "queries"],
        },
        "diagnostics": {"file_sha256": file_sha256, **app_diag},
        "warnings": []
    }

class handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self._json(200, {"ok": True})

    def do_POST(self):
        tmp_path = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("PDF-файл пуст.")
            if length > MAX_FILE_BYTES:
                raise ValueError("PDF больше 4,3 МБ. Для такого файла понадобится отдельная схема загрузки.")
            data = self.rfile.read(length)
            if not data.startswith(b"%PDF"):
                raise ValueError("Ожидается PDF-файл.")
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            result = parse_pdf(tmp_path)
            result["filename"] = self.headers.get("X-Filename", "upload.pdf")
            self._json(200, result)
        except ValueError as e:
            self._json(400, {"ok": False, "error": str(e)})
        except Exception as e:
            self._json(500, {"ok": False, "error": "Не удалось обработать PDF.", "detail": str(e)})
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
