# Project Overview

ASR Enterprises is a full-stack web application with a Create React App frontend in `frontend/` and a FastAPI backend in `backend/`.

# Replit Setup

- The development workflow is `Start application`.
- The React dev server runs on `0.0.0.0:5000` for the Replit preview.
- The FastAPI backend runs on `127.0.0.1:8000` and is reached through the React dev server `/api` proxy.
- `frontend/craco.config.js` allows all dev hosts and proxies `/api` to the backend.
- The imported backend originally expected MongoDB and a private `emergentintegrations` package. Replit setup adds:
  - `backend/db_client.py` to use in-memory Mongo compatibility when `USE_IN_MEMORY_MONGO=true`.
  - `backend/emergentintegrations/` as a compatibility shim so AI-dependent routes fail gracefully or return safe defaults without private packages.

# Dependencies

- Frontend dependencies are installed with npm and tracked in `frontend/package-lock.json`.
- Backend dependencies are tracked in `pyproject.toml`/`uv.lock` and include FastAPI, Uvicorn, Motor, and in-memory Mongo compatibility.

# Deployment

- Deployment is configured as autoscale.
- Build command: `cd frontend && npm install --no-audit --no-fund && npm run build`.
- Run command starts FastAPI on `0.0.0.0:${PORT:-5000}` and serves the compiled React app from `frontend/build`.
- Frontend production dependencies were aligned for normal npm install: React 18, React DOM 18, date-fns 3, ESLint 8, AJV 8.
- The public website theme uses a premium light solar palette with sunlit gold, solar-glass blue, emerald CTA accents, and visible solar-panel grid/array effects on the homepage hero and zero-bill section.
## April 18, 2026 — Leads Bin, 250/page, Soft-Delete & WhatsApp Cleanup

### Frontend changes
- **LeadsManagement.js**: Added "Leads Bin" tab — soft-deleted leads appear here with countdown timer (days remaining before permanent deletion). Restore button per lead. Bin count badge on tab.
- **LeadsManagement.js**: Leads fetch limit increased 100 → 250.
- **LeadsManagement.js**: Single delete and bulk delete now both soft-delete (move to Leads Bin) instead of permanent delete. Confirmation message updated accordingly.
- **StaffPortal.js**: Staff leads fetch limit increased 150 → 250.
- **WhatsAppInbox.js**: Template name display changed from `truncate` to `break-all` so full template name is visible in chat bubbles.

### Backend changes
- **server.py**: `GET /dashboard/widget/counts` and CRM stats endpoints now exclude `is_deleted=true` leads from all counts — dashboard numbers match only live (non-deleted) leads.
- **server.py**: Staff leads endpoint (`GET /staff/{staff_id}/leads`) now filters out `is_deleted=true` leads so deleted leads don't appear in staff portal.
- **server.py**: Daily cleanup scheduler now purges WhatsApp messages older than 24 hours from `whatsapp_messages` collection (keeps chat history lean, compliant with Meta 24h window policy).

---

## April 17, 2026 — HR / Cashfree CRM / WhatsApp Template Fixes

**Fixed 7 reported issues via 3 architectural changes:**

1. **Shared DB client singleton** (`db_client.py` + 5 route files + `server.py`) — Previously each route module called `AsyncIOMotorClient(MONGO_URL)` independently. Under `USE_IN_MEMORY_MONGO=true` (mongomock-motor), every call spins up its OWN isolated in-memory store, so Cashfree's `db.payments.update_one(...)` wrote into one store while the CRM "Cashfree Payments" page read from a different one. All routes now use `db_client.get_db()` which caches a single shared client. Fixes: Cashfree Payments not showing in CRM, any cross-module data invisibility.

2. **Anamika (ASR1002) seed — create-only** (`server.py:610-687`) — The seed block used to `update_one(...)` on every startup, force-overwriting any edits the Super Admin made via HR Management. Now it only inserts if the record is missing. Also fixed the default email typo (`analnikarathod1905` → `anamikarathod1905`) and corrected default phone (`9999900002` → `7903434221`). `ANAMIKA_PASSWORD` env still refreshes password hash for owner recovery, nothing else.

3. **Snapshot resurrection guard** (`db_client.py`) — Test staff `ASR1003` (Persistence Test) and `ASR1004` (Sales Test) were baked into `data/mongo_snapshot.json` and reappeared on every restart. Cleaned them out of the snapshot AND added `_BLOCKED_STAFF_IDS` filter inside `load_snapshot` so these IDs can never be restored even if they reappear in a future snapshot.

