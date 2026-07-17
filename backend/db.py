import os
import logging
import time

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError

try:
    from .fallback_db import FallbackDatabase, load_fallback_database
except ImportError:
    from fallback_db import FallbackDatabase, load_fallback_database

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
MONGODB_DB = os.getenv("MONGODB_DB", "PayerPayee")

_client = None
_db = None
_data_source = "uninitialized"
_next_mongo_retry_at = 0

logger = logging.getLogger(__name__)


def connect_mongo():
    global _client, _db, _data_source, _next_mongo_retry_at

    if _db is not None and not isinstance(_db, FallbackDatabase):
        return _db

    if _db is not None and time.monotonic() < _next_mongo_retry_at:
        return _db

    try:
        _client = MongoClient(
            MONGODB_URI,
            serverSelectionTimeoutMS=int(os.getenv("MONGODB_TIMEOUT_MS", "5000")),
            connectTimeoutMS=int(os.getenv("MONGODB_TIMEOUT_MS", "5000")),
        )
        _client.admin.command("ping")
        _db = _client[MONGODB_DB]
        _data_source = "mongodb"
    except (PyMongoError, ValueError) as error:
        if _client is not None:
            _client.close()
        _client = None
        _db = load_fallback_database()
        _data_source = "bundled-snapshot"
        _next_mongo_retry_at = time.monotonic() + int(os.getenv("MONGODB_RETRY_SECONDS", "60"))
        logger.warning("MongoDB unavailable; using bundled claims snapshot: %s", type(error).__name__)
    return _db


def close_mongo():
    global _client, _db, _data_source, _next_mongo_retry_at

    if _client is not None:
        _client.close()
        _client = None
    _db = None
    _data_source = "uninitialized"
    _next_mongo_retry_at = 0


def get_mongo_config():
    return {
        "dbName": MONGODB_DB,
        "hasUri": bool(MONGODB_URI),
        "dataSource": _data_source,
    }
