import json
import os
import sys
from pathlib import Path

try:
    from .claim_mapper import normalize_claim
    from .import_claims import DEFAULT_CSV_PATH, read_csv_rows
except ImportError:
    from claim_mapper import normalize_claim
    from import_claims import DEFAULT_CSV_PATH, read_csv_rows


OUTPUT_PATH = (
    Path(__file__).resolve().parent.parent
    / "frontend"
    / "public"
    / "data"
    / "claims-fallback.json"
)


def main():
    csv_path = Path(sys.argv[1] if len(sys.argv) > 1 else os.getenv("CSV_PATH", DEFAULT_CSV_PATH)).expanduser().resolve()
    claims = [normalize_claim(row) for row in read_csv_rows(csv_path)]
    claims = [claim for claim in claims if claim.get("claimId") and claim.get("memberId")]
    claims.sort(key=lambda claim: (claim.get("dos", ""), claim.get("claimId", "")), reverse=True)

    for claim in claims:
        claim.pop("raw", None)
        claim.pop("importedAt", None)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(claims, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"claims": len(claims), "output": str(OUTPUT_PATH)}))


if __name__ == "__main__":
    main()
