"""Solvability proof for the upgraded ``pp_pay_claim`` task.

The upgraded task asks the agent to pay (zero out ``patient_responsibility``)
the THREE highest-balance approved insurance claims that have an available EOB
(ranked by patient responsibility, claim-id tiebreak), while leaving every other
claim — the cheaper approved claims, the processing claim, and all denied claims —
untouched, and sending no messages.

Levers exercised here:
  * seed-computed discriminator the agent must re-derive (top-K by
    patient_responsibility) — exposed as target ``top_k_payable_claim_ids``;
  * a SATURATING bijection over that target list (pay all 3, no excess);
  * a filtered critical invariant freezing every non-target claim;
  * exact-value + provenance expr predicates (``initial.get_claim``).

Correct path is driven through the REAL backend ``POST /claims/{id}/pay``
endpoint via ``starlette.testclient.TestClient(app)`` to confirm achievability
past the route's ``patient_responsibility > 0`` gate.
"""

from __future__ import annotations

from decimal import Decimal

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


PP = "/api/env/patient_portal"


def _new_session(seed: int = 42):
    """Create a real session on the app's session manager and return ids/targets."""
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id="pp_pay_claim", seed=seed
    )
    state = sm.get_state(sid)
    return sm, sid, dict(targets), state


def _pay(client: TestClient, sid: str, clm_id: str):
    resp = client.post(f"{PP}/claims/{clm_id}/pay", json={"session_id": sid})
    return resp


def test_seed_shape_is_solvable() -> None:
    """The seed must expose exactly 3 payable top-K claims and frozen siblings."""
    _, _, targets, state = _new_session(42)
    by_id = {c.id: c for c in state.claims}
    topk = list(targets["top_k_payable_claim_ids"])
    approved = list(targets["approved_claim_ids"])

    # Exactly K=3 payable targets, all approved + EOB + responsibility > 0.
    assert len(topk) == 3, topk
    for cid in topk:
        c = by_id[cid]
        assert cid in approved
        assert c.eob_available is True
        assert Decimal(str(c.patient_responsibility)) > 0

    # There must be cheaper approved claims that are NOT targets (the trap).
    non_target_approved = [cid for cid in approved if cid not in topk]
    assert non_target_approved, "expected cheaper approved claims to stay frozen"

    # The top-K are genuinely the highest-balance approved EOB claims.
    payable = [
        cid for cid in approved
        if by_id[cid].eob_available and Decimal(str(by_id[cid].patient_responsibility)) > 0
    ]
    expected = sorted(
        payable,
        key=lambda cid: (-float(by_id[cid].patient_responsibility), cid),
    )[:3]
    assert set(topk) == set(expected), (topk, expected)

    # service_date order must NOT coincide with the amount order — proving the
    # agent cannot solve via a "most recent" heuristic.
    by_date = sorted(payable, key=lambda cid: (by_id[cid].service_date, cid))[-3:]
    assert set(by_date) != set(topk), "amount-ranking must differ from date-ranking"


def test_correct_trajectory_via_real_pay_endpoint_passes() -> None:
    """Paying exactly the top-3 payable claims through the real endpoint passes."""
    sm, sid, targets, state = _new_session(42)
    client = TestClient(app)

    for cid in targets["top_k_payable_claim_ids"]:
        resp = _pay(client, sid, cid)
        assert resp.status_code == 200, resp.text
        assert Decimal(str(resp.json()["patient_responsibility"])) == 0

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task("pp_pay_claim"),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, result
    # canonical_diff coverage is richer than a single check.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_paying_a_cheaper_approved_claim_fails() -> None:
    """Near-miss: pay 2 correct + 1 WRONG (a cheaper frozen approved claim)."""
    sm, sid, targets, state = _new_session(42)
    client = TestClient(app)

    topk = list(targets["top_k_payable_claim_ids"])
    approved = list(targets["approved_claim_ids"])
    wrong = next(cid for cid in approved if cid not in topk)

    # Pay only the first two correct targets, then pay a wrong (frozen) claim.
    for cid in topk[:2]:
        assert _pay(client, sid, cid).status_code == 200
    assert _pay(client, sid, wrong).status_code == 200

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task("pp_pay_claim"),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"expected fail, got: {result}"


def test_underpaying_only_two_targets_fails() -> None:
    """Near-miss: pay only 2 of the 3 required targets (bijection not saturated)."""
    sm, sid, targets, state = _new_session(42)
    client = TestClient(app)

    for cid in list(targets["top_k_payable_claim_ids"])[:2]:
        assert _pay(client, sid, cid).status_code == 200

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task("pp_pay_claim"),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"expected fail, got: {result}"
