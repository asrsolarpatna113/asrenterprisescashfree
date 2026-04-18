"""Database client — production-grade persistent storage.

Priority order for database selection:
  1. MONGO_URI is set → real MongoDB Atlas (fully persistent, production mode)
  2. USE_IN_MEMORY_MONGO=true  → mongomock-motor with JSON snapshot fallback
  3. default                   → real Motor client against MONGO_URL

Setting MONGO_URI automatically disables in-memory mode so production never
accidentally runs against the local mock database.
"""

import asyncio
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Determine database mode
# ──────────────────────────────────────────────────────────────────────────────

# If an external Atlas URI is configured, always use real MongoDB.
MONGO_URI = os.environ.get("MONGO_URI", "").strip()

if MONGO_URI:
    USE_IN_MEMORY = False
    logger.info("[db] MONGO_URI is set — using MongoDB Atlas (persistent)")
else:
    USE_IN_MEMORY = os.environ.get("USE_IN_MEMORY_MONGO", "").lower() == "true"
    if USE_IN_MEMORY:
        logger.info("[db] USE_IN_MEMORY_MONGO=true — using in-memory database with snapshot")
    else:
        logger.info("[db] Using local/network MongoDB via MONGO_URL")

if USE_IN_MEMORY:
    from mongomock_motor import AsyncMongoMockClient as AsyncIOMotorClient
else:
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore

# ──────────────────────────────────────────────────────────────────────────────
# Snapshot (in-memory fallback only)
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "mongo_snapshot.json"
SNAPSHOT_PATH = Path(os.environ.get("MONGO_SNAPSHOT_PATH", str(_DEFAULT_SNAPSHOT_PATH)))

# Staff IDs that must NEVER be auto-restored from the snapshot file.
_BLOCKED_STAFF_IDS = {"ASR1003", "ASR1004"}

# ──────────────────────────────────────────────────────────────────────────────
# Shared client / db (process-wide singleton)
# ──────────────────────────────────────────────────────────────────────────────

_shared_client = None
_shared_dbs: dict = {}


def get_client():
    """Return the process-wide shared MongoDB client (lazy-initialized).

    Connection string priority:
      1. MONGO_URI  (Atlas / external)
      2. MONGO_URL  (local / network fallback)
    """
    global _shared_client
    if _shared_client is None:
        if MONGO_URI:
            _shared_client = AsyncIOMotorClient(
                MONGO_URI,
                serverSelectionTimeoutMS=10_000,
                connectTimeoutMS=10_000,
                socketTimeoutMS=30_000,
                retryWrites=True,
                retryReads=True,
                maxPoolSize=10,
                minPoolSize=1,
            )
            logger.info("[db] MongoDB Atlas client created")
        else:
            mongo_url = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
            _shared_client = AsyncIOMotorClient(mongo_url)
    return _shared_client


def get_db(db_name: str = None):
    """Return a shared database handle keyed by db_name."""
    name = db_name or os.environ.get("DB_NAME", "asr_dev")
    if name not in _shared_dbs:
        _shared_dbs[name] = get_client()[name]
    return _shared_dbs[name]


async def ping_db(db_name: str = None) -> bool:
    """Verify the database connection is alive. Returns True on success."""
    try:
        db = get_db(db_name)
        await db.command("ping")
        return True
    except Exception as exc:
        logger.error(f"[db] ping failed: {exc}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# JSON snapshot helpers (in-memory mode only)
# ──────────────────────────────────────────────────────────────────────────────

_save_lock = asyncio.Lock()


def _json_default(obj):
    if isinstance(obj, (datetime, date)):
        return {"__dt__": obj.isoformat()}
    try:
        from bson import ObjectId
        if isinstance(obj, ObjectId):
            return None
    except Exception:
        pass
    return str(obj)


def _json_object_hook(d):
    if isinstance(d, dict) and len(d) == 1 and "__dt__" in d:
        try:
            return datetime.fromisoformat(d["__dt__"])
        except Exception:
            return d["__dt__"]
    return d


async def load_snapshot(client, db_name: str) -> int:
    """Load a previously saved snapshot into the given database.

    No-op when USE_IN_MEMORY is False (real MongoDB handles persistence).
    """
    if not USE_IN_MEMORY:
        return 0
    if not SNAPSHOT_PATH.exists():
        return 0
    try:
        with SNAPSHOT_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f, object_hook=_json_object_hook)
    except Exception as e:
        logger.warning(f"[mongo-snapshot] failed to read {SNAPSHOT_PATH}: {e}")
        return 0

    db = client[db_name]
    total = 0
    for coll_name, docs in (data or {}).items():
        if not isinstance(docs, list) or not docs:
            continue
        clean = []
        for d in docs:
            if isinstance(d, dict):
                d.pop("_id", None)
                if coll_name in ("hr_employees", "crm_staff_accounts"):
                    sid = d.get("staff_id") or d.get("employee_id")
                    if sid in _BLOCKED_STAFF_IDS:
                        continue
                clean.append(d)
        if clean:
            try:
                await db[coll_name].insert_many(clean)
                total += len(clean)
            except Exception as e:
                logger.warning(f"[mongo-snapshot] insert into {coll_name} failed: {e}")
    logger.info(f"[mongo-snapshot] restored {total} documents from {SNAPSHOT_PATH}")
    return total


async def save_snapshot(client, db_name: str) -> int:
    """Persist all in-memory collections to disk.

    No-op when USE_IN_MEMORY is False — real MongoDB already persists.
    """
    if not USE_IN_MEMORY:
        return 0
    async with _save_lock:
        try:
            SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            db = client[db_name]
            names = await db.list_collection_names()
            out = {}
            total = 0
            for n in names:
                try:
                    docs = await db[n].find({}, {"_id": 0}).to_list(None)
                except Exception:
                    continue
                out[n] = docs
                total += len(docs)
            tmp = SNAPSHOT_PATH.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(out, f, default=_json_default)
            tmp.replace(SNAPSHOT_PATH)
            return total
        except Exception as e:
            logger.warning(f"[mongo-snapshot] save failed: {e}")
            return 0


async def periodic_snapshot(client, db_name: str, interval: int = 15):
    """Background task: snapshot in-memory DB to disk every `interval` seconds.

    Exits immediately when running against real MongoDB.
    """
    if not USE_IN_MEMORY:
        logger.info("[db] Periodic snapshot disabled — using real MongoDB")
        return
    while True:
        try:
            await asyncio.sleep(interval)
            await save_snapshot(client, db_name)
        except asyncio.CancelledError:
            await save_snapshot(client, db_name)
            break
        except Exception as e:
            logger.warning(f"[mongo-snapshot] periodic save error: {e}")
