"""Solvability + difficulty proof for the upgraded pp_file_claim_appeal task.

The task was escalated from `hard` (single denied-claim appeal by earliest
deadline) to `expert`: the agent must now identify EVERY appealable denied
claim (denied + EOB available + appeal_deadline >= now), rank them by appeal
deadline (earliest first, claim-ID tie-break), and appeal EXACTLY the two
most-urgent — while leaving the OTHER appealable denied claims (the most
tempting siblings, including one with a far larger patient responsibility)
untouched, along with every approved/processing claim and all other records.

This module proves:
  * the intended answer (`top_2_urgent_appealable_claim_ids`) is achievable by
    driving the REAL backend `/claims/{id}/appeal` endpoint past its gates
    (status==denied, eob_available, deadline>=now), and scores >= 0.99;
  * a near-miss (appealing by patient-responsibility rank instead of deadline,
    i.e. the pp_claim_audit heuristic) FAILS — confirming the new
    deadline-ranking discriminator genuinely bites.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_file_claim_appeal"
API = "/api/env/patient_portal"


def _new_session() -> tuple[SessionManager, str, dict]:
    """Create a session on the app's shared SessionManager so TestClient and
    the test observe the same state."""
    sm: SessionManager = app.state.session_manager
    sid, targets, _ = sm.create_session(env_id="patient_portal", task_id=TASK_ID, seed=42)
    return sm, sid, dict(targets)


def _appeal_via_backend(client: TestClient, sid: str, clm_id: str):
    return client.post(
        f"{API}/claims/{clm_id}/appeal",
        json={"session_id": sid, "reason": "Appeal supported by attached EOB and records."},
    )


def test_targets_are_well_formed_and_discriminating():
    """The urgency-ranked answer must be a strict 2-element subset of the
    appealable denied claims, and must DIFFER from a responsibility-based pick
    (otherwise the deadline discriminator would be vacuous)."""
    sm, sid, targets = _new_session()
    state = sm.get_state(sid)

    top2 = targets["top_2_urgent_appealable_claim_ids"]
    by_deadline = targets["appealable_claim_ids_by_deadline"]
    denied = targets["denied_claim_ids"]

    assert len(top2) == 2, top2
    assert top2 == by_deadline[:2]
    # There must be appealable denied claims LEFT OVER (frozen siblings) so the
    # "appeal exactly two" constraint is non-trivial.
    assert len(by_deadline) >= 4, by_deadline
    assert set(top2).issubset(set(denied))

    claims = {c.id: c for c in state.claims}
    # Every appealable claim is genuinely appealable through the backend gate.
    for cid in by_deadline:
        c = claims[cid]
        assert c.status == "denied" and c.eob_available

    # The two urgent claims have the earliest deadlines among appealable claims.
    deadlines = [(cid, claims[cid].appeal_deadline) for cid in by_deadline]
    assert deadlines[:2] == sorted(deadlines, key=lambda kv: (kv[1], kv[0]))[:2]

    # Discriminator check: a responsibility-ranked top-2 (the pp_claim_audit
    # heuristic) selects a DIFFERENT set, so an agent cannot pass by sorting on
    # the wrong field.
    by_resp = sorted(
        by_deadline,
        key=lambda cid: (-float(claims[cid].patient_responsibility), cid),
    )[:2]
    assert set(by_resp) != set(top2), (
        "task is not discriminating: deadline-rank and responsibility-rank coincide"
    )


def test_correct_trajectory_via_backend_passes():
    """Appeal exactly the two most-urgent eligible claims through the real
    endpoint; evaluate() must pass with score >= 0.99."""
    sm, sid, targets = _new_session()
    client = TestClient(app)

    for clm_id in targets["top_2_urgent_appealable_claim_ids"]:
        resp = _appeal_via_backend(client, sid, clm_id)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "appealed"

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"result: {result}"
    # Richer than the legacy 2-check eval (bijection + many invariants).
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_backend_rejects_appealing_non_appealable_claim():
    """The backend gate itself enforces the eligibility filter: an approved
    claim cannot be appealed (proves the gate the agent must respect)."""
    sm, sid, targets = _new_session()
    client = TestClient(app)
    approved_ids = targets["approved_claim_ids"]
    assert approved_ids
    resp = _appeal_via_backend(client, sid, approved_ids[0])
    assert resp.status_code == 422, resp.text


def test_wrong_responsibility_rank_trajectory_fails():
    """Near-miss: appeal the two highest patient-responsibility eligible claims
    (the pp_claim_audit heuristic) instead of the two most-urgent. This both
    misses a required urgent claim AND touches a frozen sibling, so it must
    FAIL (critical invariant + bijection)."""
    sm, sid, targets = _new_session()
    state = sm.get_state(sid)
    client = TestClient(app)

    claims = {c.id: c for c in state.claims}
    by_resp = sorted(
        targets["appealable_claim_ids_by_deadline"],
        key=lambda cid: (-float(claims[cid].patient_responsibility), cid),
    )[:2]
    assert set(by_resp) != set(targets["top_2_urgent_appealable_claim_ids"])

    for clm_id in by_resp:
        resp = _appeal_via_backend(client, sid, clm_id)
        assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"near-miss should fail: {result}"


def test_extra_appeal_trajectory_fails():
    """Over-acting: appeal the two urgent claims PLUS a third eligible claim.
    The exact-cardinality bijection + frozen-sibling critical invariant must
    fail this."""
    sm, sid, targets = _new_session()
    client = TestClient(app)

    to_appeal = list(targets["top_2_urgent_appealable_claim_ids"])
    # Add the next eligible-by-deadline claim that is NOT in the urgent set.
    extra = [
        cid for cid in targets["appealable_claim_ids_by_deadline"]
        if cid not in to_appeal
    ][0]
    to_appeal.append(extra)

    for clm_id in to_appeal:
        resp = _appeal_via_backend(client, sid, clm_id)
        assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"over-acting should fail: {result}"
