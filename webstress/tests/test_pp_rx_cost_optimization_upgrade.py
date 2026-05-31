"""Solvability proof for the hardened pp_rx_cost_optimization task.

The upgraded task asks the agent to:
  1. Identify the single mail-order pharmacy with the LOWEST 90-day supply cost
     (a computed discriminator — the destination is no longer named in the
     instruction; the agent must price-compare several mail-order options).
  2. Transfer EXACTLY the active prescriptions that are currently filled at a
     retail (non-mail-order) pharmacy AND still have >=1 refill remaining
     (a precomputed eligibility bijection) to that cheapest mail-order pharmacy.
  3. Leave every other prescription (zero-refill active, already-at-mail-order
     active, expired) frozen; not touch the default pharmacy; send no messages;
     request no renewal.

This drives the CORRECT solution through the real
``POST /api/env/patient_portal/medications/{rx_id}/transfer`` endpoint and
asserts the canonical_diff evaluator scores it as a pass, then drives several
near-miss trajectories and asserts each fails.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_rx_cost_optimization"


def _new_session(seed: int = 42):
    """Create a session on the app's shared SessionManager and return
    (client, session_manager, sid, targets)."""
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id=TASK_ID, seed=seed
    )
    client = TestClient(app)
    return client, sm, sid, dict(targets)


def _transfer(client: TestClient, sid: str, rx_id: str, pharmacy_id: str):
    return client.post(
        f"/api/env/patient_portal/medications/{rx_id}/transfer",
        json={"session_id": sid, "pharmacy_id": pharmacy_id},
    )


def _evaluate(sm, sid: str, targets: dict):
    state = sm.get_state(sid)
    return evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=targets,
        trajectory=[],
    )


# ---------------------------------------------------------------------------
# Sanity: the seed shape the proof depends on
# ---------------------------------------------------------------------------

def test_seed_shape_is_discriminating():
    """Every relevant seed exposes a non-empty eligible set, an excluded
    active set, >=2 distinct-cost mail-order pharmacies, and a cheapest that is
    truly the min cost with the documented id tie-break."""
    for seed in (42, 1, 7, 100, 2026):
        _client, sm, sid, t = _new_session(seed)
        state = sm.get_state(sid)
        pharms = {p.id: p for p in state.pharmacies}

        eligible = t["retail_refillable_active_rx_ids"]
        assert len(eligible) >= 1, f"seed {seed}: empty eligible set"
        excluded = [r for r in t["active_rx_ids"] if r not in eligible]
        assert len(excluded) >= 1, f"seed {seed}: nothing excluded (not discriminating)"

        mo_ids = t["mail_order_pharmacy_ids"]
        assert len(mo_ids) >= 2, f"seed {seed}: <2 mail-order pharmacies"

        costs = {pid: float(pharms[pid].cost_per_90day_supply) for pid in mo_ids}
        min_cost = min(costs.values())
        winners = sorted(pid for pid, c in costs.items() if c == min_cost)
        assert t["cheapest_mail_order_pharmacy_id"] == winners[0], (
            f"seed {seed}: cheapest target {t['cheapest_mail_order_pharmacy_id']} "
            f"!= min-cost id-tiebreak winner {winners[0]} (costs={costs})"
        )

        # Every eligible rx is genuinely at a retail pharmacy with >=1 refill,
        # and every excluded active rx fails at least one condition.
        rx_by_id = {rx.id: rx for rx in state.prescriptions}
        for rid in eligible:
            rx = rx_by_id[rid]
            assert not pharms[rx.pharmacy_id].is_mail_order
            assert rx.refills_remaining >= 1
        for rid in excluded:
            rx = rx_by_id[rid]
            assert pharms[rx.pharmacy_id].is_mail_order or rx.refills_remaining == 0


# ---------------------------------------------------------------------------
# CORRECT solution through the real transfer endpoint passes
# ---------------------------------------------------------------------------

def test_correct_transfer_passes_via_real_endpoint():
    """Transfer exactly the eligible rxes to the cheapest mail-order pharmacy
    through the real endpoint; the evaluator must score it as a pass."""
    for seed in (42, 1, 7, 100, 2026):
        client, sm, sid, t = _new_session(seed)
        dest = t["cheapest_mail_order_pharmacy_id"]
        for rx_id in t["retail_refillable_active_rx_ids"]:
            resp = _transfer(client, sid, rx_id, dest)
            assert resp.status_code == 200, f"seed {seed} rx {rx_id}: {resp.text}"
            assert resp.json()["pharmacy_id"] == dest

        result = _evaluate(sm, sid, t)
        assert result.get("success") is True, f"seed {seed}: {result}"
        assert result.get("score", 0.0) >= 0.99, f"seed {seed}: {result}"
        # canonical_diff is richer than a 2-check eval (1 bijection + 8 invariants).
        assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


# ---------------------------------------------------------------------------
# WRONG / near-miss trajectories fail
# ---------------------------------------------------------------------------

def test_wrong_destination_fails():
    """Transferring eligible rxes to a NON-cheapest mail-order pharmacy fails."""
    client, sm, sid, t = _new_session(42)
    wrong = next(
        pid for pid in t["mail_order_pharmacy_ids"]
        if pid != t["cheapest_mail_order_pharmacy_id"]
    )
    for rx_id in t["retail_refillable_active_rx_ids"]:
        assert _transfer(client, sid, rx_id, wrong).status_code == 200
    result = _evaluate(sm, sid, t)
    assert result.get("success") is False, result


def test_partial_transfer_fails():
    """Transferring only SOME eligible rxes (missing one) fails the bijection."""
    client, sm, sid, t = _new_session(42)
    dest = t["cheapest_mail_order_pharmacy_id"]
    eligible = list(t["retail_refillable_active_rx_ids"])
    assert len(eligible) >= 2
    for rx_id in eligible[:-1]:  # skip the last eligible rx
        assert _transfer(client, sid, rx_id, dest).status_code == 200
    result = _evaluate(sm, sid, t)
    assert result.get("success") is False, result


def test_over_transfer_excluded_rx_fails():
    """Also transferring an EXCLUDED active rx (zero-refill or already
    mail-order) trips the prescriptions invariant and fails."""
    client, sm, sid, t = _new_session(42)
    dest = t["cheapest_mail_order_pharmacy_id"]
    eligible = list(t["retail_refillable_active_rx_ids"])
    excluded = [r for r in t["active_rx_ids"] if r not in eligible]
    assert excluded, "seed 42 expected to have excluded active rxes"
    for rx_id in eligible:
        assert _transfer(client, sid, rx_id, dest).status_code == 200
    # Over-act: move an excluded active rx to the cheapest pharmacy too.
    # (Excluded rxes are still 'active' so the transfer endpoint accepts them.)
    extra = excluded[0]
    resp = _transfer(client, sid, extra, dest)
    if resp.status_code == 200:
        result = _evaluate(sm, sid, t)
        assert result.get("success") is False, result
    else:
        # If the endpoint refused (e.g. expired status), the over-act could not
        # happen — pick a different excluded rx that is active.
        active_ids = set(t["active_rx_ids"])
        extra2 = next((r for r in excluded if r in active_ids), None)
        assert extra2 is not None
        assert _transfer(client, sid, extra2, dest).status_code == 200
        result = _evaluate(sm, sid, t)
        assert result.get("success") is False, result


def test_default_pharmacy_change_fails():
    """Doing the correct transfers but also flipping the default pharmacy
    trips the pharmacies invariant and fails."""
    client, sm, sid, t = _new_session(42)
    dest = t["cheapest_mail_order_pharmacy_id"]
    for rx_id in t["retail_refillable_active_rx_ids"]:
        assert _transfer(client, sid, rx_id, dest).status_code == 200
    # Mirror a "set default pharmacy" side effect directly on state (there is
    # no dedicated default-change route; this models an over-eager agent that
    # marks the new pharmacy as default). method: direct state mutation.
    state = sm.get_state(sid)
    for p in state.pharmacies:
        if p.id == t["default_pharmacy_id"]:
            p.is_default = False
        if p.id == dest:
            p.is_default = True
    result = _evaluate(sm, sid, t)
    assert result.get("success") is False, result


def test_no_action_fails():
    """Doing nothing fails (the positive bijection is unsatisfied)."""
    _client, sm, sid, t = _new_session(42)
    result = _evaluate(sm, sid, t)
    assert result.get("success") is False, result
