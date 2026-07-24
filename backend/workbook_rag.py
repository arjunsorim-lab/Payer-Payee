"""Workbook-only local FAISS retrieval for Provider LLM evidence.

The vector index retrieves evidence only. Monetary values always come from
``financial_engine``.
"""

from __future__ import annotations

import json
import re
from hashlib import blake2b
from pathlib import Path
from threading import RLock

import numpy as np

try:
    import faiss
except ImportError as error:  # surfaced by the affected API endpoints
    faiss = None
    FAISS_IMPORT_ERROR = error
else:
    FAISS_IMPORT_ERROR = None

try:
    from .workbook_enrichment import RAG_INDEX_VERSION
except ImportError:
    from workbook_enrichment import RAG_INDEX_VERSION


VECTOR_DIMENSION = 384
INDEX_ROOT = Path(__file__).resolve().parent / ".rag_index"
_CACHE = {}
_LOCK = RLock()
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./+-]+")
_DIRECT_ID_PATTERN = re.compile(r"\b(?:MBR|PATMBR)\d+\b", re.IGNORECASE)


def clear_rag_cache():
    with _LOCK:
        _CACHE.clear()


def _require_faiss():
    if faiss is None:
        raise RuntimeError(
            "Workbook RAG is unavailable because faiss-cpu could not be loaded. "
            f"Install backend requirements and restart the backend ({type(FAISS_IMPORT_ERROR).__name__})."
        )


def _sanitize_text(value):
    return _DIRECT_ID_PATTERN.sub("[internal identifier removed]", str(value or ""))


def _embedding(text):
    vector = np.zeros(VECTOR_DIMENSION, dtype="float32")
    for token in _TOKEN_PATTERN.findall(_sanitize_text(text).lower()):
        digest = blake2b(token.encode("utf-8"), digest_size=16).digest()
        index = int.from_bytes(digest[:8], "little") % VECTOR_DIMENSION
        sign = 1.0 if digest[8] & 1 else -1.0
        vector[index] += sign * (1.0 + min(len(token), 20) / 20.0)
    norm = np.linalg.norm(vector)
    if norm:
        vector /= norm
    return vector


def _claim_document(claim, workbook_hash):
    fields = claim["workbookFields"]
    selected_fields = [
        "Service_Date_From",
        "CPT_Code",
        "CPT_Description",
        "ICD10_Diagnosis_Code",
        "ICD10_Diagnosis_Description",
        "Place_of_Service_Code",
        "Place_of_Service_Description",
        "Payer_Name",
        "Billing_Provider_Name",
        "Claim_Status_Description",
        "Charge_Amount",
        "Allowed_Amount",
        "Paid_Amount",
        "Patient_Responsibility",
        "Adjustment_Amount",
        "Expected_Reimbursement",
        "Contract_Allowed_Amount",
        "Underpayment_Amount",
        "Underpayment_Flag",
        "Outstanding_Patient_Balance",
        "Balance_Status",
        "Days_Outstanding",
        "Aging_Bucket",
        "Payment_Plan_Status",
        "Collection_Status",
        "Prior_Authorization_Required",
        "Authorization_Status",
        "Referral_Required",
        "Referral_Status",
        "Corrected_Claim_Flag",
        "Duplicate_Claim_Flag",
        "Related_Claim_Flag",
        "Condition_Resolved",
        "Treatment_Outcome",
        "Repeat_Visit_Reason",
        "Comparable_Episodes_Count",
        "Reason_Code",
        "Reason_Description",
    ]
    content = " | ".join(
        f"{name}: {_sanitize_text(fields.get(name))}"
        for name in selected_fields
        if fields.get(name) not in (None, "")
    )
    return {
        "text": content,
        "fields_used": [name for name in selected_fields if fields.get(name) not in (None, "")],
        "metadata": {
            "source_sheet": "837_Claims",
            "source_row": claim["workbookSourceRow"],
            "claim_id": claim["claimId"],
            "member_id": claim["memberId"],
            "episode_id": claim.get("episodeId") or "",
            "reason_code": str(fields.get("Reason_Code") or ""),
            "service_date": claim.get("dos") or "",
            "is_historical_reference": bool(claim["isHistoricalReference"]),
            "workbook_hash": workbook_hash,
        },
    }


def _supporting_documents(database):
    specs = [
        ("Reason_Code_Legend", database.reason_legend_rows),
        ("New_Fields_Dictionary", database.field_dictionary_rows),
        ("Data_Notes_READ_ME", database.data_notes_rows),
    ]
    documents = []
    for sheet_name, rows in specs:
        for row in rows:
            source_row = int(row.get("_source_row") or 0)
            content_fields = {
                key: value
                for key, value in row.items()
                if key != "_source_row" and value not in (None, "")
            }
            documents.append({
                "text": _sanitize_text(" | ".join(f"{key}: {value}" for key, value in content_fields.items())),
                "fields_used": list(content_fields),
                "metadata": {
                    "source_sheet": sheet_name,
                    "source_row": source_row,
                    "claim_id": "",
                    "member_id": "",
                    "episode_id": "",
                    "reason_code": str(content_fields.get("Reason_Code") or ""),
                    "service_date": "",
                    "is_historical_reference": False,
                    "workbook_hash": database.workbook_hash,
                },
            })
    return documents


