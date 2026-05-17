# www — goflight.ai static site + form intake

The marketing site at **www.goflight.ai** and the hiring page at **join.goflight.ai**, plus the single Lambda that backs both forms.

## Layout

```
www/
├─ index.html                       Landing page  (www.goflight.ai)
├─ join.html                        Founding-engineer hiring page
├─ assets/                          Shared images
├─ lambda_functions/
│  └─ www-contact/                  Single handler: /contact + /apply
│     ├─ index.py                   handler.handler = index.handler
│     ├─ deploy.sh                  Idempotent code/config update + route ensure
│     └─ test_handler.py            Stubs SES; runs offline
└─ .github/workflows/deploy.yml     S3 sync + CloudFront invalidate on push to main
```

## Infrastructure (account 138893339755, us-east-1)

| Resource | ID / Name |
|---|---|
| S3 bucket | `goflight-frontend-prod-138893339755` |
| CloudFront | `E1XKI7Q9ZCX56P` |
| API Gateway HTTP API | `8pm6qmjog0` |
| Lambda | `www-contact` (handles **both** routes) |
| Route — landing form | `POST /contact` → `hello@goflight.ai` |
| Route — hiring form | `POST /apply` → `jack@goflight.ai` (+ optional resume attachment) |
| SES sender / domain | `hello@goflight.ai` / `goflight.ai` (DKIM in Cloudflare) |

Profile: `138893339755-goflight-production`. DNS is Cloudflare (token at `~/.cloudflare-api-token`).

## Deploying

### Static site

`git push origin main`. Actions runs `aws s3 sync . s3://goflight-frontend-prod-138893339755` (excluding `.git`, `.github`, `*.py`, `lambda_functions/*`, `README.md`) and invalidates `/*` on CloudFront. Both `index.html` and `join.html` ship together.

### Lambda

```bash
cd lambda_functions/www-contact
./deploy.sh
```

Idempotent. Updates code + config, ensures `/contact` and `/apply` routes both exist and point at the function, ensures the IAM role has `ses:SendRawEmail` (needed for resume attachments), and adds `https://join.goflight.ai` to the API's CORS allow-list (preserves all other CORS fields).

> **Deploy the Lambda before merging `join.html`** so the new `/apply` route exists by the time anyone hits the page.

## Routing model

One Lambda, two routes. It branches on **payload shape**, not URL — so an apply-payload posted to `/contact` still goes to `jack@`, and a contact-payload posted to `/apply` still goes to `hello@`. Routes exist mainly so CORS preflight works for both and so logs are easy to filter.

## Form contracts

### `POST /contact` (landing-page contact form)

```json
{
  "name": "Jane Smith",
  "email": "jane@example.com",
  "role": "operator | broker | passenger | fbo | press | other",
  "context": "Optional free text, up to 1000 chars"
}
```

Sends to `CONTACT_TO` (`hello@goflight.ai`). Reply-To = sender.

### `POST /apply` (hiring form)

```json
{
  "name": "Pat Engineer",
  "email": "pat@example.com",
  "portfolio": "https://github.com/patengineer",
  "interesting": "What I shipped, >= 20 chars",
  "resume": {
    "filename": "pat.pdf",
    "contentType": "application/pdf",
    "base64": "<base64 of file, decoded size <= 5 MB>"
  }
}
```

`resume` is optional. Allowed: `pdf`, `doc`, `docx`. Sends to `APPLY_TO` (`jack@goflight.ai`). Reply-To = applicant.

## Local development

```bash
python3 -m http.server 8000           # index.html + join.html at localhost:8000
cd lambda_functions/www-contact
python3 test_handler.py               # 15 offline tests, no AWS calls
```

## One-time setup for `join.goflight.ai`

The page is reachable today at `https://www.goflight.ai/join.html`. To serve it at `join.goflight.ai`:

1. **ACM cert** for `join.goflight.ai` in `us-east-1` (DNS-validated via Cloudflare).
2. **CloudFront alternate domain** — add `join.goflight.ai` to distribution `E1XKI7Q9ZCX56P`, attach the cert.
3. **CloudFront Function** (viewer-request) — rewrite `Host: join.goflight.ai` + `URI: /` → `/join.html`.
4. **Cloudflare DNS** — `CNAME join → <distribution-domain>.cloudfront.net`, **DNS-only** (orange cloud breaks CloudFront SNI).
