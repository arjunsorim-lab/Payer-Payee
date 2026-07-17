"""Generate the machine-readable Provider LLM v2 batch quality report."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .db import connect_mongo
from .provider_prediction import build_provider_batch


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "output/provider_llm_batch_report.json")
    db = connect_mongo()
    claims = list(db.claims.find({}).sort([("dos", 1), ("claimId", 1)]))
    episodes, report = build_provider_batch(
        claims,
        window_days=int(os.getenv("PROVIDER_EPISODE_WINDOW_DAYS", "90")),
        min_peers=int(os.getenv("PROVIDER_MIN_PEERS", "5")),
    )
    report["generatedAt"] = datetime.now(timezone.utc).isoformat()
    report["summary"] = {
        "totalForecastedEpisodes": len(episodes),
        "totalPotentiallyAvoidableRepeatServiceSpend": round(sum(item["avoidableSpend"] for item in episodes), 2),
        "averagePriorityScore": round(sum(item["priorityScore"] for item in episodes) / len(episodes), 2) if episodes else 0,
        "averageConfidenceScore": round(sum(item["confidenceScore"] for item in episodes) / len(episodes), 2) if episodes else 0,
    }
    report["episodes"] = [{
        "episodeId": item["episodeId"],
        "memberReference": item["memberReference"],
        "claimCount": item["features"]["claimCount"],
        "repeatRisk90Day": item["repeatRisk"]["probabilities"]["90"],
        "denialProbability": item["denialRisk"]["probability"],
        "predictedPaid": item["forecast"]["paid"],
        "potentiallyAvoidableRepeatServiceSpend": item["avoidableSpend"],
        "priorityScore": item["priorityScore"],
        "confidence": item["confidence"],
        "peerCount": item["peerCount"],
    } for item in episodes]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output.resolve()), **report["validation"], **report["quality"], **report["summary"]}, indent=2))


if __name__ == "__main__":
    main()
