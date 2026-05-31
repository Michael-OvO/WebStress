"""Solvability proof for the upgraded (medium-tier) pp_update_default_pharmacy.

Drives the CORRECT solution through the REAL backend mutation endpoints
(set-default + transfer) via Starlette TestClient so the proof also confirms
the actions are achievable past all gates, then evaluates the net state diff
through the canonical_diff evaluator. A deliberately-wrong/near-miss
trajectory is also evaluated and must fail.

Method: real endpoints via TestClient(app), sharing app.state.session_manager
so the mutated state is exactly what evaluate() grades.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


TASK_ID = "pp_update_default_pharmacy"


def _new_session():
    """Create a session on the APP's session manager and return its primitives.

    Returns (client, session_id, targets, state). The client drives mutations
    against the same SessionManager the state is read from, so evaluate() sees
    exactly the endpoint-mutated state.
    """
    client = TestClient(app)
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id=TASK_ID, seed=42
    )
    state = sm.get_state(sid)
    return client, sid, dict(targets), state


def _set_default(client: TestClient, sid: str, pharm_id: str):
    return client.post(
        f"/api/env/patient_portal/profile/pharmacy/{pharm_id}/set-default",
        json={"session_id": sid},
    )


def _transfer(client: TestClient, sid: str, rx_id: str, pharm_id: str):
    return client.post(
        f"/api/env/patient_portal/medications/{rx_id}/transfer",
        json={"session_id": sid, "pharmacy_id": pharm_id},
    )


def test_targets_have_expected_shape():
    """The seed exposes the discriminator + transfer-set targets the task needs."""
    _, _, targets, _ = _new_session()
    # New discriminator target: cheapest non-default retail pharmacy.
    assert targets["new_default_pharmacy_id"], targets
    # The cheapest retail must NOT be the original default and must NOT be mail-order.
    assert targets["new_default_pharmacy_id"] != targets["original_default_id"]
    assert targets["new_default_pharmacy_id"] != targets["mail_order_pharmacy_id"]
    # Transfer set is the active rxes currently at the old default.
    assert isinstance(targets["transfer_rx_ids"], list)
    assert len(targets["transfer_rx_ids"]) >= 1
    # Transfer set must be a strict subset of all active rxes (there are
    # distractor active rxes filled elsewhere that must NOT move).
    assert set(targets["transfer_rx_ids"]).issubset(set(targets["active_rx_ids"]))
    assert len(targets["transfer_rx_ids"]) < len(targets["active_rx_ids"])
    # Existing keys preserved for variant safety.
    assert "target_pharmacy_id" in targets
    assert "pharmacy_name" in targets
    assert "original_default_id" in targets


def test_correct_trajectory_evaluates_to_pass():
    """Correct solution via real endpoints passes via evaluate()."""
    client, sid, targets, state = _new_session()
    new_default = targets["new_default_pharmacy_id"]

    # 1) Set the lowest-fee retail pharmacy as the new default.
    r = _set_default(client, sid, new_default)
    assert r.status_code == 200, r.text

    # 2) Transfer every active rx at the previous default to the new default.
    for rx_id in targets["transfer_rx_ids"]:
        r = _transfer(client, sid, rx_id, new_default)
        assert r.status_code == 200, r.text

    task = get_task(TASK_ID)
    result = evaluate(task=task, server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"result: {result}"
    # Richer than a 2-check eval (bijection + 2 pharmacy updates + invariants).
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_wrong_destination_fails():
    """Setting the WRONG (more-expensive) retail pharmacy as default fails."""
    client, sid, targets, state = _new_session()
    correct = targets["new_default_pharmacy_id"]
    # Find a non-default, non-mail-order retail pharmacy that is NOT the cheapest.
    wrong = next(
        (
            p.id for p in state.pharmacies
            if not p.is_default and not p.is_mail_order and p.id != correct
        ),
        None,
    )
    assert wrong is not None, "expected a more-expensive retail decoy"

    r = _set_default(client, sid, wrong)
    assert r.status_code == 200, r.text
    for rx_id in targets["transfer_rx_ids"]:
        r = _transfer(client, sid, rx_id, wrong)
        assert r.status_code == 200, r.text

    task = get_task(TASK_ID)
    result = evaluate(task=task, server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"result: {result}"


def test_extra_side_effect_fails():
    """Correct default + transfer, but ALSO moving a frozen distractor rx fails."""
    client, sid, targets, state = _new_session()
    new_default = targets["new_default_pharmacy_id"]

    _set_default(client, sid, new_default)
    for rx_id in targets["transfer_rx_ids"]:
        _transfer(client, sid, rx_id, new_default)

    # Now move an active rx that is NOT in the transfer set (filled elsewhere).
    extra = next(
        (rid for rid in targets["active_rx_ids"] if rid not in targets["transfer_rx_ids"]),
        None,
    )
    assert extra is not None, "expected a distractor active rx outside the transfer set"
    r = _transfer(client, sid, extra, new_default)
    assert r.status_code == 200, r.text

    task = get_task(TASK_ID)
    result = evaluate(task=task, server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"result: {result}"


def test_partial_transfer_fails():
    """Setting default correctly but transferring only SOME of the set fails the bijection."""
    client, sid, targets, state = _new_session()
    new_default = targets["new_default_pharmacy_id"]

    _set_default(client, sid, new_default)
    # Transfer all but the last one — bijection must not saturate.
    for rx_id in targets["transfer_rx_ids"][:-1]:
        _transfer(client, sid, rx_id, new_default)

    task = get_task(TASK_ID)
    result = evaluate(task=task, server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"result: {result}"
