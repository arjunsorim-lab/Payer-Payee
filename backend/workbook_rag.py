"""Workbook-only FAISS retrieval using local Ollama embeddings.

This module creates semantic, de-identified evidence documents. It never
calculates predictions or financial values.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any

import numpy as np

try:
    import faiss
except ImportError as error:
    faiss = None
    FAISS_IMPORT_ERROR = error
else:
    FAISS_IMPORT_ERROR = None

try:
    from .ollama_service import OllamaClient, OllamaError
    from .workbook_enrichment import load_workbook_database
except ImportError:
    from ollama_service import OllamaClient, OllamaError
    from workbook_enrichment import load_workbook_database


RAG_VERSION = os.getenv("RAG_VERSION", "workbook-rag-v1")
DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "8"))
VECTOR_WEIGHT = float(os.getenv("RAG_VECTOR_WEIGHT", "0.55"))
STRUCTURED_WEIGHT = float(os.getenv("RAG_STRUCTURED_WEIGHT", "0.45"))
EMBED_BATCH_SIZE = max(int(os.getenv("RAG_EMBED_BATCH_SIZE", "64")), 1)
INDEX_ROOT = Path(
    os.getenv(
        "RAG_INDEX_DIR",
        str(Path(__file__).resolve().parent / ".rag_index"),
    )
)
_CACHE: dict[tuple[str, str, str], dict[str, Any]] = {}
_LOCK = RLock()
_MEMBER_ID_PATTERN = re.compile(r"\b(?:MBR|PATMBR)\d+\b", re.IGNORECASE)
_PHI_FIELD_PATTERN = re.compile(
    r"(patient.*(?:name|dob|birth|account|address|phone|email)|"
    r"subscriber.*(?:name|member)|"
    r"(?:^|[_\s])(?:first_name|last_name|dob|date_of_birth|account_number|"
    r"address|phone|telephone|email)(?:$|[_\s]))",
    re.IGNORECASE,
)


CLAIM_DOCUMENT_SPECS = {
    "claim_financial": [
        "Service_Date_From",
        "Payer_ID",
        "Payer_Name",
        "CPT_Code",
        "CPT_Description",
        "ICD10_Family",
        "Place_of_Service_Code",
        "Units",
        "Claim_Status_Description",
        "Charge_Amount",
        "Allowed_Amount",
        "Paid_Amount",
        "Patient_Responsibility",
        "Adjustment_Amount",
        "Expected_Reimbursement",
        "Underpayment_Flag",
        "Underpayment_Amount",
        "Recovered_Amount",
        "Payment_Tolerance",
        "Reason_Code",
    ],
    "contract_payment": [
        "Contract_ID",
        "Network_Status",
        "Fee_Schedule_Amount",
        "Contract_Allowed_Amount",
        "Expected_Reimbursement",
        "Paid_Amount",
        "Recovered_Amount",
        "Underpayment_Amount",
        "Underpayment_Flag",
        "Payment_Tolerance",
    ],
    "denial": [
        "Claim_Status_Description",
        "Denial_Reason",
        "CARC_Code",
        "RARC_Code",
        "Denial_Correctable_Flag",
        "Appeal_Status",
        "Resubmission_Status",
        "Recovered_Amount",
        "Denial_Resolution",
    ],
    "patient_balance": [
        "Patient_Responsibility",
        "Patient_Payment_Received",
        "Outstanding_Patient_Balance",
        "Balance_Status",
        "Days_Outstanding",
        "Aging_Bucket",
        "Payment_Plan_Status",
        "Collection_Status",
        "Chk_Confirmed_Unpaid_Balance",
    ],
    "authorization_referral": [
        "Prior_Authorization_Required",
        "Authorization_Status",
        "Authorization_Valid_From",
        "Authorization_Valid_To",
        "Authorized_Units",
        "Referral_Required",
        "Referral_Status",
    ],
    "episode": [
        "Episode_ID",
        "Related_Claim_Flag",
        "Repeat_Visit_Reason",
        "Condition_Resolved",
        "Treatment_Outcome",
        "Comparable_Episodes_Count",
        "Episode_Duration_Days",
        "Allowed_Amount",
        "Paid_Amount",
    ],
}


def clear_rag_cache():
    with _LOCK:
        _CACHE.clear()


def _require_faiss():
    if faiss is None:
        raise RuntimeError(
            "Workbook RAG requires faiss-cpu. "
            f"Import failed with {type(FAISS_IMPORT_ERROR).__name__}."
        )


def _clean_text(value: Any) -> str:
    return _MEMBER_ID_PATTERN.sub(
        "[internal identifier removed]", str(value or "")
    ).strip()


def _document_id(
    workbook_hash: str, document_type: str, source_sheet: str, source_row: int
) -> str:
    identity = f"{workbook_hash}|{document_type}|{source_sheet}|{source_row}"
    return sha256(identity.encode("utf-8")).hexdigest()[:24]


def _metadata(
    database,
    claim,
    document_type: str,
    fields_used: list[str],
) -> dict[str, Any]:
    fields = claim["workbookFields"]
    return {
        "document_id": _document_id(
            database.workbook_hash,
            document_type,
            "837_Claims",
            claim["workbookSourceRow"],
        ),
        "document_type": document_type,
        "source_sheet": "837_Claims",
        "source_row": claim["workbookSourceRow"],
        "claim_id": claim["claimId"],
        "member_id": claim["memberId"],
        "episode_id": claim.get("episodeId") or "",
        "service_date": claim.get("dos") or "",
        "payer_id": claim.get("payerId") or "",
        "provider_id": claim.get("billingProviderNpi") or "",
        "cpt_code": claim.get("cptCode") or "",
        "diagnosis_family": str(fields.get("ICD10_Family") or ""),
        "place_of_service": claim.get("placeOfServiceCode") or "",
        "units": claim.get("units") or 0,
        "reason_code": str(fields.get("Reason_Code") or ""),
        "is_historical_reference": bool(claim["isHistoricalReference"]),
        "workbook_hash": database.workbook_hash,
        "fields_used": fields_used,
    }


def _claim_documents(database, claim) -> list[dict[str, Any]]:
    fields = claim["workbookFields"]
    documents = []
    for document_type, field_names in CLAIM_DOCUMENT_SPECS.items():
        used = [
            name
            for name in field_names
            if not _PHI_FIELD_PATTERN.search(name)
            and fields.get(name) not in (None, "")
        ]
        if not used:
            continue
        parts = [
            f"evidence type: {document_type.replace('_', ' ')}",
            f"claim ID: {claim['claimId']}",
        ]
        parts.extend(f"{name}: {_clean_text(fields.get(name))}" for name in used)
        documents.append(
            {
                "text": " | ".join(parts),
                "metadata": _metadata(database, claim, document_type, used),
            }
        )
    return documents


def _supporting_documents(database) -> list[dict[str, Any]]:
    specs = [
        ("reason_code", "Reason_Code_Legend", database.reason_legend_rows),
        (
            "field_definition",
            "New_Fields_Dictionary",
            database.field_dictionary_rows,
        ),
        ("data_note", "Data_Notes_READ_ME", database.data_notes_rows),
    ]
    documents = []
    for document_type, sheet_name, rows in specs:
        for row in rows:
            source_row = int(row.get("_source_row") or 0)
            content = {
                key: value
                for key, value in row.items()
                if key != "_source_row"
                and value not in (None, "")
                and not _PHI_FIELD_PATTERN.search(key)
                and not _PHI_FIELD_PATTERN.search(str(value))
            }
            if not content:
                continue
            fields_used = list(content)
            text = " | ".join(
                [f"evidence type: {document_type.replace('_', ' ')}"]
                + [
                    f"{key}: {_clean_text(value)}"
                    for key, value in content.items()
                ]
            )
            metadata = {
                "document_id": _document_id(
                    database.workbook_hash,
                    document_type,
                    sheet_name,
                    source_row,
                ),
                "document_type": document_type,
                "source_sheet": sheet_name,
                "source_row": source_row,
                "claim_id": "",
                "member_id": "",
                "episode_id": "",
                "service_date": "",
                "payer_id": "",
                "provider_id": "",
                "cpt_code": "",
                "diagnosis_family": "",
                "place_of_service": "",
                "units": 0,
                "reason_code": str(content.get("Reason_Code") or ""),
                "is_historical_reference": False,
                "workbook_hash": database.workbook_hash,
                "fields_used": fields_used,
            }
            documents.append({"text": text, "metadata": metadata})
    return documents


def create_documents(database) -> list[dict[str, Any]]:
    documents = []
    for claim in database.claims:
        documents.extend(_claim_documents(database, claim))
    documents.extend(_supporting_documents(database))
    return documents


def _paths(database, embedding_model: str):
    root = INDEX_ROOT / database.workbook_hash
    safe_model = re.sub(r"[^A-Za-z0-9_.-]+", "_", embedding_model)
    return {
        "root": root,
        "index": root / "index.faiss",
        "metadata": root / "metadata.json",
        "manifest": root / "manifest.json",
        "model_marker": safe_model,
    }


def _normalize_matrix(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype="float32")
    if matrix.ndim != 2 or not matrix.shape[0] or not matrix.shape[1]:
        raise RuntimeError("Ollama returned an invalid embedding matrix.")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise RuntimeError("Ollama returned a zero-length embedding vector.")
    return matrix / norms


def _manifest_valid(
    manifest: dict[str, Any],
    database,
    client: OllamaClient,
    index,
    documents,
) -> bool:
    return bool(
        manifest.get("workbook_hash") == database.workbook_hash
        and manifest.get("embedding_model") == client.embed_model
        and manifest.get("rag_version") == RAG_VERSION
        and int(manifest.get("document_count") or 0) == len(documents)
        and int(manifest.get("vector_count") or 0) == index.ntotal
        and int(manifest.get("embedding_dimension") or 0) == index.d
        and index.ntotal == len(documents)
    )


def build_index(
    database,
    *,
    force: bool = False,
    client: OllamaClient | None = None,
) -> dict[str, Any]:
    _require_faiss()
    client = client or OllamaClient()
    paths = _paths(database, client.embed_model)
    cache_key = (database.workbook_hash, client.embed_model, RAG_VERSION)
    with _LOCK:
        if not force and cache_key in _CACHE:
            return _CACHE[cache_key]
        if (
            not force
            and paths["index"].is_file()
            and paths["metadata"].is_file()
            and paths["manifest"].is_file()
        ):
            documents = json.loads(paths["metadata"].read_text(encoding="utf-8"))
            manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            index = faiss.read_index(str(paths["index"]))
            if _manifest_valid(manifest, database, client, index, documents):
                bundle = {
                    "index": index,
                    "documents": documents,
                    "manifest": manifest,
                    "path": str(paths["root"]),
                }
                _CACHE[cache_key] = bundle
                return bundle

        documents = create_documents(database)
        if not documents:
            raise RuntimeError("No de-identified workbook RAG documents were created.")
        vectors = []
        errors = []
        for start in range(0, len(documents), EMBED_BATCH_SIZE):
            batch = documents[start : start + EMBED_BATCH_SIZE]
            try:
                vectors.extend(client.embed([item["text"] for item in batch]))
            except OllamaError as error:
                errors.append({"batch_start": start, "error": str(error)})
                break
        if errors:
            raise RuntimeError(
                "Ollama workbook embedding failed: " + errors[0]["error"]
            )
        matrix = _normalize_matrix(np.asarray(vectors, dtype="float32"))
        if matrix.shape[0] != len(documents):
            raise RuntimeError(
                "Embedding count does not match workbook document count."
            )
        index = faiss.IndexFlatIP(matrix.shape[1])
        index.add(matrix)
        if index.ntotal != len(documents):
            raise RuntimeError("FAISS vector count does not match metadata count.")
        paths["root"].mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(paths["index"]))
        paths["metadata"].write_text(
            json.dumps(documents, separators=(",", ":")),
            encoding="utf-8",
        )
        manifest = {
            "workbook_hash": database.workbook_hash,
            "embedding_model": client.embed_model,
            "embedding_dimension": int(matrix.shape[1]),
            "document_count": len(documents),
            "vector_count": int(index.ntotal),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rag_version": RAG_VERSION,
            "selectable_claim_count": len(database.selectable_claims),
            "historical_claim_count": len(database.historical_claims),
            "workbook_row_count": len(database.claims),
            "skipped_documents": 0,
            "errors": [],
        }
        paths["manifest"].write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        bundle = {
            "index": index,
            "documents": documents,
            "manifest": manifest,
            "path": str(paths["root"]),
        }
        _CACHE[cache_key] = bundle
        return bundle


def index_status(database, client: OllamaClient | None = None) -> dict[str, Any]:
    client = client or OllamaClient()
    paths = _paths(database, client.embed_model)
    if (
        not paths["manifest"].is_file()
        or not paths["index"].is_file()
        or not paths["metadata"].is_file()
    ):
        return {
            "ready": False,
            "workbook_hash": database.workbook_hash,
            "index_version": RAG_VERSION,
            "embedding_model": client.embed_model,
            "index_path": str(paths["root"]),
        }
    try:
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        index = faiss.read_index(str(paths["index"])) if faiss else None
        ready = bool(
            index
            and manifest.get("workbook_hash") == database.workbook_hash
            and manifest.get("embedding_model") == client.embed_model
            and manifest.get("rag_version") == RAG_VERSION
            and int(manifest.get("document_count") or 0) == index.ntotal
            and int(manifest.get("vector_count") or 0) == index.ntotal
            and int(manifest.get("embedding_dimension") or 0) == index.d
        )
        return {
            "ready": ready,
            "workbook_hash": database.workbook_hash,
            "document_count": int(manifest.get("document_count") or 0),
            "vector_count": int(index.ntotal) if index else 0,
            "embedding_dimension": int(manifest.get("embedding_dimension") or 0),
            "index_version": RAG_VERSION,
            "embedding_model": client.embed_model,
            "index_path": str(paths["root"]),
            "created_at": manifest.get("created_at"),
        }
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return {
            "ready": False,
            "workbook_hash": database.workbook_hash,
            "index_version": RAG_VERSION,
            "embedding_model": client.embed_model,
            "index_path": str(paths["root"]),
            "error": str(error),
        }


def _is_scoped(
    metadata,
    claim,
    cutoff: str,
    qualified_peer_ids: set[str] | None = None,
) -> bool:
    if metadata["source_sheet"] != "837_Claims":
        return True
    if metadata["claim_id"] == claim["claimId"]:
        return True
    service_date = metadata.get("service_date") or ""
    if not service_date or service_date >= cutoff:
        return False
    if metadata["member_id"] == claim["memberId"]:
        return True
    if metadata["episode_id"] and metadata["episode_id"] == claim.get("episodeId"):
        return True
    if not metadata["is_historical_reference"]:
        return False
    if qualified_peer_ids:
        return metadata["claim_id"] in qualified_peer_ids
    fields = claim["workbookFields"]
    return bool(
        metadata["cpt_code"] == claim.get("cptCode")
        or metadata["diagnosis_family"] == str(fields.get("ICD10_Family") or "")
        or (
            metadata["payer_id"] == claim.get("payerId")
            and metadata["place_of_service"] == claim.get("placeOfServiceCode")
        )
    )


def _structured_score(metadata, claim, reason_codes: set[str]) -> float:
    if metadata["source_sheet"] != "837_Claims":
        if metadata["document_type"] == "reason_code":
            return 0.55 if metadata["reason_code"] in reason_codes else 0.4
        if metadata["document_type"] == "field_definition":
            return 0.35
        return 0.25
    if metadata["claim_id"] == claim["claimId"]:
        return 1.0
    score = 0.0
    fields = claim["workbookFields"]
    if metadata["member_id"] == claim["memberId"]:
        score += 0.42
    if metadata["episode_id"] and metadata["episode_id"] == claim.get("episodeId"):
        score += 0.36
    if metadata["cpt_code"] == claim.get("cptCode"):
        score += 0.12
    if metadata["diagnosis_family"] == str(fields.get("ICD10_Family") or ""):
        score += 0.1
    if metadata["payer_id"] == claim.get("payerId"):
        score += 0.08
    if metadata["provider_id"] == claim.get("billingProviderNpi"):
        score += 0.06
    if metadata["place_of_service"] == claim.get("placeOfServiceCode"):
        score += 0.05
    if metadata["units"] == claim.get("units"):
        score += 0.03
    if metadata["reason_code"] and metadata["reason_code"] in reason_codes:
        score += 0.08
    return min(score, 1.0)


def _evidence_type(metadata, claim, qualified_peer_ids):
    if metadata["source_sheet"] != "837_Claims":
        return metadata["document_type"]
    if metadata["document_type"] == "short_timeframe_pair":
        return "short_timeframe_claim_pair"
    if metadata["claim_id"] == claim["claimId"]:
        return "exact_selected_claim"
    if metadata["episode_id"] and metadata["episode_id"] == claim.get("episodeId"):
        return "same_episode_claim"
    if metadata["member_id"] == claim["memberId"]:
        return "earlier_same_member_claim"
    if metadata["claim_id"] in qualified_peer_ids:
        return "matched_historical_peer"
    if metadata["cpt_code"] == claim.get("cptCode") and metadata["diagnosis_family"] == str(claim["workbookFields"].get("ICD10_Family") or ""):
        return "same_cpt_icd_family_claim"
    return metadata["document_type"]


def retrieve_claim_evidence(
    database,
    claim_id: str,
    question: str,
    top_k: int = DEFAULT_TOP_K,
    *,
    financial_result: dict[str, Any] | None = None,
    client: OllamaClient | None = None,
) -> dict[str, Any]:
    client = client or OllamaClient()
    bundle = build_index(database, client=client)
    claim = database.find_claim(claim_id, selectable_only=True)
    if not claim:
        raise KeyError("Selectable claim was not found for workbook RAG.")
    cutoff = claim.get("dos") or ""
    qualified_peer_ids = {
        str(peer_id)
        for peer_id in (financial_result or {}).get(
            "historical_prediction_basis", {}
        ).get("peer_claim_ids", [])
        if peer_id
    }
    qualified_peer_ids.update(
        str(peer_id)
        for peer_id in (financial_result or {}).get(
            "prediction", {}
        ).get("avoidable_prediction_basis", {}).get(
            "repeat_cost", {}
        ).get("evidence_claim_ids", [])
        if peer_id
    )
    reason_codes = {
        str(category.get("reason_code") or "")
        for category in (financial_result or {}).get(
            "financial_opportunities", {}
        ).values()
    }
    query = _clean_text(
        " | ".join(
            [
                question or "provider financial prediction evidence",
                f"CPT {claim.get('cptCode')}",
                f"diagnosis family {claim['workbookFields'].get('ICD10_Family')}",
                "90-day repeat probability avoidability evidence incremental repeat allowed cost",
                f"reason codes {' '.join(sorted(reason_codes))}",
            ]
        )
    )
    query_matrix = _normalize_matrix(
        np.asarray(client.embed([query]), dtype="float32")
    )
    candidates = [
        (index, document)
        for index, document in enumerate(bundle["documents"])
        if _is_scoped(
            document["metadata"],
            claim,
            cutoff,
            qualified_peer_ids,
        )
    ]
    ranked = []
    for index_position, document in candidates:
        vector = np.asarray(
            bundle["index"].reconstruct(index_position), dtype="float32"
        )
        similarity = float(np.dot(query_matrix[0], vector))
        structured = _structured_score(
            document["metadata"], claim, reason_codes
        )
        final = VECTOR_WEIGHT * similarity + STRUCTURED_WEIGHT * structured
        ranked.append((final, similarity, structured, document))
    ranked.sort(key=lambda item: item[0], reverse=True)
    retrieved = []
    seen = set()
    for final, similarity, structured, document in ranked:
        metadata = document["metadata"]
        dedupe_key = metadata["document_id"]
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        retrieved.append(
            {
                "document_id": metadata["document_id"],
                "document_type": metadata["document_type"],
                "evidence_type": _evidence_type(metadata, claim, qualified_peer_ids),
                "claim_id": metadata["claim_id"],
                "service_date": metadata["service_date"],
                "source_sheet": metadata["source_sheet"],
                "source_row": metadata["source_row"],
                "reason_code": metadata["reason_code"],
                "vector_similarity": round(similarity, 6),
                "structured_match_score": round(structured, 6),
                "final_score": round(final, 6),
                "similarity": round(final, 6),
                "similarity_score": round(final, 6),
                "fields_used": metadata["fields_used"],
                "text": document["text"],
            }
        )
        if len(retrieved) >= top_k:
            break
    short_patterns = (financial_result or {}).get("short_timeframe_patterns", [])
    if short_patterns:
        pair = short_patterns[0]
        source_claim = database.find_claim(pair["claim_2"], selectable_only=False)
        if source_claim:
            retrieved.append({
                "document_id": _document_id(database.workbook_hash, "claim_financial", "837_Claims", source_claim["workbookSourceRow"]),
                "document_type": "claim_financial",
                "evidence_type": "short_timeframe_claim_pair",
                "claim_id": pair["claim_2"], "service_date": pair["date_2"],
                "source_sheet": "837_Claims", "source_row": source_claim["workbookSourceRow"],
                "reason_code": "", "vector_similarity": 0.0,
                "structured_match_score": round(pair["relationship_score"] / 100, 6),
                "final_score": round(pair["relationship_score"] / 100, 6),
                "similarity": round(pair["relationship_score"] / 100, 6),
                "similarity_score": round(pair["relationship_score"] / 100, 6),
                "fields_used": ["Claim_ID", "Service_Date_From", "CPT_Code", "ICD10_Family", "Billing_Provider_NPI", "Payer_ID", "Episode_ID"],
                "text": f"Workbook-derived short-timeframe evidence: claims {pair['claim_1']} and {pair['claim_2']} occurred {pair['days_apart']} day(s) apart. The backend reports coded relationship flags without a clinical conclusion.",
            })
    return {
        "query": query,
        "claim_id": claim["claimId"],
        "cutoff_date": cutoff,
        "embedding_model": client.embed_model,
        "rag_version": RAG_VERSION,
        "workbook_hash": database.workbook_hash,
        "retrieved_documents": retrieved,
        "retrieved_chunks": retrieved,
        "document_count": bundle["manifest"]["document_count"],
        "vector_count": bundle["manifest"]["vector_count"],
        "index_path": bundle["path"],
    }


def retrieve_evidence(
    database,
    financial_result,
    question: str = "",
    top_k: int = DEFAULT_TOP_K,
):
    return retrieve_claim_evidence(
        database,
        financial_result["claim_id"],
        question,
        top_k,
        financial_result=financial_result,
    )


def _build_cli() -> None:
    parser = argparse.ArgumentParser(description="Build workbook Ollama RAG index")
    parser.add_argument("command", choices=["build"])
    parser.add_argument("--force", action="store_true", default=True)
    args = parser.parse_args()
    if args.command != "build":
        return
    workbook_path = os.getenv("SAVINGS_WORKBOOK_PATH", "").strip()
    if not workbook_path:
        raise SystemExit("SAVINGS_WORKBOOK_PATH is required.")
    database = load_workbook_database(workbook_path, force=True)
    bundle = build_index(database, force=args.force)
    manifest = bundle["manifest"]
    report = {
        "workbook_hash": database.workbook_hash,
        "rows_processed": len(database.claims),
        "selectable_claims": len(database.selectable_claims),
        "historical_claims": len(database.historical_claims),
        "documents_created": manifest["document_count"],
        "embeddings_created": manifest["vector_count"],
        "vector_dimension": manifest["embedding_dimension"],
        "faiss_vectors_stored": manifest["vector_count"],
        "skipped_documents": manifest["skipped_documents"],
        "errors": manifest["errors"],
        "index_location": bundle["path"],
        "embedding_model": manifest["embedding_model"],
        "rag_version": manifest["rag_version"],
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    _build_cli()
