from analytics import ClaimsAnalytics, build_lower_spend_benchmark, build_utilisation_benchmark


class FakeCollection:
    def __init__(self, rows):
        self.rows = list(rows)

    def find(self, *_args, **_kwargs):
        return list(self.rows)


class FakeDatabase:
    def __init__(self, rows):
        self.collection = FakeCollection(rows)

    def __getitem__(self, _name):
        return self.collection


def claim(
    claim_id,
    member,
    service_date,
    *,
    family="F1",
    diagnosis=None,
    payer="P1",
    provider="N1",
    pos="11",
    cpt="X1",
    units=1,
    paid=100,
    historical="N",
):
    return {
        "Claim_ID": claim_id,
        "Member_ID": member,
        "Service_Date_From": service_date,
        "ICD10_Diagnosis_Code": diagnosis or f"{family}.1",
        "ICD10_Family": family,
        "Payer_ID": payer,
        "Payer_Name": payer,
        "Billing_Provider_NPI": provider,
        "Billing_Provider_Name": provider,
        "Place_of_Service_Code": pos,
        "CPT_Code": cpt,
        "Units": units,
        "Paid_Amount": paid,
        "Is_Historical_Reference_Record": historical,
    }


def engine(rows):
    return ClaimsAnalytics(FakeDatabase(rows))


def test_regression_claim_uses_scenario_3_and_positive_spend_reduction():
    rows = [
        claim("CLM00001092", "MBR00015", 20260529, family="Z87", diagnosis="Z87.01", payer="61101", provider="4163119785", pos=81, cpt="90732", paid=295.77),
        claim("CLM00000135", "MBR00002", 20231027, family="Z87", diagnosis="Z87.39", payer="108", provider="9963334018", pos=41, cpt="85025", units=2, paid=113.82),
        claim("CLM00000108", "MBR00002", 20240501, family="Z87", diagnosis="Z87.39", payer="108", provider="9963334018", pos=41, cpt="85025", units=2, paid=587.23),
        claim("CLM00000137", "MBR00002", 20240924, family="Z87", diagnosis="Z87.39", payer="108", provider="9963334018", pos=41, cpt="85025", units=1, paid=158.87),
        claim("CLM00000140", "MBR00002", 20260220, family="Z87", diagnosis="Z87.39", payer="108", provider="9963334018", pos=41, cpt="85025", units=3, paid=647.39),
    ]
    result = engine(rows).payer_savings_prediction("CLM00001092")
    assert result["target"]["member_id"] == "MBR00015"
    assert result["target"]["diagnosis_family"] == "Z87"
    assert result["scenario_selection"]["scenario_1"]["available"] is False
    assert result["scenario_selection"]["scenario_2"]["available"] is False
    assert result["scenario_selection"]["selected"]["number"] == 3
    assert result["peer_summary"] == {"member_count": 1, "episode_count": 4, "claim_count": 4}
    assert result["prediction"]["utilisation_reduction_opportunity"] == 0.0
    assert result["lower_spend_benchmark"]["value"] == 136.35
    assert result["prediction"]["payer_spend_reduction_opportunity"] == 159.42
    assert result["prediction"]["episode_predicted_payer_avoidable_spend"] == 159.42
    assert result["prediction"]["selected_claim"]["attributed_payer_avoidable_spend"] == 159.42
    assert {row["Member_ID"] for row in result["supporting_evidence"] if row["Evidence_Role"] != "Historical Reference"} == {"MBR00015", "MBR00002"}


def test_target_episode_is_bounded_and_historical_rows_do_not_increase_totals():
    rows = [
        claim("T1", "M1", 20260101, family="D1", paid=50),
        claim("T2", "M1", 20260120, family="D1", paid=75),
        claim("OLD", "M1", 20260405, family="D1", paid=900),
        claim("HIST", "M1", 20260115, family="D1", paid=900, historical="Y"),
    ]
    result = engine(rows).payer_savings_prediction("T2")
    assert result["target"]["claim_ids"] == ["T1", "T2"]
    assert result["target"]["claim_count"] == 2
    assert result["target"]["total_paid"] == 125.0
    assert result["target"]["episode_duration_days"] <= 90
    assert all(row["Claim_ID"] != "HIST" or row["Evidence_Role"] == "Historical Reference" for row in result["supporting_evidence"])


def test_strict_match_has_priority_and_excludes_other_scenarios():
    rows = [
        claim("T", "M1", 20260101, family="D1", payer="P1", provider="N1", pos="11", cpt="X", units=2, paid=200),
        claim("S", "M2", 20260102, family="D1", payer="P1", provider="N1", pos="11", cpt="X", units=3, paid=100),
        claim("S2", "M3", 20260103, family="D1", payer="P1", provider="N9", pos="22", cpt="Y", units=9, paid=90),
        claim("S3", "M4", 20260104, family="D1", payer="P9", provider="N9", pos="22", cpt="Y", units=9, paid=80),
    ]
    result = engine(rows).payer_savings_prediction("T")
    assert result["scenario_selection"]["selected"]["number"] == 1
    assert {item["member_id"] for item in result["peer_members_used"]} == {"M2"}


