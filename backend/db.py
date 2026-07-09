import os

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017/")
MONGODB_DB = os.getenv("MONGODB_DB", "PayerPayee")

_client = None
_db = None


def connect_mongo():
    global _client, _db

    if _db is not None:
        return _db

    _client = MongoClient(MONGODB_URI)
    _db = _client[MONGODB_DB]
    return _db


def close_mongo():
    global _client, _db

    if _client is not None:
        _client.close()
        _client = None
        _db = None


def get_mongo_config():
    return {
        "dbName": MONGODB_DB,
        "hasUri": bool(MONGODB_URI),
    }
