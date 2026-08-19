import copy
import json
import os
import re
import unittest
from pathlib import Path
from unittest.mock import patch


WORKBOOK = Path(
    os.getenv(
        "SAVINGS_WORKBOOK_PATH",
        "/Users/user/Downloads/EDI_834_837_20_members_ENRICHED (2).xlsx",
    )
)
os.environ["SAVINGS_WORKBOOK_PATH"] = str(WORKBOOK)
os.environ.setdefault("CLAIMS_WORKSHEET_NAME", "837_Claims")
os.environ.setdefault("ELIGIBILITY_WORKSHEET_NAME", "834_Eligibility")
os.environ.setdefault("REASON_LEGEND_WORKSHEET_NAME", "Reason_Code_Legend")
os.environ.setdefault("FIELD_DICTIONARY_WORKSHEET_NAME", "New_Fields_Dictionary")
os.environ.setdefault("DATA_NOTES_WORKSHEET_NAME", "Data_Notes_READ_ME")

from backend.app import app
from backend.financial_engine import build_financial_result
from backend.financial_engine import clear_financial_cache
from backend.prediction_validation import (
    PredictionConsistencyError,
    claim_backtest,
    validate_prediction_result,
)
from backend.workbook_enrichment import REQUIRED_SHEETS, load_workbook_database
from backend.workbook_enrichment import _notify_hash_change
from backend.workbook_llm import (
    _groq_exact_answer,
    _validate_model_numbers,
    generate_workbook_chat_answer,
    generate_workbook_llm_analysis,
)
from backend.workbook_rag import build_index, retrieve_evidence


