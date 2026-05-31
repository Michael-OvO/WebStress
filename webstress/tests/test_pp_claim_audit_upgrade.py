"""Solvability proof for the hardened pp_claim_audit task.

The hardened task seeds a MIX of denied claims: 6 eligible (denied + EOB +
future deadline) plus 6 denied-but-INELIGIBLE lookalikes (3 with no EOB, 3
with an already-expired appeal deadline). Several ineligible lookalikes carry
a HIGHER patient_responsibility than the genuine top-3 (e.g. the expired
clm_15 with the highest PR of all denied claims), so an agent that ranks
top-3-by-PR without applying the full eligibility filter will pick decoys.

The intended answer is target['top_3_appealable_claim_ids'] (the top 3 of the
eligible set by patient responsibility, claim-id tiebreaker). We drive the
correct solution through the REAL backend appeal endpoint via TestClient to
confirm the answer is achievable past the route's gates (denied + eob +
deadline), then assert the canonical_diff evaluator scores it as a pass.
"""

from decimal import Decimal

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

_PREFIX = "/api/env/patient_portal"


def _new_session():
    """Create a session on the app's shared SessionManager (so HTTP routes and
    the evaluator operate on the SAME state object)."""
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id="pp_claim_audit", seed=42
    )
    return sm, sid, dict(targets)


def _appeal(client: TestClient, sid: str, clm_id: str):
    return client.post(
        f"{_PREFIX}/claims/{clm_id}/appeal",
        json={"session_id": sid, "reason": "Documentation submitted for reconsideration."},
    )


def test_correct_trajectory_evaluates_to_pass():
    sm, sid, targets = _new_session()
    state = sm.get_state(sid)
    top3 = targets["top_3_appealable_claim_ids"]

    # Sanity: the intended set must be a strict subset of the eligible set,
    # which itself must be a strict subset of all denied claims — i.e. the
    # eligibility filter actually removes lookalikes.
    assert len(top3) == 3
    eligible = set(targets["appealable_claim_ids"])
    denied = set(targets["denied_claim_ids"])
    assert set(top3).issubset(eligible)
    assert eligible.issubset(denied)
    assert len(eligible) < len(denied), "eligibility filter must remove some denied lookalikes"

    # The highest-patient-responsibility denied claim overall must NOT be in the
    # intended set (it is an ineligible decoy), proving the filter matters.
    claims_by_id = {c.id: c for c in state.claims}
    denied_sorted = sorted(
        denied, key=lambda cid: float(claims_by_id[cid].patient_responsibility), reverse=True
    )
    assert denied_sorted[0] not in top3, "top denied-by-PR claim should be an ineligible decoy"

    with TestClient(app) as client:
        for clm_id in top3:
            resp = _appeal(client, sid, clm_id)
            assert resp.status_code == 200, f"appeal {clm_id} failed: {resp.status_code} {resp.text}"
            assert resp.json()["status"] == "appealed"

    state = sm.get_state(sid)
    appealed = [c.id for c in state.claims if c.status == "appealed"]
    assert sorted(appealed) == sorted(top3)

    result = evaluate(
        task=get_task("pp_claim_audit"),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score: {result.get('score')}"
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_ineligible_decoy_is_gated_by_the_appeal_endpoint():
    """The real appeal route must REJECT the ineligible high-PR decoys, proving
    the eligibility distinction is enforced server-side (no EOB / expired)."""
    sm, sid, targets = _new_session()
    state = sm.get_state(sid)
    claims_by_id = {c.id: c for c in state.claims}

    denied = set(targets["denied_claim_ids"])
    eligible = set(targets["appealable_claim_ids"])
    ineligible = denied - eligible
    assert ineligible, "there must be denied-but-ineligible decoys"

    with TestClient(app) as client:
        for clm_id in ineligible:
            resp = _appeal(client, sid, clm_id)
            assert resp.status_code == 422, (
                f"ineligible {clm_id} should be rejected, got {resp.status_code}"
            )

    # Nothing was appealed, so the evaluator must NOT pass (no positive op).
    state = sm.get_state(sid)
    assert all(c.status != "appealed" for c in state.claims)
    result = evaluate(
        task=get_task("pp_claim_audit"),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False


def test_overappeal_near_miss_evaluates_to_fail():
    """Appealing the correct 3 PLUS one extra eligible (but not top-3) claim is
    a near miss: the endpoint allows it (it is eligible) but the exact-3
    bijection + cardinality constraint must reject it."""
    sm, sid, targets = _new_session()
    state = sm.get_state(sid)
    top3 = targets["top_3_appealable_claim_ids"]
    eligible = targets["appealable_claim_ids"]

    extra = next(cid for cid in eligible if cid not in top3)

    with TestClient(app) as client:
        for clm_id in list(top3) + [extra]:
            resp = _appeal(client, sid, clm_id)
            assert resp.status_code == 200, f"appeal {clm_id} failed: {resp.text}"

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task("pp_claim_audit"),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"over-appeal must fail: {result}"


def test_paying_a_claim_violates_forbidden_action():
    """Appealing the correct 3 but ALSO paying a claim (zeroing patient
    responsibility) must trip the critical 'do not pay any claim' constraint."""
    sm, sid, targets = _new_session()
    state = sm.get_state(sid)
    top3 = targets["top_3_appealable_claim_ids"]

    # Pay an approved claim with positive responsibility (real pay endpoint).
    approved = targets["approved_claim_ids"]
    claims_by_id = {c.id: c for c in state.claims}
    payable = next(
        cid for cid in approved if Decimal(str(claims_by_id[cid].patient_responsibility)) > 0
    )

    with TestClient(app) as client:
        for clm_id in top3:
            assert _appeal(client, sid, clm_id).status_code == 200
        pay = client.post(f"{_PREFIX}/claims/{payable}/pay", json={"session_id": sid})
        assert pay.status_code == 200, pay.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task("pp_claim_audit"),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"paying a claim must fail: {result}"
