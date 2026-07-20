"""Read-only loader for the synthetic provider-savings demonstration workbook."""

from collections import Counter, defaultdict
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path

from openpyxl import load_workbook

try:
    from .claim_mapper import normalize_claim
except ImportError:
    from claim_mapper import normalize_claim


WORKBOOK_NAME = "claims_with_dummy_savings_fields.xlsx"
ORIGINAL_SHEET = "Claims_Original"
ENRICHMENT_SHEET = "Dummy_Enrichment"
REQUIRED_SHEETS = {ORIGINAL_SHEET, ENRICHMENT_SHEET, "Data_Dictionary", "Suggestion_Criteria"}
SYNTHETIC_WARNING = (
    "Synthetic enrichment data is active. Savings and recovery recommendations are "
    "for demonstration only and must not be used for real billing decisions."
)
_WORKBOOK_CACHE = {}


def _clean_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _sheet_records(sheet):
    rows = sheet.iter_rows(values_only=True)
    headers = [str(value or "").strip() for value in next(rows)]
    return headers, [
        {header: _clean_value(value) for header, value in zip(headers, row)}
        for row in rows
        if any(value not in (None, "") for value in row)
    ]


def join_claim_enrichment(original_rows, enrichment_rows, source_hash, source_name=WORKBOOK_NAME):
    """Join by Claim_ID and verify Member_ID without changing original values."""
    claims = [claim for claim in (normalize_claim(row) for row in original_rows) if claim.get("claimId") and claim.get("memberId")]
    by_claim = defaultdict(list)
    for row in enrichment_rows:
        by_claim[str(row.get("Claim_ID") or "").strip()].append(dict(row))

    duplicate_ids = {claim_id for claim_id, rows in by_claim.items() if len(rows) > 1}
    matched = member_mismatch = ambiguous = 0
    enrichment_columns = sorted({key for row in enrichment_rows for key in row})
    original_columns = sorted({key for row in original_rows for key in row})
    for claim in claims:
        claim_id = claim["claimId"]
        member_id = claim["memberId"]
        candidates = by_claim.get(claim_id, [])
        member_matches = [row for row in candidates if str(row.get("Member_ID") or "").strip() == member_id]
        claim["sourceCsvHash"] = source_hash
        claim["sourceWorkbookHash"] = source_hash
        claim["sourceWorkbook"] = source_name
        claim["sourceCsvColumns"] = original_columns
        claim["sourceOriginalFields"] = original_columns
        claim["sourceSyntheticFields"] = enrichment_columns
        claim["dataMode"] = "synthetic_demo"
        if len(member_matches) == 1 and len(candidates) == 1:
            enrichment = {
                key: value for key, value in member_matches[0].items()
                if key not in {"Claim_ID", "Member_ID"}
            }
            claim["syntheticEnrichment"] = enrichment
            matched += 1
        else:
            claim["syntheticEnrichment"] = None
            if candidates and not member_matches:
                member_mismatch += 1
            if len(candidates) > 1:
                ambiguous += 1

    source_ids = Counter(str(row.get("Claim_ID") or "").strip() for row in original_rows)
    unmatched = len(claims) - matched
    report = {
        "source_claim_count": len(original_rows),
        "valid_source_claim_count": len(claims),
        "enrichment_row_count": len(enrichment_rows),
        "matched_enrichment_count": matched,
        "unmatched_claim_count": unmatched,
        "member_mismatch_count": member_mismatch,
        "duplicate_enrichment_count": sum(len(by_claim[claim_id]) - 1 for claim_id in duplicate_ids),
        "duplicate_enrichment_claim_count": len(duplicate_ids),
        "ambiguous_enrichment_count": ambiguous,
        "duplicate_original_claim_count": sum(count > 1 for count in source_ids.values()),
        "source_workbook": source_name,
        "source_workbook_hash": source_hash,
        "data_mode": "synthetic_demo",
        "synthetic_warning": SYNTHETIC_WARNING,
    }
    return claims, report


def read_savings_workbook(path):
    """Load the original and synthetic sheets with a hash-aware in-process cache."""
    workbook_path = Path(path).expanduser().resolve()
    stat = workbook_path.stat()
    cache_key = (str(workbook_path), stat.st_mtime_ns, stat.st_size)
    cached = _WORKBOOK_CACHE.get(cache_key)
    if cached:
        return cached

    source_hash = sha256(workbook_path.read_bytes()).hexdigest()
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    missing = REQUIRED_SHEETS.difference(workbook.sheetnames)
    if missing:
        workbook.close()
        raise ValueError(f"Workbook is missing required sheet(s): {', '.join(sorted(missing))}")
    original_columns, original_rows = _sheet_records(workbook[ORIGINAL_SHEET])
    enrichment_columns, enrichment_rows = _sheet_records(workbook[ENRICHMENT_SHEET])
    workbook.close()
    claims, report = join_claim_enrichment(original_rows, enrichment_rows, source_hash, workbook_path.name)
    report.update({
        "original_columns": original_columns,
        "enrichment_columns": enrichment_columns,
    })
    result = claims, report
    _WORKBOOK_CACHE.clear()
    _WORKBOOK_CACHE[cache_key] = result
    return result
