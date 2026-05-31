"""Solvability + difficulty proof for the upgraded ``pp_request_renewal`` task.

The task was hardened from a single trivial renewal into a dual-action,
discriminator-gated task:

* renew **every** active prescription that has 0 refills remaining (a bijection
  over ``target['zero_refill_rx_ids']`` — there are now two such prescriptions),
* refill **exactly** the named refillable prescription
  (``target['refill_rx_id']`` / ``target['refill_medication']``), which must
  have its ``refills_remaining`` decremented by one and ``last_filled``
  refreshed, while staying ``active``,
* touch nothing else (every sibling collection is pinned ``preserve: ALL`` and
  the most-tempting prescription sibling invariant is ``severity: critical``).

The correct solution is driven through the REAL backend endpoints
(``POST /medications/{rx_id}/renewal`` and ``POST /medications/{rx_id}/refill``)
so the proof also confirms the canonical answer survives the server's status
gates (renewal requires status in {active, expired}; refill requires
status == active and refills_remaining > 0).
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

API = "/api/env/patient_portal"
TASK_ID = "pp_request_renewal"


def _fresh_session(seed: int = 42):
    """Create a session on a manager wired into the app so TestClient calls hit
    the same state we later hand to ``evaluate``.

    Returns ``(session_manager, session_id, targets_dict, client)``.
    """
    sm = SessionManager()
    app.state.session_manager = sm
    sid, targets, _ = sm.create_session(env_id="patient_portal", task_id=TASK_ID, seed=seed)
    return sm, sid, dict(targets), TestClient(app)


def _renew(client: TestClient, sid: str, rx_id: str):
    return client.post(f"{API}/medications/{rx_id}/renewal", json={"session_id": sid})


def _refill(client: TestClient, sid: str, rx_id: str):
    return client.post(f"{API}/medications/{rx_id}/refill", json={"session_id": sid})


def _evaluate(sm: SessionManager, sid: str, targets: dict):
    return evaluate(
        task=get_task(TASK_ID),
        server_state=sm.get_state(sid),
        targets=targets,
        trajectory=[],
    )


# ---------------------------------------------------------------------------
# Seed sanity — the discriminator targets are well-formed and distinct.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [42, 7, 123, 2026])
def test_seed_targets_are_well_formed(seed: int):
    sm, sid, t, _ = _fresh_session(seed)
    state = sm.get_state(sid)
    rxes = {r.id: r for r in state.prescriptions}

    # Two distinct zero-refill renewal slots.
    assert len(t["zero_refill_rx_ids"]) == 2
    assert len(set(t["zero_refill_rx_ids"])) == 2
    for rid in t["zero_refill_rx_ids"]:
        assert rxes[rid].refills_remaining == 0
        assert rxes[rid].status == "active"

    # The refill target is a DIFFERENT, refillable prescription.
    refill_id = t["refill_rx_id"]
    assert refill_id not in t["zero_refill_rx_ids"]
    assert rxes[refill_id].refills_remaining > 0
    assert rxes[refill_id].status == "active"
    assert t["refill_medication"].lower() in rxes[refill_id].medication.lower()


# ---------------------------------------------------------------------------
# Correct solution — driven through the real endpoints — passes.
# ---------------------------------------------------------------------------

def test_correct_trajectory_via_real_endpoints_passes():
    sm, sid, t, client = _fresh_session(seed=42)

    # Method: the canonical solution is driven entirely through the real
    # POST /medications/{rx_id}/renewal and /refill backend routes so the
    # proof confirms the intended answer survives the server status gates.
    for rid in t["zero_refill_rx_ids"]:
        resp = _renew(client, sid, rid)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending_renewal"

    before = next(r for r in sm.get_state(sid).prescriptions if r.id == t["refill_rx_id"])
    before_refills = before.refills_remaining
    resp = _refill(client, sid, t["refill_rx_id"])
    assert resp.status_code == 200, resp.text
    assert resp.json()["refills_remaining"] == before_refills - 1
    assert resp.json()["status"] == "active"

    result = _evaluate(sm, sid, t)
    assert result["success"] is True, f"result: {result}"
    assert result["score"] >= 0.99
    # canonical_diff checks are richer than a single positive op.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


# ---------------------------------------------------------------------------
# Wrong / near-miss trajectories — fail.
# ---------------------------------------------------------------------------

def test_missing_second_renewal_fails():
    """Only one of the two zero-refill prescriptions renewed — bijection not
    saturated — so the task fails even though the refill is correct."""
    sm, sid, t, client = _fresh_session(seed=42)
    _renew(client, sid, t["zero_refill_rx_ids"][0])  # forget the second
    _refill(client, sid, t["refill_rx_id"])

    result = _evaluate(sm, sid, t)
    assert result["success"] is False


def test_skipping_the_refill_fails():
    """Both renewals done but the required refill skipped."""
    sm, sid, t, client = _fresh_session(seed=42)
    for rid in t["zero_refill_rx_ids"]:
        _renew(client, sid, rid)

    result = _evaluate(sm, sid, t)
    assert result["success"] is False


def test_renewing_the_refill_target_instead_of_refilling_fails():
    """Refill-vs-renewal confusion: the agent renews the medication that should
    have been refilled. refills_remaining and last_filled are then unchanged, so
    the update[1] predicate fails."""
    sm, sid, t, client = _fresh_session(seed=42)
    for rid in t["zero_refill_rx_ids"]:
        _renew(client, sid, rid)
    _renew(client, sid, t["refill_rx_id"])  # WRONG: renewal, not refill

    result = _evaluate(sm, sid, t)
    assert result["success"] is False


def test_extra_renewal_on_frozen_sibling_fails():
    """Everything correct, plus a stray renewal on a refillable prescription
    that should have been left untouched — trips the critical
    prescription-preservation invariant."""
    sm, sid, t, client = _fresh_session(seed=42)
    for rid in t["zero_refill_rx_ids"]:
        _renew(client, sid, rid)
    _refill(client, sid, t["refill_rx_id"])

    other = next(
        r.id
        for r in sm.get_state(sid).prescriptions
        if r.status == "active"
        and r.id not in t["zero_refill_rx_ids"]
        and r.id != t["refill_rx_id"]
    )
    _renew(client, sid, other)

    result = _evaluate(sm, sid, t)
    assert result["success"] is False
