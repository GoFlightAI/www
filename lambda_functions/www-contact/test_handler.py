"""Offline tests for the unified www-contact handler.

    python3 test_handler.py
"""
import base64
import email
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch


def _parsed_apply_email():
    """Parse the MIME bytes the handler passed to send_email."""
    raw = index.ses.send_email.call_args.kwargs["Content"]["Raw"]["Data"]
    return email.message_from_bytes(raw)


def _apply_text_body():
    msg = _parsed_apply_email()
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            return part.get_payload(decode=True).decode("utf-8")
    return ""

os.environ.setdefault("FROM_EMAIL", "hello@goflight.ai")
os.environ.setdefault("CONTACT_TO", "hello@goflight.ai")
os.environ.setdefault("APPLY_TO", "jack@goflight.ai")
sys.path.insert(0, os.path.dirname(__file__))

with patch("boto3.client") as _client:
    _client.return_value = MagicMock()
    import index  # noqa: E402


def _event(body, method="POST", origin="https://goflight.ai"):
    return {
        "body": json.dumps(body),
        "headers": {"origin": origin, "content-type": "application/json"},
        "requestContext": {"http": {"method": method}},
        "isBase64Encoded": False,
    }


CONTACT_BODY = {"name": "Jane Smith", "email": "jane@example.com", "role": "operator", "context": "Fleet of 12 jets."}
APPLY_BODY = {
    "name": "Pat Engineer",
    "email": "pat@example.com",
    "portfolio": "https://github.com/patengineer",
    "interesting": "Built a multi-agent triage pipeline for our support inbox, in production for ~6 months.",
}


class UnifiedHandlerTests(unittest.TestCase):
    def setUp(self):
        index.ses = MagicMock()
        index.ses.send_email.return_value = {"MessageId": "ok"}

    def test_contact_happy(self):
        res = index.handler(_event(CONTACT_BODY), None)
        self.assertEqual(res["statusCode"], 200)
        kwargs = index.ses.send_email.call_args.kwargs
        self.assertEqual(kwargs["Destination"]["ToAddresses"], ["hello@goflight.ai"])
        self.assertIn("Simple", kwargs["Content"])
        self.assertIn("Operator inquiry from Jane Smith", kwargs["Content"]["Simple"]["Subject"]["Data"])

    def test_contact_missing_email(self):
        res = index.handler(_event(dict(CONTACT_BODY, email="nope")), None)
        self.assertEqual(res["statusCode"], 400)

    def test_contact_no_role(self):
        res = index.handler(_event({"name": "Anon", "email": "a@b.co"}), None)
        self.assertEqual(res["statusCode"], 200)
        subj = index.ses.send_email.call_args.kwargs["Content"]["Simple"]["Subject"]["Data"]
        self.assertEqual(subj, "Hello from Anon")

    def test_apply_happy_no_resume(self):
        res = index.handler(_event(APPLY_BODY), None)
        self.assertEqual(res["statusCode"], 200)
        kwargs = index.ses.send_email.call_args.kwargs
        self.assertEqual(kwargs["Destination"]["ToAddresses"], ["jack@goflight.ai"])
        self.assertIn("Raw", kwargs["Content"])
        msg = _parsed_apply_email()
        self.assertEqual(msg["Subject"], "GoFlight Application: Pat Engineer")
        self.assertIn("pat@example.com", msg["Reply-To"])
        body = _apply_text_body()
        self.assertIn("pat@example.com", body)
        self.assertIn("github.com/patengineer", body)

    def test_apply_with_resume(self):
        pdf_bytes = b"%PDF-1.4 fake body"
        body = dict(APPLY_BODY, resume={
            "filename": "pat_resume.pdf",
            "contentType": "application/pdf",
            "base64": base64.b64encode(pdf_bytes).decode(),
        })
        res = index.handler(_event(body), None)
        self.assertEqual(res["statusCode"], 200)
        msg = _parsed_apply_email()
        attachments = [p for p in msg.walk() if p.get_filename()]
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "pat_resume.pdf")
        self.assertEqual(attachments[0].get_payload(decode=True), pdf_bytes)

    def test_apply_bad_url(self):
        res = index.handler(_event(dict(APPLY_BODY, portfolio="github.com/foo")), None)
        self.assertEqual(res["statusCode"], 400)

    def test_apply_short_text(self):
        res = index.handler(_event(dict(APPLY_BODY, interesting="too short")), None)
        self.assertEqual(res["statusCode"], 400)

    def test_apply_bad_resume_ext(self):
        body = dict(APPLY_BODY, resume={
            "filename": "evil.exe", "contentType": "application/octet-stream",
            "base64": base64.b64encode(b"x").decode(),
        })
        res = index.handler(_event(body), None)
        self.assertEqual(res["statusCode"], 400)

    def test_apply_oversize_resume(self):
        big = b"x" * (6 * 1024 * 1024)
        body = dict(APPLY_BODY, resume={
            "filename": "r.pdf", "contentType": "application/pdf",
            "base64": base64.b64encode(big).decode(),
        })
        res = index.handler(_event(body), None)
        self.assertEqual(res["statusCode"], 400)

    def test_options(self):
        res = index.handler(_event({}, method="OPTIONS"), None)
        self.assertEqual(res["statusCode"], 204)

    def test_method_not_allowed(self):
        res = index.handler(_event(CONTACT_BODY, method="GET"), None)
        self.assertEqual(res["statusCode"], 405)

    def test_join_origin_allowed(self):
        res = index.handler(_event(APPLY_BODY, origin="https://join.goflight.ai"), None)
        self.assertEqual(res["headers"]["Access-Control-Allow-Origin"], "https://join.goflight.ai")

    def test_evil_origin_falls_back(self):
        res = index.handler(_event(CONTACT_BODY, origin="https://evil.com"), None)
        self.assertEqual(res["headers"]["Access-Control-Allow-Origin"], "https://goflight.ai")

    def test_routes_by_shape(self):
        """Apply payload posted via /contact still gets treated as apply (shape-based)."""
        res = index.handler(_event(APPLY_BODY), None)
        self.assertEqual(res["statusCode"], 200)
        self.assertEqual(
            index.ses.send_email.call_args.kwargs["Destination"]["ToAddresses"],
            ["jack@goflight.ai"],
        )

    def test_invalid_json(self):
        ev = {"body": "{not json", "headers": {}, "requestContext": {"http": {"method": "POST"}}}
        res = index.handler(ev, None)
        self.assertEqual(res["statusCode"], 400)


if __name__ == "__main__":
    unittest.main()
