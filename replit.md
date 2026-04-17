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