def _paths(database):
    root = INDEX_ROOT / database.workbook_hash
    return root, root / "index.faiss", root / "documents.json", root / "metadata.json"


def build_index(database, *, force=False):
    _require_faiss()
    root, index_path, documents_path, metadata_path = _paths(database)
    with _LOCK:
        if not force and database.workbook_hash in _CACHE:
            return _CACHE[database.workbook_hash]
        if not force and index_path.is_file() and documents_path.is_file() and metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("index_version") == RAG_INDEX_VERSION:
                bundle = {
                    "index": faiss.read_index(str(index_path)),
                    "documents": json.loads(documents_path.read_text(encoding="utf-8")),
                    "metadata": metadata,
                    "path": str(root),
                }
                _CACHE[database.workbook_hash] = bundle
                return bundle

        documents = [
            _claim_document(claim, database.workbook_hash)
            for claim in database.claims
        ] + _supporting_documents(database)
        matrix = np.vstack([_embedding(document["text"]) for document in documents]).astype("float32")
        index = faiss.IndexFlatIP(VECTOR_DIMENSION)
        index.add(matrix)
        root.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(index_path))
        documents_path.write_text(json.dumps(documents, separators=(",", ":")), encoding="utf-8")
        metadata = {
            "index_version": RAG_INDEX_VERSION,
            "workbook_hash": database.workbook_hash,
            "document_count": len(documents),
            "vector_dimension": VECTOR_DIMENSION,
            "embedding": "local-blake2b-feature-hashing",
            "source_sheets": ["837_Claims", "Reason_Code_Legend", "New_Fields_Dictionary", "Data_Notes_READ_ME"],
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        bundle = {"index": index, "documents": documents, "metadata": metadata, "path": str(root)}
        _CACHE[database.workbook_hash] = bundle
        return bundle


def _eligible(document, claim, cutoff):
    metadata = document["metadata"]
    if metadata["source_sheet"] != "837_Claims":
        return True
    if metadata["claim_id"] == claim["claimId"]:
        return True
    if metadata["service_date"] and metadata["service_date"] > cutoff:
        return False
    if metadata["member_id"] == claim["memberId"]:
        return True
    if metadata["episode_id"] and metadata["episode_id"] == claim.get("episodeId"):
        return True
    return bool(metadata["is_historical_reference"])


def retrieve_evidence(database, financial_result, question="", top_k=12):
    bundle = build_index(database)
    claim = database.find_claim(financial_result["claim_id"], selectable_only=True)
    if not claim:
        raise KeyError("Selectable claim was not found for workbook RAG.")
    summary = financial_result["supported_money_summary"]
    query = _sanitize_text(
        " | ".join([
            question or "provider claim financial opportunity",
            f"CPT {claim.get('cptCode')}",
            f"diagnosis {claim.get('diagnosisCode')}",
            f"reason codes {' '.join(category['reason_code'] for category in financial_result['financial_opportunities'].values())}",
            f"top opportunity {summary['top_supported_opportunity_type']}",
            f"best action {summary['best_action'].get('stage')}",
        ])
    )
    vector = _embedding(query).reshape(1, -1)
    search_size = min(max(top_k * 20, 100), len(bundle["documents"]))
    scores, indexes = bundle["index"].search(vector, search_size)
    retrieved = []
    seen = set()
    cutoff = claim.get("dos") or ""
    for document in bundle["documents"]:
        metadata = document["metadata"]
        if metadata["source_sheet"] == "837_Claims" and metadata["claim_id"] == claim["claimId"]:
            retrieved.append({
                "source_sheet": metadata["source_sheet"],
                "source_row": metadata["source_row"],
                "claim_id": metadata["claim_id"],
                "reason_code": metadata["reason_code"],
                "similarity": 1.0,
                "fields_used": document["fields_used"],
            })
            seen.add((metadata["source_sheet"], metadata["claim_id"]))
            break
    for score, index in zip(scores[0], indexes[0]):
        if index < 0:
            continue
        document = bundle["documents"][int(index)]
        if not _eligible(document, claim, cutoff):
            continue
        metadata = document["metadata"]
        dedupe_key = (
            metadata["source_sheet"],
            metadata["claim_id"] or metadata["source_row"],
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        retrieved.append({
            "source_sheet": metadata["source_sheet"],
            "source_row": metadata["source_row"],
            "claim_id": metadata["claim_id"],
            "reason_code": metadata["reason_code"],
            "similarity": round(float(score), 6),
            "fields_used": document["fields_used"],
        })
        if len(retrieved) >= top_k:
            break
    return {
        "index_version": RAG_INDEX_VERSION,
        "workbook_hash": database.workbook_hash,
        "query": query,
        "retrieved_chunks": retrieved,
        "document_count": bundle["metadata"]["document_count"],
        "index_path": bundle["path"],
    }
