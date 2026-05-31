"""Solvability + integrity proof for the hardened pp_complex_claim_dispute task.

The upgraded task requires the agent to RE-DERIVE the genuinely-appealable
denied-claim subset (status=='denied' AND eob_available AND appeal_deadline
not passed) and appeal exactly that subset via the real backend appeal action,
while leaving the non-appealable denied decoys (no-EOB / past-deadline), the
approved claims, and the processing claims untouched.

Correct trajectory is driven through the REAL backend endpoint
(POST /api/env/patient_portal/claims/{id}/appeal) via starlette TestClient so
this also confirms the intended answer set passes every backend appeal gate.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_complex_claim_dispute"


def _make_session():
    """Create a real session on the app's session manager and return helpers."""
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id=TASK_ID, seed=42
    )
    return sm, sid, dict(targets)


def test_seed_discriminator_is_nontrivial():
    """The answer set must be a STRICT subset of all denied claims.

    If appealable == denied, the task is the trivial 'appeal all denied'
    again. The harder version requires excluding no-EOB + past-deadline
    denied decoys.
    """
    sm, sid, targets = _make_session()
    denied = set(targets["denied_claim_ids"])
    appealable = set(targets["appealable_denied_claim_ids"])
    assert appealable, "expected at least one appealable denied claim"
    assert appealable < denied, (
        "appealable set must be a STRICT subset of denied claims "
        f"(denied={sorted(denied)}, appealable={sorted(appealable)})"
    )
    # There must be genuine decoys to exclude.
    state = sm.get_state(sid)
    by_id = {c.id: c for c in state.claims}
    excluded = denied - appealable
    assert excluded, "expected non-appealable denied decoys"
    for cid in excluded:
        c = by_id[cid]
        # Every excluded denied claim must fail at least one real appeal gate.
        from datetime import timezone
        fails_eob = not c.eob_available
        fails_deadline = c.appeal_deadline < state_now(state)
        assert fails_eob or fails_deadline, (
            f"excluded claim {cid} should fail a gate but looks appealable"
        )


def state_now(state):
    """The seeder anchor used for appeal-deadline gating (utc_now-based)."""
    from webstress.backend.routes.patient_portal import utc_now
    return utc_now()


def test_correct_trajectory_via_backend_passes():
    """Appeal exactly the appealable denied claims through the real endpoint."""
    sm, sid, targets = _make_session()
    client = TestClient(app)

    appealed_ids = []
    for clm_id in targets["appealable_denied_claim_ids"]:
        resp = client.post(
            f"/api/env/patient_portal/claims/{clm_id}/appeal",
            json={"session_id": sid, "reason": "Submitting appeal with EOB on file."},
        )
        assert resp.status_code == 200, (
            f"appeal of {clm_id} should succeed past backend gates: "
            f"{resp.status_code} {resp.text}"
        )
        appealed_ids.append(clm_id)

    state = sm.get_state(sid)
    appealed_now = {c.id for c in state.claims if c.status == "appealed"}
    assert appealed_now == set(appealed_ids)

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"result: {result}"


def test_backend_rejects_non_appealable_decoys():
    """The backend itself must reject appealing no-EOB / past-deadline claims."""
    sm, sid, targets = _make_session()
    client = TestClient(app)
    denied = set(targets["denied_claim_ids"])
    appealable = set(targets["appealable_denied_claim_ids"])
    decoys = sorted(denied - appealable)
    assert decoys
    for clm_id in decoys:
        resp = client.post(
            f"/api/env/patient_portal/claims/{clm_id}/appeal",
            json={"session_id": sid, "reason": "trying to appeal a decoy"},
        )
        assert resp.status_code == 422, (
            f"decoy {clm_id} must be rejected by a backend gate, got {resp.status_code}"
        )


def test_under_appeal_fails():
    """Missing one appealable claim must fail (saturation + cardinality)."""
    sm, sid, targets = _make_session()
    client = TestClient(app)
    appealable = list(targets["appealable_denied_claim_ids"])
    assert len(appealable) >= 2
    for clm_id in appealable[:-1]:  # skip the last one -> under-appeal
        resp = client.post(
            f"/api/env/patient_portal/claims/{clm_id}/appeal",
            json={"session_id": sid, "reason": "appeal"},
        )
        assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, (
        f"under-appeal must fail, got: {result}"
    )


def test_over_appeal_with_extra_denied_claim_fails():
    """Appealing every appealable claim PLUS a wrongly-forced decoy must fail.

    The backend will not let us appeal a decoy, so we mirror an agent that
    bypasses the UI gate by directly flipping a past-deadline denied decoy
    (which still has eob_available=True) to 'appealed'. The cardinality
    constraint + comprehensive invariant must catch the extra mutation.
    """
    sm, sid, targets = _make_session()
    client = TestClient(app)
    for clm_id in targets["appealable_denied_claim_ids"]:
        resp = client.post(
            f"/api/env/patient_portal/claims/{clm_id}/appeal",
            json={"session_id": sid, "reason": "appeal"},
        )
        assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    # Find a denied decoy that has EOB but is past deadline (eob guard wouldn't
    # catch it, so the cardinality/comprehensive layers must).
    appealable = set(targets["appealable_denied_claim_ids"])
    denied = set(targets["denied_claim_ids"])
    extra = None
    for c in state.claims:
        if c.id in (denied - appealable) and c.eob_available:
            extra = c
            break
    assert extra is not None, "expected an EOB-bearing past-deadline decoy"
    extra.status = "appealed"  # direct mutation mirroring a gate-bypass

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, (
        f"over-appeal of a decoy must fail, got: {result}"
    )


def test_paying_appealable_claim_fails():
    """Paying (instead of appealing) a target claim must fail the diff."""
    sm, sid, targets = _make_session()
    client = TestClient(app)
    appealable = list(targets["appealable_denied_claim_ids"])
    # Appeal all but the first, then PAY the first (wrong action).
    for clm_id in appealable[1:]:
        resp = client.post(
            f"/api/env/patient_portal/claims/{clm_id}/appeal",
            json={"session_id": sid, "reason": "appeal"},
        )
        assert resp.status_code == 200, resp.text
    pay = client.post(
        f"/api/env/patient_portal/claims/{appealable[0]}/pay",
        json={"session_id": sid},
    )
    assert pay.status_code == 200, pay.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, (
        f"paying a target instead of appealing must fail, got: {result}"
    )