def test_scenario_2_requires_payer_but_allows_other_identity_fields():
    rows = [
        claim("T", "M1", 20260101, family="D1", payer="P1", provider="N1", pos="11", cpt="X", paid=200),
        claim("P", "M2", 20260102, family="D1", payer="P1", provider="N9", pos="22", cpt="Y", units=5, paid=80),
    ]
    result = engine(rows).payer_savings_prediction("T")
    assert result["scenario_selection"]["scenario_1"]["available"] is False
    assert result["scenario_selection"]["selected"]["number"] == 2


def test_same_member_rows_are_never_external_peers():
    rows = [
        claim("T", "M1", 20260101, family="D1", payer="P1", paid=200),
        claim("SAME", "M1", 20260102, family="D1", payer="P9", paid=10),
        claim("OTHER", "M2", 20260103, family="D1", payer="P9", paid=100),
    ]
    result = engine(rows).payer_savings_prediction("T")
    assert all(item["member_id"] != "M1" for item in result["peer_members_used"])


def test_lower_spend_benchmark_can_be_positive_when_claim_count_is_equal():
    peers = [
        {"episode_id": "p1", "member_id": "M2", "claim_count": 1, "total_paid": 113.82, "rows": [{"Paid_Amount": 113.82}], "claim_ids": ["p1"]},
        {"episode_id": "p2", "member_id": "M3", "claim_count": 1, "total_paid": 158.87, "rows": [{"Paid_Amount": 158.87}], "claim_ids": ["p2"]},
        {"episode_id": "p3", "member_id": "M4", "claim_count": 1, "total_paid": 587.23, "rows": [{"Paid_Amount": 587.23}], "claim_ids": ["p3"]},
        {"episode_id": "p4", "member_id": "M5", "claim_count": 1, "total_paid": 647.39, "rows": [{"Paid_Amount": 647.39}], "claim_ids": ["p4"]},
    ]
    util = build_utilisation_benchmark(peers, 1)
    lower = build_lower_spend_benchmark(peers, 295.77)
    assert util["excess_claim_count"] == 0
    assert lower["value"] == 136.35


def test_multi_claim_episode_saving_is_attributed_by_paid_share():
    rows = [
        claim("T1", "M1", 20260101, family="D1", payer="P1", paid=100),
        claim("T2", "M1", 20260110, family="D1", payer="P1", paid=300),
        claim("P", "M2", 20260103, family="D1", payer="P1", paid=100),
    ]
    result = engine(rows).payer_savings_prediction("T1")
    # Target total is 400; the one peer is lower by 300.  T1 owns 25% of it.
    assert result["target"]["claim_count"] == 2
    assert result["prediction"]["episode_predicted_payer_avoidable_spend"] == 300.0
    assert result["prediction"]["claim_attributed_payer_avoidable_spend"] == 75.0
    assert result["prediction"]["selected_claim"]["episode_spend_share"] == 0.25


def test_no_external_peer_returns_zero_without_fabricating_a_scenario():
    result = engine([claim("T", "M1", 20260101, family="D1", paid=100)]).payer_savings_prediction("T")
    assert result["scenario_selection"]["selected"]["number"] == 0
    assert result["prediction"]["episode_predicted_payer_avoidable_spend"] == 0.0
    assert result["confidence"]["level"] == "Low"


def test_different_member_historical_row_can_support_a_peer_but_not_target_totals():
    rows = [
        claim("T", "M1", 20260101, family="D1", payer="P1", paid=200),
        claim("HPEER", "M2", 20260102, family="D1", payer="P1", paid=100, historical="Y"),
    ]
    result = engine(rows).payer_savings_prediction("T")
    assert result["scenario_selection"]["selected"]["number"] == 1
    assert result["target"]["total_paid"] == 200.0
    assert result["peer_summary"]["claim_count"] == 1
    assert "HPEER" in {row["Claim_ID"] for row in result["supporting_evidence"]}


def test_payer_savings_endpoint_returns_the_rule_result():
    import app as prediction_app

    rows = [
        claim("T", "M1", 20260101, family="D1", payer="P1", paid=200),
        claim("P", "M2", 20260102, family="D1", payer="P1", paid=100),
    ]
    previous_engine = prediction_app.engine
    prediction_app.engine = engine(rows)
    try:
        with prediction_app.app.test_client() as client:
            response = client.get("/api/claims/T/payer-savings")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["analysis_type"] == "payer_savings_prediction"
        assert payload["calculation_trace"]["target_claim_id"] == "T"
    finally:
        prediction_app.engine = previous_engine