@unittest.skipUnless(WORKBOOK.is_file(), "Integrated workbook attachment is required")
class IntegratedWorkbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database = load_workbook_database(WORKBOOK, force=True)
        cls.client = app.test_client()

    def test_only_integrated_sheets_are_required_and_all_claim_columns_are_preserved(self):
        self.assertEqual(
            REQUIRED_SHEETS,
            {
                "837_Claims",
                "834_Eligibility",
                "Reason_Code_Legend",
                "New_Fields_Dictionary",
                "Data_Notes_READ_ME",
            },
        )
        self.assertEqual(self.database.report["total_claim_count"], 2317)
        self.assertEqual(self.database.report["claim_column_count"], 145)
        self.assertEqual(len(self.database.selectable_claims), 1502)
        self.assertEqual(len(self.database.historical_claims), 815)
        self.assertIn("Authorization_Valid_From", self.database.selectable_claims[0]["workbookFields"])
        self.assertIn("Remit_835_Received_Date", self.database.selectable_claims[0]["workbookFields"])

    def test_historical_reference_rows_are_not_selectable_or_visible(self):
        historical = self.database.historical_claims[0]
        self.assertIsNone(self.database.find_claim(historical["claimId"], selectable_only=True))
        response = self.client.get(
            f"/api/members/{historical['memberId']}/claims"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(not item["isHistoricalReference"] for item in response.get_json()["items"]))

    def test_display_claim_id_alias_resolves_to_canonical_selectable_claim(self):
        claim = self.database.find_claim("CLM-000143", selectable_only=True)
        self.assertIsNotNone(claim)
        self.assertEqual(claim["claimId"], "CLM00000143")
        response = self.client.post("/api/predictions/provider-case/CLM-000143/llm")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["claim_id"], "CLM00000143")

    def test_clm_1092_financial_categories_and_best_action(self):
        result = build_financial_result(self.database, "CLM00001092")
        summary = result["supported_money_summary"]
        self.assertEqual(result["financial_opportunities"]["underpayment"]["amount"], 0.0)
        self.assertEqual(result["financial_opportunities"]["underpayment"]["status"], "supported_zero")
        self.assertEqual(result["financial_opportunities"]["patient_balance"]["amount"], 73.71)
        self.assertEqual(summary["recoverable_now"], 73.71)
        self.assertEqual(summary["potentially_avoidable_spend_supported"], 0.0)
        self.assertEqual(summary["best_action"]["type"], "patient_balance")
        avoidable = result["financial_opportunities"]["potentially_avoidable_episode_spend"]
        self.assertEqual(avoidable["reason_code"], "INSUFFICIENT_COMPARABLE_EPISODES")
        self.assertIn("Only 3 comparable episodes", avoidable["reason"])
        calculation = next(
            section["items"]
            for section in result["scenario_map"]["sections"]
            if section["title"] == "Financial Opportunity"
        )
        patient = next(item for item in calculation if item["type"] == "patient_balance")
        self.assertEqual(patient["formula"], "114.16 - 40.45 = 73.71")

    def test_clm_143_financial_categories_and_best_action(self):
        result = build_financial_result(self.database, "CLM00000143")
        summary = result["supported_money_summary"]
        self.assertEqual(result["actual_claim_facts"]["paid"], 549.53)
        self.assertEqual(result["financial_opportunities"]["underpayment"]["amount"], 16.42)
        self.assertEqual(summary["recoverable_now"], 16.42)
        self.assertEqual(summary["best_action"]["type"], "underpayment")
        self.assertEqual(result["financial_opportunities"]["patient_balance"]["amount"], 0.0)
        self.assertEqual(result["financial_opportunities"]["authorization"]["amount"], 0.0)
        self.assertEqual(result["financial_opportunities"]["referral"]["amount"], 0.0)

    def test_summary_scenario_llm_and_chat_use_identical_numbers(self):
        claim_id = "CLM00001092"
        canonical = build_financial_result(self.database, claim_id)
        analysis = generate_workbook_llm_analysis(self.database, claim_id)
        chat = generate_workbook_chat_answer(
            self.database, claim_id, canonical["episode_id"], "How much can be saved?", "c1"
        )
        expected = canonical["supported_money_summary"]
        scenario_money = next(
            section["items"]
            for section in canonical["scenario_map"]["sections"]
            if section["title"] == "Financial Prediction"
        )
        self.assertEqual(analysis["supported_money_summary"], expected)
        self.assertEqual(
            scenario_money["predicted_provider_payment"],
            canonical["financial_prediction_snapshot"]["predicted_provider_payment"]["value"],
        )
        self.assertEqual(
            chat["financial_explanation"]["recoverable_now"], expected["recoverable_now"]
        )
        self.assertEqual(
            chat["financial_explanation"]["potentially_avoidable_spend_supported"],
            expected["potentially_avoidable_spend_supported"],
        )
        explanation = analysis["prediction_explanation"]
        self.assertEqual(len(explanation["sections"]), 7)
        self.assertIn(
            "Predicted avoidable spend",
            [section["title"] for section in explanation["sections"]],
        )
        self.assertEqual(
            [section["title"] for section in explanation["sections"]],
            [
                "What this prediction means",
                "Money that can be acted on now",
                "How the amount was determined",
                "What to do next",
                "What the forecast risk means",
                "Predicted avoidable spend",
                "How confident the model is",
            ],
        )
        self.assertIn(
            f"${expected['recoverable_now']:,.2f}",
            explanation["sections"][1]["body"],
        )

    def test_repeated_chat_question_is_numeric_stable_across_conversations(self):
        result = build_financial_result(self.database, "CLM00000143")
        first = generate_workbook_chat_answer(
            self.database, result["claim_id"], result["episode_id"], "How much can be saved?", "first"
        )
        second = generate_workbook_chat_answer(
            self.database, result["claim_id"], result["episode_id"], "  HOW much can be saved ", "second"
        )
        self.assertEqual(first["answer"], second["answer"])
        self.assertEqual(first["financial_explanation"], second["financial_explanation"])
        self.assertEqual(second["conversation_id"], "second")

    @patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False)
    @patch("backend.workbook_llm.urlopen")
    def test_groq_cannot_change_backend_financial_text(self, mocked_urlopen):
        canonical = build_financial_result(self.database, "CLM00000143")
        rag = {"retrieved_documents": []}
        mocked_urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"content": json.dumps({"answer": "The amount is $999,999.00."})}}]
        }).encode()
        answer, source, _ = _groq_exact_answer("The amount is $16.42.", canonical, rag)
        self.assertEqual(answer, "The amount is $16.42.")
        self.assertEqual(source, "deterministic_backend")

    def test_model_numeric_grounding_rejects_invented_money(self):
        canonical = build_financial_result(self.database, "CLM00000143")
        _validate_model_numbers("The supported amount is $16.42.", canonical)
        with self.assertRaisesRegex(ValueError, "introduced numeric values"):
            _validate_model_numbers("The supported amount is $999,999.00.", canonical)

    def test_prediction_does_not_call_ollama_or_rag(self):
        clear_financial_cache()
        with patch(
            "backend.ollama_service.OllamaClient.embed",
            side_effect=AssertionError("prediction called Ollama"),
        ), patch(
            "backend.workbook_rag.retrieve_evidence",
            side_effect=AssertionError("prediction called RAG"),
        ):
            result = build_financial_result(self.database, "CLM00000143")
        self.assertTrue(result["consistency_check"]["passed"])

    def test_prediction_validation_rejects_formula_and_probability_drift(self):
        result = copy.deepcopy(
            build_financial_result(self.database, "CLM00000143")
        )
        backtest = claim_backtest(result)
        self.assertEqual(
            backtest["paid"]["actual"],
            result["actual_claim_facts"]["paid"],
        )
        self.assertIn("actual_inside_interval", backtest["paid"])
        result["financial_prediction_snapshot"]["denial_probability"] = 1.01
        result["financial_prediction_snapshot"][
            "predicted_denial_revenue_exposure"
        ] = -1
        with self.assertRaises(PredictionConsistencyError) as raised:
            validate_prediction_result(result)
        self.assertTrue(
            any("between zero and one" in item for item in raised.exception.details)
        )
        self.assertTrue(
            any("canonical formula" in item for item in raised.exception.details)
        )

    def test_rag_is_workbook_only_and_excludes_direct_phi(self):
        bundle = build_index(self.database)
        self.assertGreater(bundle["manifest"]["document_count"], len(self.database.claims))
        self.assertEqual(
            bundle["manifest"]["document_count"],
            bundle["manifest"]["vector_count"],
        )
        self.assertEqual(
            {
                document["metadata"]["source_sheet"]
                for document in bundle["documents"]
            },
            {"837_Claims", "Reason_Code_Legend", "New_Fields_Dictionary", "Data_Notes_READ_ME"},
        )
        forbidden_field_fragments = (
            "Patient_First_Name",
            "Patient_Last_Name",
            "Patient_DOB",
            "Subscriber_Name",
            "Patient_Account_Number",
            "Member_ID:",
        )
        for document in bundle["documents"]:
            self.assertFalse(any(fragment in document["text"] for fragment in forbidden_field_fragments))
            self.assertIsNone(re.search(r"\b(?:MBR|PATMBR)\d+\b", document["text"], re.IGNORECASE))

    def test_rag_filters_claim_episode_and_cutoff(self):
        canonical = build_financial_result(self.database, "CLM00001092")
        rag = retrieve_evidence(self.database, canonical, "patient balance")
        selected = self.database.find_claim(canonical["claim_id"])
        by_id = {
            document["metadata"]["document_id"]: document
            for document in build_index(self.database)["documents"]
        }
        for chunk in rag["retrieved_chunks"]:
            document = by_id[chunk["document_id"]]
            metadata = document["metadata"]
            if metadata["source_sheet"] != "837_Claims" or metadata["claim_id"] == selected["claimId"]:
                continue
            self.assertLess(metadata["service_date"], selected["dos"])
            self.assertTrue(
                metadata["member_id"] == selected["memberId"]
                or metadata["episode_id"] == selected["episodeId"]
                or metadata["is_historical_reference"]
            )

    def test_clm_143_retrieval_contains_underpayment_evidence_fields(self):
        canonical = build_financial_result(self.database, "CLM00000143")
        rag = retrieve_evidence(
            self.database,
            canonical,
            "Why is the supported underpayment recoverable?",
        )
        exact_claim_fields = {
            field
            for document in rag["retrieved_documents"]
            if document["claim_id"] == canonical["claim_id"]
            for field in document["fields_used"]
        }
        self.assertTrue(
            {
                "Expected_Reimbursement",
                "Paid_Amount",
                "Recovered_Amount",
                "Underpayment_Amount",
                "Underpayment_Flag",
                "Payment_Tolerance",
            }.issubset(exact_claim_fields)
        )

    def test_workbook_hash_change_clears_financial_rag_llm_and_chat_caches(self):
        from backend import financial_engine, workbook_llm, workbook_rag

        financial_engine._RESULT_CACHE["test"] = {}
        workbook_llm._ANALYSIS_CACHE["test"] = {}
        workbook_llm._CHAT_CACHE["test"] = {}
        workbook_rag._CACHE["test"] = {}
        _notify_hash_change("old-workbook-hash", "new-workbook-hash")
        self.assertFalse(financial_engine._RESULT_CACHE)
        self.assertFalse(workbook_llm._ANALYSIS_CACHE)
        self.assertFalse(workbook_llm._CHAT_CACHE)
        self.assertFalse(workbook_rag._CACHE)

    def test_all_selectable_claims_can_build_a_canonical_result(self):
        for claim in self.database.selectable_claims:
            result = build_financial_result(self.database, claim["claimId"])
            self.assertEqual(result["claim_id"], claim["claimId"])
            self.assertIn("recoverable_now", result["supported_money_summary"])
            self.assertTrue(result["consistency_check"]["passed"])
            self.assertTrue(all(
                item["status"] == "supported" and item["amount"] > 0
                for item in result["supported_financial_opportunities"]
            ))
            self.assertTrue(all(
                item["status"] != "supported" or item["amount"] <= 0
                for item in result["non_actionable_evidence"]
            ))
            snapshot = result["financial_prediction_snapshot"]
            self.assertEqual(
                snapshot["predicted_denial_revenue_exposure"],
                round(snapshot["denial_probability"] * snapshot["predicted_provider_payment"]["value"], 2),
            )
            self.assertEqual(
                snapshot["predicted_repeat_payment_exposure"],
                round(snapshot["repeat_probability_90d"] * snapshot["predicted_provider_payment"]["value"], 2),
            )

    def test_every_selectable_member_has_a_dynamic_summary(self):
        for member in self.database.members:
            response = self.client.get(f"/api/members/{member['memberId']}")
            self.assertEqual(response.status_code, 200, member["memberId"])
            payload = response.get_json()
            self.assertEqual(payload["item"]["memberId"], member["memberId"])
            self.assertEqual(
                payload["item"]["supportedMoneySummary"]["claim_count"],
                len(self.database.member_claims(member["memberId"])),
            )

    def test_production_prediction_code_has_no_member_or_claim_constants(self):
        root = Path(__file__).parents[2]
        production = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                root / "backend" / "financial_engine.py",
                root / "backend" / "workbook_llm.py",
                root / "frontend" / "src" / "App.jsx",
            )
        )
        self.assertNotRegex(production, r"\bCLM\d{4,}\b|\bMBR\d{3,}\b")

    def test_affected_endpoints_report_one_workbook_version(self):
        endpoints = [
            "/api/claims",
            "/api/members",
            "/api/predictions/scenarios",
            "/api/predictions/provider-case/CLM00000143",
        ]
        hashes = set()
        for endpoint in endpoints:
            response = self.client.get(endpoint)
            self.assertEqual(response.status_code, 200, endpoint)
            payload = response.get_json()
            source = payload.get("source") or payload.get("meta", {}).get("source")
            self.assertIsNotNone(source, endpoint)
            hashes.add(source["workbook_hash"])
        self.assertEqual(hashes, {self.database.workbook_hash})

    def test_frontend_contains_no_legacy_labels_or_empty_state_phrases(self):
        frontend_root = Path(__file__).parents[2] / "frontend" / "src"
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in frontend_root.rglob("*")
            if path.suffix in {".js", ".jsx", ".css"}
        )
        forbidden = [
            "Not " + "identified",
            "None " + "identified",
            "Not " + "calculated",
            "Not " + "supported",
            "Un" + "available",
            "Un" + "known",
            "Range " + "unavailable",
            "Claims" + "_Original",
            "Dummy" + "_Enrichment",
        ]
        for phrase in forbidden:
            self.assertNotIn(phrase, text)

    def test_configured_workbook_failure_is_explicit(self):
        with self.assertRaisesRegex(FileNotFoundError, "Configured workbook cannot be loaded"):
            load_workbook_database("/tmp/payer-payee-workbook-does-not-exist.xlsx", force=True)


if __name__ == "__main__":
    unittest.main()
