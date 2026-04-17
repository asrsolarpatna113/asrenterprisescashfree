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
