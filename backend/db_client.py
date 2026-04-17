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
        # Strip Mongo internal _id (mongomock will reassign) to avoid type conflicts
        clean = []
        for d in docs:
            if isinstance(d, dict):
                d.pop("_id", None)
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
