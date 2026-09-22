"""Exercise persisted review decisions, evidence gating and CSV exports."""
import csv
import io
from copy import deepcopy
from datetime import date

import pytest

from backend.review_store import save_review, read_review_history, read_reviews
from backend.savings_validation import build_savings_validation
from backend.claim_export import claims_csv, recommendations_csv

PLAN = {"available": True, "anchor_service_date": "2026-01-01", "source_data_type": "synthetic_demonstration"}


def test_every_review_action_persists_and_audits(tmp_path):
    db = tmp_path / "reviews.sqlite3"
    version = 0
    for status in ['assigned', 'deferred', 'rejected', 'accepted', 'completed', 'outcome_recorded']:
        review = save_review(db, 'review', dict(version=version, status=status,
            reason=f'Test evidence supports {status}', assignee='test-team', due_date='2026-12-01',
            completed_date='2026-02-01', outcome='improved', outcome_date='2026-03-01',
            outcome_notes='Synthetic follow-up assessment, reference TEST-OUTCOME'), 'test-reviewer', PLAN)
        version += 1
        assert review['version'] == version
        assert read_reviews(db, ['review'])['review']['status'] == status
        assert review['verified_savings'] is None
        assert review['evidence_type'] == 'reviewer_observation'
    history = read_review_history(db, 'review')
    assert [row['detail']['after']['status'] for row in history] == ['assigned', 'deferred', 'rejected', 'accepted', 'completed', 'outcome_recorded']
    with pytest.raises(RuntimeError):
        save_review(db, 'review', dict(version=0, status='accepted', reason='Stale overwrite', assignee='team'), 'other', PLAN)
    with pytest.raises(ValueError):
        save_review(db, 'review', dict(version=version, status='accepted', reason='Invalid reopening', assignee='team'), 'other', PLAN)
    assert len(read_review_history(db, 'review')) == 6


def test_completion_requires_evidence_and_insufficient_plan_can_be_deferred(tmp_path):
    db = tmp_path / 'review.sqlite3'
    save_review(db, 'a', dict(version=0, status='accepted', reason='Reviewed evidence', assignee='team'), 'reviewer', PLAN)
    with pytest.raises(ValueError, match='evidence'):
        save_review(db, 'a', dict(version=1, status='completed', reason='Done', completed_date='2026-02-01'), 'reviewer', PLAN)
    incomplete = {**PLAN, 'available': False}
    with pytest.raises(ValueError):
        save_review(db, 'b', dict(version=0, status='accepted', reason='No evidence', assignee='team'), 'reviewer', incomplete)
    result = save_review(db, 'b', dict(version=0, status='deferred', reason='Collect missing labs', assignee='team', due_date='2026-12-01'), 'reviewer', incomplete)
    assert result['status'] == 'deferred'


def test_cohort_difference_and_reviewer_note_never_become_verified_savings():
    result = build_savings_validation(
        intervention_date='2025-01-01', today=date(2026, 1, 1),
        cohort_paid_values=[200, 250, 210, 240, 230],
        matching_dimensions={'same_condition': True},
        expected_follow_up_contacts=1, observed_follow_up_contacts=1,
        verification_claims=[{'claim_id': 'POST', 'dos': '2025-02-01', 'paid': 50, 'related': True}],
        review={'status': 'outcome_recorded', 'outcome': 'improved', 'outcome_notes': 'Observed'},
    )
    assert result['verification_status'] == 'unverified'
    assert result['amounts']['verified_savings'].get('value') is None
    assert result['causal_evidence_status'] == 'not_established'
    assert result['observed_cohort_difference']['value'] == 176


@pytest.mark.parametrize('claim', [
    {'dos':'2025-02-01','paid':None,'related':True},
    {'dos':'2025-12-01','paid':50,'related':True},
    {'dos':'2024-12-01','paid':50,'related':True},
    {'dos':'2025-02-01','paid':50,'related':False},
    {'dos':'2025-02-01','paid':50,'related':True,'synthetic':True},
])
def test_unsupported_claims_are_excluded_from_verification(claim):
    result = build_savings_validation(intervention_date='2025-01-01', verification_claims=[claim])
    assert result['post_intervention_evidence']['claim_count'] == 0
    assert result['verification_status'] == 'insufficient_evidence'


def test_exports_preserve_provenance_and_neutralize_formulas():
    row = {'claimId': 'TEST', 'memberId': '=1+1', 'paid': 30, 'synthetic': True}
    before = deepcopy(row)
    exported = list(csv.DictReader(io.StringIO(claims_csv([row]))))[0]
    assert row == before
    assert exported['evidence_type'] == 'synthetic_demonstration'
    assert exported['member_id'] == "'=1+1"
    proposal = list(csv.DictReader(io.StringIO(recommendations_csv([{'action': 'Review evidence'}]))))[0]
    assert proposal['evidence_type'] == 'recommendation'
    assert proposal['verified_savings'] == ''


def test_missing_estimates_are_not_zero_dollars():
    validation = build_savings_validation(anchor_service_date='2026-01-01',
        predicted_opportunity=None, estimated_savings=None)
    assert validation['amounts']['predicted_opportunity'].get('value') is None
    assert validation['amounts']['estimated_savings'].get('value') is None


def test_synthetic_history_cannot_supply_recorded_care_context():
    from backend.intervention_plans import clinical_inputs
    anchor = {'claimId': 'R', 'dos': '2026-01-03', 'diagnosisCode': 'N39.0', 'workbookFields': {}}
    demo = {'claimId': 'D', 'dos': '2026-01-01', 'diagnosisCode': 'N39.0',
            'cptCode': '87086', 'cptDescription': 'Urine culture',
            'workbookFields': {'Reason_Code': 'SYNTHETIC_SEQUENCE_PREVENTIVE'}}
    context = clinical_inputs([demo, anchor], anchor)
    assert context['history']['history_claim_count'] == 0
    assert not any(item.get('claim_id') == 'D' for item in context['evidence_used'])


def test_historical_reference_excludes_future_and_synthetic_paid_amounts():
    from backend.intervention_plans import _cohort_paid_values
    anchor = {'claimId': 'A', 'dos': '2026-02-01', 'diagnosisCode': 'N39.0'}
    recorded = {'claimId': 'R', 'dos': '2026-01-01', 'diagnosisCode': 'N39.0', 'paid': 30}
    future = {**recorded, 'claimId': 'F', 'dos': '2026-03-01', 'paid': 900}
    demo = {**recorded, 'claimId': 'D', 'paid': 800, 'workbookFields': {'Synthetic_Flag': 'Y'}}
    assert _cohort_paid_values([recorded, future, demo, anchor], anchor) == [30]


def test_synthetic_amounts_never_receive_recorded_care_labels():
    result = build_savings_validation(anchor_service_date='2026-01-01',
        billed_charge=100, paid_amount=70, source_data_type='synthetic_demonstration')
    for key in ('billed_charge', 'paid_amount'):
        assert result['amounts'][key]['evidence_type'] == 'synthetic_demonstration'
