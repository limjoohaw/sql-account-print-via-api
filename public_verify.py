"""Public, unauthenticated QR invoice-verification endpoint.

A Customer scans the QR on a printed invoice → GET /v?c=&k=&n=&s=  →  this route
verifies the HMAC signature (so the DocNo can't be tampered with — IDOR protection),
fetches the LIVE document from SQL Account, and returns the PDF inline. If the document
was cancelled in SQL Account, a message is shown instead of the PDF.

This is a plain FastAPI route on NiceGUI's underlying `app` (not a ui.page): it is
anonymous, stateless, and returns either a PDF or a small HTML page. It deliberately does
NOT call the login guard.

Registered at import time via the @app.get decorator — main.py imports this module.
"""

import time
import html
import collections

from fastapi import Request
from fastapi.responses import Response, HTMLResponse
from nicegui import app

from companies import find_company
from doc_types import load_doc_types
from sql_api import SQLAccAPIClient, get_field_value
from verify import verify
from logger import print_logger
from config import settings
from shared import CLR_PRIMARY, CLR_DANGER

# ---------------------------------------------------------------------------
# Rate limiter — max attempts per IP per window (mirrors the login limiter)
# ---------------------------------------------------------------------------
_verify_attempts: dict[str, list[float]] = collections.defaultdict(list)
_MAX_VERIFY_ATTEMPTS = 30
_VERIFY_WINDOW_SECONDS = 60


def _client_ip(request: Request) -> str:
    """Best-effort client IP, honouring the reverse proxy in front of the app."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    """Record this hit and return True if the IP has exceeded the window budget."""
    now = time.time()
    hits = [t for t in _verify_attempts[ip] if now - t < _VERIFY_WINDOW_SECONDS]
    hits.append(now)
    _verify_attempts[ip] = hits
    return len(hits) > _MAX_VERIFY_ATTEMPTS


# ---------------------------------------------------------------------------
# Cancellation detection
# ---------------------------------------------------------------------------
# CONFIRMED (per SQL Account): the document JSON has a boolean field "cancelled"
# (lowercase) — true = cancelled, false = active. The case variants and string/numeric
# branches below are harmless extra resilience in case a response ever differs.
_CANCELLED_FIELDS = ("cancelled", "Cancelled", "CANCELLED")
_TRUTHY_CANCELLED = {"t", "true", "y", "yes", "1", "-1"}


def _is_cancelled(data) -> bool:
    """Return True if the document JSON indicates the document was cancelled.

    Primary path: the boolean `cancelled` field (true → cancelled).
    """
    for fld in _CANCELLED_FIELDS:
        val = get_field_value(data, fld)
        if val is None:
            continue
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return int(val) != 0
        return str(val).strip().lower() in _TRUTHY_CANCELLED
    return False


# ---------------------------------------------------------------------------
# HTML response helpers (mobile-friendly, lightly branded)
# ---------------------------------------------------------------------------
def _page(title: str, message: str, color: str, status_code: int = 200) -> HTMLResponse:
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>
  body {{ margin:0; font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
         background:#f5f6fa; color:#2c2c3a; display:flex; min-height:100vh;
         align-items:center; justify-content:center; padding:24px; }}
  .card {{ background:#fff; border-radius:16px; box-shadow:0 8px 30px rgba(0,0,0,.08);
          max-width:420px; width:100%; padding:32px 28px; text-align:center; }}
  .bar {{ height:6px; border-radius:6px; background:{color}; margin:-32px -28px 24px;
          border-top-left-radius:16px; border-top-right-radius:16px; }}
  h1 {{ font-size:20px; margin:0 0 12px; color:{color}; }}
  p {{ font-size:15px; line-height:1.5; margin:0; color:#555; }}
  .foot {{ margin-top:24px; font-size:12px; color:#aaa; }}
</style>
</head>
<body>
  <div class="card">
    <div class="bar"></div>
    <h1>{safe_title}</h1>
    <p>{safe_message}</p>
    <div class="foot">GOLINK Document Verification</div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=body, status_code=status_code)


# Generic message used for every "this link is not valid" case. Deliberately identical
# for bad signature / missing company / unknown doc type / not found, so an attacker can't
# distinguish "wrong signature" from "no such document" (no information leak).
def _invalid_link() -> HTMLResponse:
    return _page(
        "Invalid or expired link",
        "This verification link is not valid. Please scan the QR code directly from the "
        "original document.",
        CLR_DANGER,
        status_code=404,
    )


def _decode_template(t: str) -> str | None:
    """Hex-decode the `t` URL param into the raw report-format name. None if malformed."""
    try:
        return bytes.fromhex(t).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


@app.get("/v")
def verify_document(request: Request, c: str = "", k: str = "", n: str = "",
                    t: str = "", s: str = ""):
    """Public QR verification endpoint. Returns the live PDF or an HTML message.

    Query params: c=company id, k=doctype key, n=docno, t=hex(format name), s=signature.
    """
    ip = _client_ip(request)
    start = time.time()

    def _log(status: str, error: str = ""):
        latency = int((time.time() - start) * 1000)
        print_logger.log_print("qr-verify", c or "-", k or "-", n or "-", "verify",
                               status, latency_ms=latency, error=error)

    if _rate_limited(ip):
        _log("RATE_LIMITED", ip)
        return _page("Too many requests",
                     "Please wait a moment and try scanning again.",
                     CLR_DANGER, status_code=429)

    # --- Validate inputs & signature -------------------------------------------------
    if not (c and k and n and t and s):
        _log("BAD_REQUEST")
        return _invalid_link()

    template_name = _decode_template(t)
    if not template_name:
        _log("BAD_TEMPLATE")
        return _invalid_link()

    company = find_company(c)
    doc_types = load_doc_types()
    dt = doc_types.get(k)

    # Verify signature against the company's secret (covers the format name too).
    # Any failure → generic invalid link.
    if (company is None or dt is None
            or not verify(company.verify_secret, c, k, n, template_name, s)):
        _log("BAD_SIG")
        return _invalid_link()

    # --- Fetch the live document from SQL Account ------------------------------------
    try:
        client = SQLAccAPIClient(
            host=company.api_host,
            region=settings.sqlacc_aws_region,
            access_key=company.access_key,
            secret_key=company.secret_key,
        )
        data = client.fetch_document_json(dt.resource, n)

        dockey = get_field_value(data, "dockey")
        if dockey is None:
            _log("NOT_FOUND")
            return _page("Document not found",
                         f"No {html.escape(dt.label)} matching this reference was found "
                         "in the system.", CLR_DANGER, status_code=404)

        if _is_cancelled(data):
            _log("CANCELLED")
            return _page("Document cancelled",
                         "This document has been cancelled in system.",
                         CLR_DANGER)

        # Render using the signed format name carried in the QR (`t`). It's part of the
        # HMAC payload, so it can't be tampered with; SQL Account validates it exists.
        response = client.fetch_document_pdf(dt.resource, dockey, template_name)
        pdf_bytes = response.content

        if not pdf_bytes or pdf_bytes[:5] != b"%PDF-":
            _log("INVALID_PDF", "Response not %PDF-")
            return _page("Unable to display document",
                         "The document could not be generated right now. Please try "
                         "again later.", CLR_DANGER, status_code=502)

        _log("OK")
        filename = dt.filename.format(docno=n)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    except Exception as ex:  # noqa: BLE001 — surface a generic page, log the detail
        _log("ERROR", str(ex)[:200])
        return _page("Unable to display document",
                     "The document could not be retrieved right now. Please try again "
                     "later.", CLR_DANGER, status_code=502)
