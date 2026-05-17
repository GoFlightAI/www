"""GoFlight www form intake.

Single Lambda handling two routes on the same HTTP API:

  POST /contact   landing-page contact form  → hello@goflight.ai
  POST /apply     founding-engineer form     → jack@goflight.ai (+ optional resume)

Routing is by payload shape, not URL, so either route accepts either payload
(the API Gateway routes are just for clarity and to let CORS preflight succeed).
"""
import base64
import json
import logging
import os
import re
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

SENDER = os.environ.get("FROM_EMAIL", "hello@goflight.ai")
SENDER_NAME = os.environ.get("FROM_NAME", "GoFlight")
CONTACT_TO = os.environ.get("CONTACT_TO", "hello@goflight.ai")
APPLY_TO = os.environ.get("APPLY_TO", "jack@goflight.ai")

ALLOWED_ORIGINS = {
    "https://goflight.ai",
    "https://www.goflight.ai",
    "https://join.goflight.ai",
}
ALLOWED_RESUME_EXT = {"pdf", "doc", "docx"}
MAX_RESUME_BYTES = 5 * 1024 * 1024
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

ROLE_LABELS = {
    "operator": "An operator (runs aircraft)",
    "broker": "A broker",
    "passenger": "A passenger / charter customer",
    "fbo": "FBO / ground / vendor",
    "press": "Press / advisor / investor",
    "other": "Something else",
}

ses = boto3.client("sesv2")


def _cors(origin: str) -> dict:
    allowed = origin if origin in ALLOWED_ORIGINS else "https://goflight.ai"
    return {
        "Access-Control-Allow-Origin": allowed,
        "Access-Control-Allow-Headers": "content-type",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Max-Age": "86400",
        "Vary": "Origin",
    }


def _resp(code: int, body: dict | None = None, origin: str = "") -> dict:
    return {
        "statusCode": code,
        "headers": {**_cors(origin), "Content-Type": "application/json"},
        "body": json.dumps(body or {}),
    }


def _safe(s, limit: int) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s).strip()[:limit]


def _decode_resume(raw):
    if not raw:
        return None
    filename = _safe(raw.get("filename", ""), 200)
    content_type = _safe(raw.get("contentType", ""), 200)
    b64 = raw.get("base64", "")
    if not filename or not b64:
        raise ValueError("resume.filename and resume.base64 are required")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_RESUME_EXT:
        raise ValueError("Resume must be .pdf, .doc, or .docx")
    try:
        data = base64.b64decode(b64, validate=True)
    except Exception as exc:
        raise ValueError("Resume could not be decoded") from exc
    if len(data) == 0:
        raise ValueError("Resume is empty")
    if len(data) > MAX_RESUME_BYTES:
        raise ValueError("Resume is too large (max 5 MB)")
    return {"filename": filename, "contentType": content_type, "bytes": data}


def _send_contact(payload: dict) -> tuple[int, dict]:
    name = _safe(payload.get("name"), 200)
    email = _safe(payload.get("email"), 320)
    role = payload.get("role") or ""
    context = _safe(payload.get("context"), 1000)

    if not name or not EMAIL_RE.match(email):
        return 400, {"error": "name and valid email required"}

    role_label = ROLE_LABELS.get(role, "(not specified)")
    subject = ("Operator inquiry" if role == "operator" else "Hello") + f" from {name}"
    body_text = (
        "Inbound from www.goflight.ai\n\n"
        f"Name:    {name}\n"
        f"Email:   {email}\n"
        f"Role:    {role_label}\n"
        f"Context: {context or '(none)'}\n\n"
        "Reply directly to this email to reach the sender.\n"
    )

    try:
        ses.send_email(
            FromEmailAddress=formataddr((SENDER_NAME, SENDER)),
            Destination={"ToAddresses": [CONTACT_TO]},
            ReplyToAddresses=[email],
            Content={"Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
            }},
        )
    except ClientError as exc:
        log.exception("contact send failed")
        return 500, {"error": "send failed", "detail": str(exc)}
    return 200, {"ok": True}


def _send_apply(payload: dict) -> tuple[int, dict]:
    name = _safe(payload.get("name"), 200)
    email = _safe(payload.get("email"), 320)
    portfolio = _safe(payload.get("portfolio"), 500)
    interesting = _safe(payload.get("interesting"), 8000)

    if not name:
        return 400, {"error": "name is required"}
    if not EMAIL_RE.match(email):
        return 400, {"error": "valid email is required"}
    if not portfolio.lower().startswith(("http://", "https://")):
        return 400, {"error": "portfolio must be an http(s) URL"}
    if len(interesting) < 20:
        return 400, {"error": "tell us more about what you shipped"}

    try:
        resume = _decode_resume(payload.get("resume"))
    except ValueError as exc:
        return 400, {"error": str(exc)}

    body_text = (
        "Inbound application from join.goflight.ai\n\n"
        f"Name:      {name}\n"
        f"Email:     {email}\n"
        f"Portfolio: {portfolio}\n\n"
        "Most interesting thing shipped with AI:\n"
        f"{interesting}\n\n"
        "Reply directly to this email to reach the applicant.\n"
    )

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"GoFlight Application: {name}"
    msg["From"] = formataddr((SENDER_NAME, SENDER))
    msg["To"] = APPLY_TO
    msg["Reply-To"] = formataddr((name, email))
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if resume:
        part = MIMEApplication(resume["bytes"])
        part.add_header("Content-Disposition", "attachment", filename=resume["filename"])
        if resume.get("contentType"):
            part.replace_header("Content-Type", resume["contentType"])
        msg.attach(part)

    try:
        ses.send_email(
            FromEmailAddress=SENDER,
            Destination={"ToAddresses": [APPLY_TO]},
            Content={"Raw": {"Data": msg.as_bytes()}},
        )
    except ClientError as exc:
        log.exception("apply send failed")
        return 500, {"error": "send failed", "detail": str(exc)}
    return 200, {"ok": True}


def _is_application(payload: dict) -> bool:
    return "portfolio" in payload or "interesting" in payload or "resume" in payload


def handler(event, _ctx):
    headers = event.get("headers") or {}
    origin = headers.get("origin") or headers.get("Origin") or ""
    method = (
        (event.get("requestContext") or {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "POST"
    )
    if method == "OPTIONS":
        return _resp(204, origin=origin)
    if method != "POST":
        return _resp(405, {"error": "method not allowed"}, origin)

    try:
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return _resp(400, {"error": "invalid json"}, origin)

    code, body = _send_apply(payload) if _is_application(payload) else _send_contact(payload)
    return _resp(code, body, origin)
