"""Solvability + near-miss proof for the hardened pp_insurance_plan_change task.

The upgraded task splits the patient's active prescriptions into a
maintenance subset (covered only via mail order under the new plan) and a
retail subset (must stay at the default pharmacy). It also requires the new
member ID / group number to be DERIVED from the current values rather than
transcribed from the instruction. This test drives the correct solution
through the real backend endpoints (TestClient) to confirm the gates are
passable, then asserts the canonical evaluator scores it as a pass; a
near-miss that over-transfers every rx to mail order must fail.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

API = "/api/env/patient_portal"


def _bootstrap():
    """Create a session on a fresh SessionManager wired into the app.

    Returns ``(client, sid, targets, state)``. The app's session_manager is
    swapped for our instance so TestClient mutations operate on the very
    session whose targets we hold.
    """
    sm = SessionManager()
    app.state.session_manager = sm
    sid, targets, _ = sm.create_session(
        env_id="patient_portal",
        task_id="pp_insurance_plan_change",
        seed=42,
    )
    client = TestClient(app)
    state = sm.get_state(sid)
    return client, sid, dict(targets), state


def _derived_insurance(state):
    ins = state.patient.insurance_plan
    member = "AET-" + ins.member_id.split("-")[1]
    group = "GRP-AET-" + ins.group_number.split("-")[1]
    return member, group


def test_correct_trajectory_evaluates_to_pass():
    client, sid, targets, state = _bootstrap()
    member_id, group_number = _derived_insurance(state)
    mail_order = targets["mail_order_pharmacy_id"]
    maintenance = targets["maintenance_rx_ids"]
    non_maintenance = targets["non_maintenance_rx_ids"]

    # Sanity: partition is non-trivial and disjoint.
    assert maintenance and non_maintenance
    assert set(maintenance).isdisjoint(set(non_maintenance))
    assert set(maintenance) | set(non_maintenance) == set(targets["active_rx_ids"])

    # 1) Update insurance to the DERIVED values via the real endpoint.
    r = client.post(
        f"{API}/profile/insurance",
        json={
            "session_id": sid,
            "plan_name": "Aetna PPO Silver",
            "member_id": member_id,
            "group_number": group_number,
        },
    )
    assert r.status_code == 200, r.text

    # 2) Set mail-order pharmacy as default.
    r = client.post(
        f"{API}/profile/pharmacy/{mail_order}/set-default",
        json={"session_id": sid},
    )
    assert r.status_code == 200, r.text

    # 3) Transfer ONLY the maintenance prescriptions to mail order.
    for rx_id in maintenance:
        r = client.post(
            f"{API}/medications/{rx_id}/transfer",
            json={"session_id": sid, "pharmacy_id": mail_order},
        )
        assert r.status_code == 200, r.text

    # 4) Leave non-maintenance prescriptions untouched (no calls).

    state = app.state.session_manager.get_state(sid)
    result = evaluate(
        task=get_task("pp_insurance_plan_change"),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score: {result.get('score')}"
    # canonical_diff is richer than a 2-check legacy eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_over_transfer_all_rx_evaluates_to_fail():
    """Transferring EVERY active rx (including retail) to mail order must fail."""
    client, sid, targets, state = _bootstrap()
    member_id, group_number = _derived_insurance(state)
    mail_order = targets["mail_order_pharmacy_id"]

    client.post(
        f"{API}/profile/insurance",
        json={
            "session_id": sid,
            "plan_name": "Aetna PPO Silver",
            "member_id": member_id,
            "group_number": group_number,
        },
    )
    client.post(
        f"{API}/profile/pharmacy/{mail_order}/set-default",
        json={"session_id": sid},
    )
    # Wrong: move ALL active rxes, not just the maintenance subset.
    for rx_id in targets["active_rx_ids"]:
        client.post(
            f"{API}/medications/{rx_id}/transfer",
            json={"session_id": sid, "pharmacy_id": mail_order},
        )

    state = app.state.session_manager.get_state(sid)
    result = evaluate(
        task=get_task("pp_insurance_plan_change"),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_verbatim_member_id_evaluates_to_fail():
    """Using a non-derived (transcribed) member ID must fail the insurance check."""
    client, sid, targets, state = _bootstrap()
    mail_order = targets["mail_order_pharmacy_id"]
    maintenance = targets["maintenance_rx_ids"]

    # Wrong: a plausible-but-not-derived member ID / group number.
    client.post(
        f"{API}/profile/insurance",
        json={
            "session_id": sid,
            "plan_name": "Aetna PPO Silver",
            "member_id": "AET-5529103",
            "group_number": "GRP-88914",
        },
    )
    client.post(
        f"{API}/profile/pharmacy/{mail_order}/set-default",
        json={"session_id": sid},
    )
    for rx_id in maintenance:
        client.post(
            f"{API}/medications/{rx_id}/transfer",
            json={"session_id": sid, "pharmacy_id": mail_order},
        )

    state = app.state.session_manager.get_state(sid)
    result = evaluate(
        task=get_task("pp_insurance_plan_change"),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"
