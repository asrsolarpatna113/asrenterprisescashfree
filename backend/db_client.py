"""Database client wrapper with optional disk persistence for in-memory mode.

When USE_IN_MEMORY_MONGO=true, all data lives inside the Python process and is
lost on every restart. To prevent that (which makes new staff / leads / etc.
appear to "disappear" after every workflow restart), this module also provides
helpers to snapshot all collections to a JSON file on disk and restore them on
startup.
"""

import asyncio
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

USE_IN_MEMORY = os.environ.get("USE_IN_MEMORY_MONGO", "").lower() == "true"

if USE_IN_MEMORY:
    from mongomock_motor import AsyncMongoMockClient as AsyncIOMotorClient
else:
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore

logger = logging.getLogger(__name__)

_DEFAULT_SNAPSHOT_PATH = Path(__file__).resolve().parent / "data" / "mongo_snapshot.json"
SNAPSHOT_PATH = Path(os.environ.get("MONGO_SNAPSHOT_PATH", str(_DEFAULT_SNAPSHOT_PATH)))

# Staff IDs that must NEVER be auto-restored from the snapshot file.
# These are legacy test/demo accounts that the owner has permanently deleted
# from HR Management. Without this guard, every workflow restart re-loads the
# snapshot and the deleted records reappear.
_BLOCKED_STAFF_IDS = {"ASR1003", "ASR1004"}

# Single shared client/db instance for the whole process. With real MongoDB
# every connection ends up at the same server so this would not matter, but
# in USE_IN_MEMORY mode (mongomock-motor) every `AsyncIOMotorClient(...)` call
# spins up its OWN isolated in-memory store. That caused Cashfree payments to
# upsert into one mongomock instance while the CRM "Cashfree Payments" page
# read from a different one, producing the "payment succeeded but does not show
# in CRM" bug. All route modules must call get_client()/get_db() instead of
# constructing their own client.
_shared_client = None
_shared_dbs: dict = {}


def get_client():
    """Return the process-wide shared mongo client (lazy-initialized)."""
    global _shared_client
    if _shared_client is None:
        mongo_url = os.environ.get("MONGO_URL", "mongodb://127.0.0.1:27017")
        _shared_client = AsyncIOMotorClient(mongo_url)
    return _shared_client


def get_db(db_name: str = None):
    """Return a shared database handle keyed by db_name.

    Cached per-name so different callers that pass different db_name values
    don't silently share a DB handle resolved by whoever imported first.
    """
    name = db_name or os.environ.get("DB_NAME", "asr_dev")
    if name not in _shared_dbs:
        _shared_dbs[name] = get_client()[name]
    return _shared_dbs[name]

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
    """Load a previously saved snapshot into the given database. Returns
    number of documents inserted. Safe to call when no snapshot exists."""
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
        # Strip Mongo internal _id (mongomock will reassign) to avoid type conflicts.
        # Also drop any record whose staff_id/employee_id is in the blocked set
        # so deleted test accounts can never be resurrected from an old snapshot.
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
    """Persist all collections from the in-memory database to disk.

    Serialized via an asyncio lock so concurrent invocations (periodic + shutdown)
    cannot race on the same temp file.
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
    """Background task: snapshot the database to disk every `interval` seconds."""
    if not USE_IN_MEMORY:
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
