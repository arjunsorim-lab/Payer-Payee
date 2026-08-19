#!/usr/bin/env python3
"""Minimal local connection example."""

from pymongo import MongoClient

client = MongoClient("mongodb://127.0.0.1:27018/")
claims = client["payer_payee"]["837_claims"]

print(claims.find_one({}, {"_id": 0, "Patient_ID": 1, "Claim_ID": 1}))