**WhatsApp template sync hardening** (`routes/whatsapp.py:420-560`)
- Only APPROVED templates from Meta are stored as active; any template that disappears from Meta is marked `is_active=False` and `meta_approved=False` (kept for history, but unusable).
- On Meta API failure the endpoint now returns `502` instead of silently overwriting the admin's curated table with hard-coded `DEFAULT_TEMPLATES`.
- Preserves the admin's `is_active` toggle on re-sync.

---

## 2026-04-17 — Stability + Automation Sweep (deployed)

### Backend additions
- **WhatsApp auto-retry** (`routes/cashfree_orders.py: retry_failed_whatsapp_messages`, `whatsapp_retry_loop`):
  - Real send retry (re-fires `send_whatsapp_template`) with exponential backoff (1, 2, 4 min) and max 3 attempts.
  - 60-second background task scheduled in `server.py` startup.
  - Rows missing `template_name` but with `order_id` are deferred to the cashfree reconcile loop instead of being permanently skipped.
- **Owner alert on payment** (`send_admin_payment_alert`, hooked into `_mark_order_paid`):
  - WhatsApp to ASR1001 owner phone (or `ADMIN_ALERT_PHONE` env override) on every successful Cashfree payment.
  - Atomic claim via insert + sparse unique index `whatsapp_messages.(order_id, type)` for `admin_payment_alert` — survives concurrent webhooks.
  - Failures are non-blocking and re-attempted by the WhatsApp retry loop.
- **CSV export endpoints**: `GET /api/payments/transactions/export.csv`, `GET /api/crm/leads/export.csv` (filters: status/source/from_date/to_date/search).
- **Revenue chart**: `GET /api/payments/revenue/chart?period=daily|weekly|monthly&days=N` returns aggregated paid-revenue time series.
- **HTML invoice**: `GET /api/payments/invoice/{order_id}` (printable, browser "Save as PDF"). All user-controlled fields are HTML-escaped to prevent XSS.

### Frontend additions
- **PaymentsDashboard**: date-range filter (from/to), Export CSV button, Revenue Trend bar chart (toggle Daily/Weekly/Monthly).
- **LeadsManagement**: Export CSV button next to existing Import CSV.

### Code-review hardening (architect FAIL → PASS)
- XSS in invoice fixed (every field run through `html.escape`).
- Admin-alert idempotency fixed via atomic claim + sparse unique index.
- Retry loop no longer drops text-fallback rows that have order context.

---

## 2026-04-17 — Production Security Hardening

### Headers (verified live in `/api/*` responses)
| Header | Value |
|---|---|
| Strict-Transport-Security | max-age=31536000; includeSubDomains; preload |
| Content-Security-Policy | locked to Cashfree, MSG91, Google (no Razorpay leftovers); `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'`, `upgrade-insecure-requests` |
| X-Frame-Options | DENY |
| X-Content-Type-Options | nosniff |
| Referrer-Policy | strict-origin-when-cross-origin |
| Permissions-Policy | geolocation=(), microphone=(), camera=() |
| Cross-Origin-Opener/Resource-Policy | same-origin / same-site |
| Server / X-Powered-By | **stripped** (uvicorn `--no-server-header` + middleware del) |

### Redirect / Transport
- `FORCE_HTTPS` env (defaults true in production) → real `RedirectResponse(301)` for any `X-Forwarded-Proto: http`. Skips `/api/health/live` for healthcheck-friendliness.
- `CANONICAL_HOST` env (e.g. `www.asrenterprises.in`) → 308 redirect for any non-API GET coming in on a different host. API + webhook paths are never redirected so payment provider POST bodies aren't lost.

### Open-redirect protection (Cashfree create-order)
- `origin_url` from the client is now host-checked against `ALLOWED_REDIRECT_HOSTS` (env, defaults to `asrenterprises.in,www.asrenterprises.in`; `*.replit.dev` for preview). Anything else falls back silently to `ASR_WEBSITE` and is logged. Verified: `https://evil.example.com` is rejected, `https://www.asrenterprises.in` is accepted.

### Existing controls (re-verified)
- Cashfree webhook HMAC-SHA256 signature verification + replay protection + idempotent processing.
- Rate limiting on auth (5/min), payment (10/min), OTP (5/min), admin (30/min) with IP block after 10 failed attempts.
- Input sanitization + suspicious-pattern auto-blocking (XSS, SQLi, NoSQL operators, path traversal).
- 10 MB request body cap; 50 MB upload cap; CORS env-driven and fail-fast against `*` in production.
- Mongo unique indexes for orders, staff, admin alerts; sparse indexes on emails / payment_id.
- Secrets fail-fast at startup if missing in production; never logged (mask helpers in `config.py`).
- HTML-escaping in invoice rendering (verified blocks `<script>` payload).

