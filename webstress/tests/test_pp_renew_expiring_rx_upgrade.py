"""Solvability + near-miss proof for the hardened pp_renew_expiring_rx task.

The task was hardened in-place (tier stays ``hard``) with:
  - higher bijection cardinality (3 expiring zero-refill rxes must be renewed),
  - stronger distractors (2 expiring rxes that still have refills, 2 zero-refill
    rxes that are NOT expiring within 30 days, 1 expired rx),
  - tightened create predicates (per-rx medication name in body + freshness),
  - a tightened update (refills_remaining preserved at 0),
  - critical sibling invariant/constraint guarding every non-target prescription.

The CORRECT solution is driven through the REAL backend renewal endpoint
(``request_renewal`` in backend/routes/patient_portal.py), which atomically
flips the prescription to ``pending_renewal`` and creates the linked
``rx_renewal`` ClinicalMessage. We then evaluate the post-state with the real
``evaluate`` entry point. Method: real route-handler functions are invoked
directly with a local SessionManager (no HTTP plumbing) so the post-state is
directly inspectable; this exercises the identical backend mutation code path
the HTTP layer calls.
"""

from webstress.backend.state import SessionManager
from webstress.backend.routes.patient_portal import (
    SessionScopedRequest,
    request_renewal,
    refill_medication,
)
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_renew_expiring_rx"


def _fresh_session():
    sm = SessionManager()
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id=TASK_ID, seed=42
    )
    return sm, sid, dict(targets)


def test_seed_shape_is_harder():
    """Seed exposes 3 required renewal slots plus the harder distractor sets."""
    _, _, targets = _fresh_session()
    assert len(targets["expiring_zero_refill_rx_ids"]) == 3
    # Expiring-with-refills distractors exist and are disjoint from the targets.
    assert len(targets["expiring_with_refills_rx_ids"]) >= 2
    assert not (
        set(targets["expiring_with_refills_rx_ids"])
        & set(targets["expiring_zero_refill_rx_ids"])
    )
    # The standalone zero-refill (non-expiring) trap is not a target.
    assert targets["zero_refill_rx_id"] not in targets["expiring_zero_refill_rx_ids"]


def test_correct_trajectory_via_renewal_endpoint_passes():
    """Renewing exactly the expiring zero-refill rxes via the real endpoint passes."""
    sm, sid, targets = _fresh_session()

    for rx_id in targets["expiring_zero_refill_rx_ids"]:
        request_renewal(
            rx_id=rx_id,
            body=SessionScopedRequest(session_id=sid),
            session_manager=sm,
        )

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99
    # Hardened diff is richer than the legacy 2-check eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_missing_one_renewal_fails():
    """Renewing only 2 of the 3 required rxes fails the saturating bijection."""
    sm, sid, targets = _fresh_session()

    for rx_id in targets["expiring_zero_refill_rx_ids"][:-1]:  # skip last
        request_renewal(
            rx_id=rx_id,
            body=SessionScopedRequest(session_id=sid),
            session_manager=sm,
        )

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_renewing_a_zero_refill_non_expiring_rx_fails():
    """Renewing the zero-refill rx that is NOT expiring within 30 days (the
    most tempting distractor: it has 0 refills like the targets) trips the
    critical sibling invariant/constraint even though all 3 targets are done."""
    sm, sid, targets = _fresh_session()

    for rx_id in targets["expiring_zero_refill_rx_ids"]:
        request_renewal(
            rx_id=rx_id,
            body=SessionScopedRequest(session_id=sid),
            session_manager=sm,
        )
    # Wrong extra action: renew the non-expiring zero-refill rx.
    request_renewal(
        rx_id=targets["zero_refill_rx_id"],
        body=SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_refilling_an_expiring_with_refills_rx_fails():
    """An agent that refills (instead of leaving alone) an expiring rx that
    still has refills mutates a frozen sibling and fails."""
    sm, sid, targets = _fresh_session()

    for rx_id in targets["expiring_zero_refill_rx_ids"]:
        request_renewal(
            rx_id=rx_id,
            body=SessionScopedRequest(session_id=sid),
            session_manager=sm,
        )
    # Wrong action: consume a refill on an expiring-with-refills rx.
    refill_medication(
        rx_id=targets["expiring_with_refills_rx_ids"][0],
        body=SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"
