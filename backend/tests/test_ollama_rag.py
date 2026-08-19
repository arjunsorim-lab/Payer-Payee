import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from backend.ollama_service import OllamaClient, OllamaError
from backend import workbook_llm, workbook_rag


class OllamaClientTests(unittest.TestCase):
    def test_health_and_model_detection(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "models": [
                {"name": "gemma3:latest"},
                {"name": "embeddinggemma:latest"},
            ]
        }
        with patch("backend.ollama_service.requests.request", return_value=response):
            health = OllamaClient().health()
        self.assertTrue(health["available"])
        self.assertTrue(health["chat_model_available"])
        self.assertTrue(health["embedding_model_available"])

    def test_timeout_is_reported(self):
        with patch(
            "backend.ollama_service.requests.request",
            side_effect=requests.Timeout(),
        ):
            with self.assertRaisesRegex(OllamaError, "timed out"):
                OllamaClient(timeout_seconds=1).list_models()

    def test_invalid_json_is_reported(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.side_effect = ValueError("bad json")
        with patch("backend.ollama_service.requests.request", return_value=response):
            with self.assertRaisesRegex(OllamaError, "invalid JSON"):
                OllamaClient().list_models()

    def test_embedding_count_and_dimensions_are_checked(self):
        client = OllamaClient()
        client.require_model = Mock()
        client._request = Mock(return_value={"embeddings": [[1, 0], [0, 1]]})
        self.assertEqual(client.embed(["one", "two"]), [[1.0, 0.0], [0.0, 1.0]])
        client._request = Mock(return_value={"embeddings": [[1, 0]]})
        with self.assertRaisesRegex(OllamaError, "count"):
            client.embed(["one", "two"])

    def test_unreachable_ollama_uses_deterministic_fallback(self):
        result = {
            "rag": {"retrieved_chunks": []},
            "actual_claim_facts": {},
            "financial_prediction_snapshot": {},
            "supported_money_summary": {},
            "supported_financial_opportunities": [],
            "historical_prediction_basis": {},
        }
        with patch(
            "backend.workbook_llm.retrieve_evidence",
            side_effect=OllamaError("unreachable"),
        ):
            rag = workbook_llm._retrieval_or_fallback(
                object(), result, "prediction"
            )
        self.assertEqual(rag["retrieved_chunks"], [])
        self.assertEqual(
            rag["retrieval_status"],
            "local_model_temporarily_unavailable",
        )
        with patch.object(
            OllamaClient,
            "chat",
            side_effect=OllamaError("unreachable"),
        ):
            answer, source, _, explanation = (
                workbook_llm._ollama_explanation(
                    "Deterministic answer.", result, rag
                )
            )
        self.assertEqual(answer, "Deterministic answer.")
        self.assertEqual(source, "deterministic_backend")
        self.assertNotIn(
            "unreachable",
            " ".join(explanation["limitations"]).lower(),
        )


class FakeOllama:
    def __init__(self, model="fake-embed"):
        self.embed_model = model
        self.chat_model = "fake-chat"

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append([
                1.0 if "underpayment" in lowered else 0.1,
                1.0 if "patient balance" in lowered else 0.1,
                1.0 if "selected" in lowered else 0.1,
                1.0,
            ])
        return vectors


class TinyDatabase:
    workbook_hash = "tiny-workbook-hash"
    reason_legend_rows = ({"Reason_Code": "R1", "Meaning": "underpayment", "_source_row": 2},)
    field_dictionary_rows = ({"Field": "Paid_Amount", "Meaning": "paid", "_source_row": 2},)
    data_notes_rows = ({"Note": "workbook only", "_source_row": 2},)

    def __init__(self):
        self.claims = (
            self._claim("C1", "M1", "E1", "2026-05-10", False, 2),
            self._claim("C0", "M1", "E1", "2026-04-10", False, 3),
            self._claim("H1", "HM1", "EH1", "2026-03-10", True, 4),
            self._claim("H2", "HM2", "EH2", "2026-06-10", True, 5),
        )
        self.selectable_claims = self.claims[:2]
        self.historical_claims = self.claims[2:]

    @staticmethod
    def _claim(claim_id, member_id, episode_id, service_date, historical, row):
        fields = {
            "Service_Date_From": service_date.replace("-", ""),
            "Payer_ID": "P1",
            "Payer_Name": "Payer",
            "CPT_Code": "99214",
            "CPT_Description": "Visit",
            "ICD10_Family": "I10",
            "Place_of_Service_Code": "11",
            "Units": 1,
            "Claim_Status_Description": "Processed",
            "Charge_Amount": 100,
            "Allowed_Amount": 70,
            "Paid_Amount": 55,
            "Patient_Responsibility": 15,
            "Adjustment_Amount": 30,
            "Expected_Reimbursement": 60,
            "Underpayment_Flag": "Y",
            "Underpayment_Amount": 5,
            "Payment_Tolerance": 2,
            "Reason_Code": "R1",
            "Episode_ID": episode_id,
            "Patient_First_Name": "Private",
            "Member_ID": member_id,
        }
        return {
            "claimId": claim_id,
            "memberId": member_id,
            "episodeId": episode_id,
            "dos": service_date,
            "payerId": "P1",
            "billingProviderNpi": "N1",
            "cptCode": "99214",
            "diagnosisCode": "I10",
            "placeOfServiceCode": "11",
            "units": 1,
            "workbookSourceRow": row,
            "isHistoricalReference": historical,
            "workbookFields": fields,
        }

    def find_claim(self, claim_id, selectable_only=True):
        source = self.selectable_claims if selectable_only else self.claims
        return next((claim for claim in source if claim["claimId"] == claim_id), None)


class WorkbookRagTests(unittest.TestCase):
    def setUp(self):
        self.database = TinyDatabase()
        self.temporary = tempfile.TemporaryDirectory()
        self.root_patch = patch.object(
            workbook_rag, "INDEX_ROOT", Path(self.temporary.name)
        )
        self.root_patch.start()
        workbook_rag.clear_rag_cache()

    def tearDown(self):
        workbook_rag.clear_rag_cache()
        self.root_patch.stop()
        self.temporary.cleanup()

    def test_documents_are_semantic_and_exclude_phi(self):
        documents = workbook_rag.create_documents(self.database)
        types = {item["metadata"]["document_type"] for item in documents}
        self.assertIn("claim_financial", types)
        self.assertIn("contract_payment", types)
        self.assertIn("patient_balance", types)
        text = " ".join(item["text"] for item in documents)
        self.assertNotIn("Private", text)
        self.assertNotIn("M1", text)
        self.assertTrue(all("document_id" in item["metadata"] for item in documents))

    def test_index_count_manifest_and_rebuild_identity(self):
        first = workbook_rag.build_index(
            self.database, force=True, client=FakeOllama("embed-a")
        )
        self.assertEqual(first["index"].ntotal, len(first["documents"]))
        self.assertEqual(
            first["manifest"]["vector_count"],
            first["manifest"]["document_count"],
        )
        workbook_rag.clear_rag_cache()
        second = workbook_rag.build_index(
            self.database, client=FakeOllama("embed-b")
        )
        self.assertEqual(second["manifest"]["embedding_model"], "embed-b")
        changed_workbook = TinyDatabase()
        changed_workbook.workbook_hash = "changed-workbook-hash"
        rebuilt = workbook_rag.build_index(
            changed_workbook, client=FakeOllama("embed-b")
        )
        self.assertNotEqual(second["path"], rebuilt["path"])
        self.assertEqual(
            rebuilt["manifest"]["workbook_hash"],
            "changed-workbook-hash",
        )

    def test_hybrid_retrieval_respects_cutoff(self):
        client = FakeOllama()
        workbook_rag.build_index(self.database, force=True, client=client)
        result = {
            "claim_id": "C1",
            "financial_opportunities": {
                "underpayment": {"reason_code": "R1"}
            },
        }
        retrieved = workbook_rag.retrieve_claim_evidence(
            self.database,
            "C1",
            "underpayment",
            top_k=20,
            financial_result=result,
            client=client,
        )
        claim_ids = {
            item["claim_id"] for item in retrieved["retrieved_documents"]
        }
        self.assertNotIn("H2", claim_ids)
        self.assertIn("C1", claim_ids)
        self.assertTrue(all(
            abs(
                item["final_score"]
                - (
                workbook_rag.VECTOR_WEIGHT * item["vector_similarity"]
                + workbook_rag.STRUCTURED_WEIGHT
                * item["structured_match_score"]
                )
            ) < 0.000002
            for item in retrieved["retrieved_documents"]
        ))


if __name__ == "__main__":
    unittest.main()