### Env vars added
- `FORCE_HTTPS` (true/false) — defaults true in prod, false in dev.
- `CANONICAL_HOST` — set to `www.asrenterprises.in` in production deploy.
- `ALLOWED_REDIRECT_HOSTS` — comma-separated allowlist for Cashfree `origin_url`.

### Post-architect-review hardening (2026-04-17 round 2)
- **CSP completed** — added the third-party scripts the React app actually loads (`assets.emergent.sh`, `connect.facebook.net`, `us.i.posthog.com` + `*.i.posthog.com`, `www.recaptcha.net`) to `script-src`/`connect-src`/`frame-src`, so production CSP no longer breaks PostHog analytics, Facebook Pixel, Emergent debug tooling, or Google reCAPTCHA xhr.
- **Open-redirect tightened** — `origin_url` validator now: HTTPS-only in production (no http downgrade), strict allowlist only (`*.replit.dev` fallback removed in production, kept in dev for preview), IDN/punycode-normalized host comparison (Cyrillic look-alikes blocked: `www.аsrenterprises.in` → rejected as `www.xn--srenterprises-v1k.in`), and rebuilds URL from sanitized `scheme + hostname` only (drops userinfo and arbitrary ports).
- **Canonical-host redirect skip-list** — extended from `/api/` to `/api/`, `/webhook`, `/cashfree` so Meta webhook verification GETs and Cashfree return URLs remain on whatever host they were called with.

## Sweep 2026-04-18 — UX polish + DNS docs + test-data cleanup

### New: cleanup endpoint for fake/test transactions
- `POST /api/cashfree/orders/cleanup-test-data` (in `backend/routes/cashfree_orders.py`).
  Soft-deletes orders matching ANY of: amount ≤ ₹5, customer name containing
  XSS markers (`<`, `>`, `script`, `javascript:`, `onerror`, `onload`), or
  pending/created status older than 48h. **Verified-paid orders (SUCCESS/PAID)
  are explicitly excluded.** Defaults to dry-run; pass `?confirm=true` to apply.
- Wired into Payments dashboard as "Cleanup Test Data" button (amber). Always
  shows a preview confirm dialog before deletion.

### Auto-logout: 10/30/15 → 60/60/30 min
- `frontend/src/hooks/useAutoLogout.js`. Old 10-min admin window was the most
  likely cause of "logged out on back button" complaints (browser back-nav does
  not fire mousemove/scroll, so the timer was already past expiry on restore).

### Lead-count "going to 0" fix
- `frontend/src/components/LeadsManagement.js`: on fetch failure we now KEEP
  the previously loaded list and show an amber error banner with a Refresh
  button, instead of silently `setLeads([])`. The "0 Total" the user was
  seeing was almost always a transient API failure, not a real data wipe.

### DNS / SSL setup (USER ACTION REQUIRED — agent cannot do this)
SSL is auto-issued and auto-renewed by Replit on the deployed domain, but only
after you point DNS correctly:

1. In your domain registrar (where you bought `asrenterprises.in`), add:
   ```
   Type   Host   Value             TTL
   A      @      34.111.179.208    3600
   A      www    34.111.179.208    3600
   ```
   (If a TXT verification record is shown in the Replit Deployments → Domains
   panel, copy it verbatim into a TXT record on `@`.)
2. In Replit → Deployments → Domains, add BOTH `asrenterprises.in` AND
   `www.asrenterprises.in`. Wait until both show "Verified" and "SSL Active"
   (usually 5–30 min, sometimes up to 24h for DNS to propagate).
3. After SSL is active, set these deployment **Secrets** (not dev secrets):
   - `FORCE_HTTPS=true`
   - `CANONICAL_HOST=www.asrenterprises.in`
   - `ALLOWED_REDIRECT_HOSTS=asrenterprises.in,www.asrenterprises.in`
4. Re-deploy. The `NET::ERR_CERT_COMMON_NAME_INVALID` will disappear once the
   cert covers both names AND the canonical-host redirect routes everything
   through `www.`.

### Known limitation (NOT changed in this round)
Admin/staff "auth" is a `localStorage` flag (`asrAdminAuth=true`) — anyone who
sets that key in devtools can access the admin UI. The only real protection is
that backend admin endpoints ARE rate-limited and the OTP flow is gated, but
direct API calls don't require an Authorization header. Migrating to real JWT +
backend `Depends(require_admin)` is a multi-day refactor touching every admin
route — flagged here so it's not forgotten.
