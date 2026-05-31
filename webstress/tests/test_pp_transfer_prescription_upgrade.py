"""Solvability proof + adversarial check for the hardened pp_transfer_prescription.

The task was re-tiered from medium → hard. It now requires the agent to:
  * identify the SUBSET of active prescriptions currently filled at the
    discontinuing mail-order pharmacy (a computed set the agent must re-derive
    from each rx's pharmacy_id), and
  * transfer EXACTLY that subset to the specific Walgreens retail pharmacy,
  * touching nothing else (no default change, no refill/renewal, no message).

The correct solution is driven through the REAL backend transfer endpoint
(POST /api/env/patient_portal/medications/{rx_id}/transfer) via a Starlette
TestClient bound to the app's shared SessionManager, then graded through the
canonical_diff evaluator. A near-miss trajectory (transferring an extra,
non-mail-order prescription) is asserted to FAIL.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_transfer_prescription"
PREFIX = "/api/env/patient_portal"


def _new_session():
    """Create a session on the app's shared SessionManager.

    Returns (client, session_id, targets, state). Driving mutations through the
    TestClient hits the same SessionManager instance, so the post-mutation
    state is the exact object the evaluator reads.
    """
    client = TestClient(app)
    sm = app.state.session_manager
    session_id, targets, _seed = sm.create_session(
        env_id="patient_portal", task_id=TASK_ID, seed=42
    )
    state = sm.get_state(session_id)
    return client, session_id, dict(targets), state


def _transfer(client: TestClient, session_id: str, rx_id: str, pharmacy_id: str):
    return client.post(
        f"{PREFIX}/medications/{rx_id}/transfer",
        json={"session_id": session_id, "pharmacy_id": pharmacy_id},
    )


def test_seed_shape_is_unambiguous():
    """The transfer set must equal exactly the ACTIVE rxes at the mail-order pharmacy."""
    _client, _sid, targets, state = _new_session()

    source = targets["source_pharmacy_id"]
    dest = targets["target_pharmacy_id"]
    transfer_set = set(targets["rxes_at_source_pharmacy"])

    assert targets["source_pharmacy_id"] == targets["mail_order_pharmacy_id"]
    assert source != dest and dest != targets["default_pharmacy_id"]
    assert len(transfer_set) >= 2  # genuine bijection, not a single update

    active_at_source = {
        r.id for r in state.prescriptions
        if r.status == "active" and r.pharmacy_id == source
    }
    # Every active rx at the mail-order pharmacy is in the transfer set and
    # vice versa — no ambiguity for a correct agent.
    assert active_at_source == transfer_set, (active_at_source, transfer_set)

    # No non-source active rx is pre-positioned at the destination (each
    # transfer is a real move that produces a diff Update).
    for r in state.prescriptions:
        if r.status == "active" and r.id in transfer_set:
            assert r.pharmacy_id == source


def test_correct_trajectory_passes():
    """Transferring exactly the mail-order subset to Walgreens passes via evaluate()."""
    client, session_id, targets, state = _new_session()
    dest = targets["target_pharmacy_id"]

    for rx_id in targets["rxes_at_source_pharmacy"]:
        resp = _transfer(client, session_id, rx_id, dest)
        assert resp.status_code == 200, resp.text
        assert resp.json()["pharmacy_id"] == dest

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score: {result.get('score')}"
    # Hard task: richer than the legacy 2-check eval (bijection + constraints
    # + standing invariant wall).
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 5


def test_extra_transfer_fails():
    """Transferring an EXTRA non-mail-order active rx trips the filtered invariant."""
    client, session_id, targets, state = _new_session()
    dest = targets["target_pharmacy_id"]

    for rx_id in targets["rxes_at_source_pharmacy"]:
        assert _transfer(client, session_id, rx_id, dest).status_code == 200

    # Move one decoy (a non-source active rx) as well — over-acting.
    extra = targets["non_source_active_rx_ids"][0]
    assert _transfer(client, session_id, extra, dest).status_code == 200

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_incomplete_transfer_fails():
    """Transferring only part of the mail-order subset fails the bijection."""
    client, session_id, targets, state = _new_session()
    dest = targets["target_pharmacy_id"]

    subset = list(targets["rxes_at_source_pharmacy"])
    assert len(subset) >= 2
    for rx_id in subset[:-1]:  # leave one mail-order rx behind
        assert _transfer(client, session_id, rx_id, dest).status_code == 200

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_wrong_destination_fails():
    """Transferring the mail-order subset to a NON-Walgreens pharmacy fails."""
    client, session_id, targets, state = _new_session()
    wrong_dest = targets["default_pharmacy_id"]  # CVS, not the required Walgreens
    assert wrong_dest != targets["target_pharmacy_id"]

    for rx_id in targets["rxes_at_source_pharmacy"]:
        assert _transfer(client, session_id, rx_id, wrong_dest).status_code == 200

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"
