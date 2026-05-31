"""Solvability proof for the upgraded pp_review_eob task.

The hardened task asks the agent to review the EOB on each insurance claim and
pay (zero out) the patient responsibility on EVERY approved claim that has an
available EOB AND a strictly-positive balance -- while leaving alone:
  * approved claims whose EOB is not yet available (positive balance, but the
    "review EOB then pay" workflow must skip them),
  * fully-covered approved claims (patient_responsibility == 0; the pay route
    422s on these),
  * denied claims (appeal/pay temptation),
  * the processing claim,
  * everything in the other collections.

The correct answer set is the seed-computed discriminator
``payable_approved_claim_ids``. The test drives the REAL backend pay endpoint
via the FastAPI TestClient for the correct trajectory, then asserts the
canonical_diff evaluator passes; a near-miss (paying a no-EOB approved claim)
is asserted to fail.
"""

from __future__ import annotations

from decimal import Decimal

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

BASE = "/api/env/patient_portal"


def _make_session() -> tuple[TestClient, SessionManager, str, dict]:
    """Create a seed-42 session and bind it to the TestClient app so the
    real /pay route mutates exactly this session."""
    sm = SessionManager()
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id="pp_review_eob", seed=42
    )
    # Point the app's dependency at our manager so the REST endpoints operate
    # on the same seeded session we hold the targets for.
    app.state.session_manager = sm
    client = TestClient(app)
    return client, sm, sid, dict(targets)


def test_payable_discriminator_is_nontrivial():
    """The seed must produce a real filtering problem: more approved claims
    than payable ones, plus denied/processing decoys."""
    sm = SessionManager()
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id="pp_review_eob", seed=42
    )
    targets = dict(targets)
    payable = targets["payable_approved_claim_ids"]
    approved = targets["approved_claim_ids"]
    denied = targets["denied_claim_ids"]
    assert payable, "expected at least one payable approved claim"
    # Strictly fewer payable than approved -> there ARE approved claims that
    # must be skipped (no-EOB or zero-balance).
    assert len(payable) < len(approved)
    assert denied, "expected denied claims as appeal/pay decoys"

    state = sm.get_state(sid)
    by_id = {c.id: c for c in state.claims}
    # Every payable claim is approved, eob-available, positive balance.
    for cid in payable:
        c = by_id[cid]
        assert c.status == "approved"
        assert c.eob_available is True
        assert float(c.patient_responsibility) > 0
    # Every approved claim NOT payable is genuinely ineligible (no EOB or zero
    # balance) -- not an arbitrary omission.
    for cid in approved:
        if cid in payable:
            continue
        c = by_id[cid]
        assert (c.eob_available is False) or (float(c.patient_responsibility) == 0)


def test_correct_trajectory_passes_via_real_pay_endpoint():
    client, sm, sid, targets = _make_session()
    payable = targets["payable_approved_claim_ids"]

    for cid in payable:
        resp = client.post(f"{BASE}/claims/{cid}/pay", json={"session_id": sid})
        assert resp.status_code == 200, resp.text
        assert Decimal(str(resp.json()["patient_responsibility"])) == Decimal("0")

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task("pp_review_eob"),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"result: {result}"
    # Richer than a 2-check legacy eval (bijection update + filtered invariants).
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_paying_extra_no_eob_claim_fails():
    """Near-miss: agent pays all payable claims AND one approved-but-no-EOB
    claim (a tempting positive-balance lookalike). The filtered claims
    invariant must catch the unauthorized extra payment."""
    client, sm, sid, targets = _make_session()
    payable = set(targets["payable_approved_claim_ids"])
    approved = targets["approved_claim_ids"]

    state = sm.get_state(sid)
    by_id = {c.id: c for c in state.claims}
    # Find an approved claim that is NOT payable but still has a balance the
    # pay route will accept (i.e. a no-EOB approved claim with resp > 0).
    extra = next(
        cid for cid in approved
        if cid not in payable
        and by_id[cid].eob_available is False
        and float(by_id[cid].patient_responsibility) > 0
    )

    for cid in payable:
        resp = client.post(f"{BASE}/claims/{cid}/pay", json={"session_id": sid})
        assert resp.status_code == 200, resp.text
    # Pay the forbidden extra claim too.
    resp = client.post(f"{BASE}/claims/{extra}/pay", json={"session_id": sid})
    assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task("pp_review_eob"),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_under_paying_fails():
    """Near-miss: agent pays only a subset of the payable claims. The
    bijection over payable_approved_claim_ids must fail to saturate."""
    client, sm, sid, targets = _make_session()
    payable = targets["payable_approved_claim_ids"]
    assert len(payable) >= 2

    # Pay all but the last payable claim.
    for cid in payable[:-1]:
        resp = client.post(f"{BASE}/claims/{cid}/pay", json={"session_id": sid})
        assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task("pp_review_eob"),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_appealing_denied_claim_fails():
    """Near-miss: agent pays the payable claims correctly but also appeals a
    denied claim (a strong distractor). The critical claims invariant must
    catch the out-of-set mutation."""
    client, sm, sid, targets = _make_session()
    payable = targets["payable_approved_claim_ids"]
    denied = targets["denied_claim_ids"]

    for cid in payable:
        resp = client.post(f"{BASE}/claims/{cid}/pay", json={"session_id": sid})
        assert resp.status_code == 200, resp.text
    # Appeal a denied claim (allowed by the route, forbidden by the task).
    resp = client.post(
        f"{BASE}/claims/{denied[0]}/appeal",
        json={"session_id": sid, "reason": "Requesting review"},
    )
    assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task("pp_review_eob"),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"
