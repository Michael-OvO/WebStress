"""Solvability + difficulty proof for the upgraded pp_message_pcp task.

The upgraded task (medium tier) requires the agent to:
  1. Schedule exactly one appointment with the ASSIGNED PCP (target.pcp_id),
     at that PCP's own earliest available slot, with an exact reason.
  2. Complete the two-step confirmation workflow (auto_confirm_specialties=[pcp]
     lands the new appointment in confirmation_state="pending"; the agent must
     then POST /appointments/{id}/confirm to reach "confirmed").
  3. Touch nothing else — in particular not cancel/modify/confirm any of the
     pre-existing seeded appointments (now a critical, comprehensive invariant).

The correct path is driven entirely through the REAL backend mutation
endpoints via TestClient so the proof also confirms every gate is passable.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


def _create_session():
    # Reuse the app's own SessionManager so the real routes (which read it from
    # request.app.state) and the evaluator operate on the same state object.
    sm: SessionManager = app.state.session_manager
    sid, targets, _ = sm.create_session(
        env_id="patient_portal",
        task_id="pp_message_pcp",
        seed=42,
    )
    state = sm.get_state(sid)
    return sm, sid, dict(targets), state


def _earliest_pcp_slot_iso(state, pcp_id: str) -> str:
    pcp = next(p for p in state.providers if p.id == pcp_id)
    return min(s.datetime for s in pcp.available_slots).isoformat()


def test_correct_trajectory_through_real_endpoints_passes():
    """Schedule + confirm with the assigned PCP via real endpoints -> pass."""
    sm, sid, targets, state = _create_session()
    pcp_id = targets["pcp_id"]
    slot_iso = _earliest_pcp_slot_iso(state, pcp_id)

    # Confirm the difficulty preconditions actually hold for this seed.
    assert state.auto_confirm_specialties == ["pcp"], state.auto_confirm_specialties
    assert len([p for p in state.providers if p.specialty == "pcp"]) >= 2
    assert len(state.appointments) >= 3  # pre-existing rows the invariant guards

    client = TestClient(app)

    # Step 1: schedule with the assigned PCP at that PCP's earliest slot.
    create_resp = client.post(
        "/api/env/patient_portal/appointments/create",
        json={
            "session_id": sid,
            "provider_id": pcp_id,
            "slot_datetime": slot_iso,
            "type": "in-person",
            "reason": "Medication side effect review",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    new_apt = create_resp.json()
    # auto_confirm_specialties=[pcp] -> the new appointment must land pending.
    assert new_apt["confirmation_state"] == "pending", new_apt
    new_apt_id = new_apt["id"]

    # Step 2: complete the confirmation workflow.
    confirm_resp = client.post(
        f"/api/env/patient_portal/appointments/{new_apt_id}/confirm",
        json={"session_id": sid},
    )
    assert confirm_resp.status_code == 200, confirm_resp.text
    assert confirm_resp.json()["confirmation_state"] == "confirmed"

    # Evaluate against the canonical_diff.
    result = evaluate(
        task=get_task("pp_message_pcp"),
        server_state=sm.get_state(sid),
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, result
    # canonical_diff is richer than a 2-check eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_scheduled_but_not_confirmed_fails():
    """Skipping the confirmation step must fail (confirmation_state != confirmed)."""
    sm, sid, targets, state = _create_session()
    pcp_id = targets["pcp_id"]
    slot_iso = _earliest_pcp_slot_iso(state, pcp_id)

    client = TestClient(app)

    create_resp = client.post(
        "/api/env/patient_portal/appointments/create",
        json={
            "session_id": sid,
            "provider_id": pcp_id,
            "slot_datetime": slot_iso,
            "type": "in-person",
            "reason": "Medication side effect review",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    assert create_resp.json()["confirmation_state"] == "pending"
    # Deliberately DO NOT confirm.

    result = evaluate(
        task=get_task("pp_message_pcp"),
        server_state=sm.get_state(sid),
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"should fail without confirm: {result}"


def test_wrong_pcp_fails():
    """Booking with the OTHER PCP (not the assigned one) must fail."""
    sm, sid, targets, state = _create_session()
    pcp_id = targets["pcp_id"]
    other_pcp = next(
        p for p in state.providers if p.specialty == "pcp" and p.id != pcp_id
    )
    slot_iso = min(s.datetime for s in other_pcp.available_slots).isoformat()

    client = TestClient(app)

    create_resp = client.post(
        "/api/env/patient_portal/appointments/create",
        json={
            "session_id": sid,
            "provider_id": other_pcp.id,
            "slot_datetime": slot_iso,
            "type": "in-person",
            "reason": "Medication side effect review",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    new_apt_id = create_resp.json()["id"]
    client.post(
        f"/api/env/patient_portal/appointments/{new_apt_id}/confirm",
        json={"session_id": sid},
    )

    result = evaluate(
        task=get_task("pp_message_pcp"),
        server_state=sm.get_state(sid),
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"wrong PCP should fail: {result}"


def test_cancelling_existing_appointment_fails():
    """Correct booking + confirm, but ALSO cancelling a pre-existing appointment
    must fail the critical, comprehensive appointments invariant."""
    sm, sid, targets, state = _create_session()
    pcp_id = targets["pcp_id"]
    slot_iso = _earliest_pcp_slot_iso(state, pcp_id)
    existing_scheduled = next(
        a.id for a in state.appointments if a.status == "scheduled"
    )

    client = TestClient(app)

    create_resp = client.post(
        "/api/env/patient_portal/appointments/create",
        json={
            "session_id": sid,
            "provider_id": pcp_id,
            "slot_datetime": slot_iso,
            "type": "in-person",
            "reason": "Medication side effect review",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    new_apt_id = create_resp.json()["id"]
    client.post(
        f"/api/env/patient_portal/appointments/{new_apt_id}/confirm",
        json={"session_id": sid},
    )

    # Side-effect: cancel a pre-existing appointment.
    cancel_resp = client.post(
        f"/api/env/patient_portal/appointments/{existing_scheduled}/cancel",
        json={"session_id": sid},
    )
    assert cancel_resp.status_code == 200, cancel_resp.text

    result = evaluate(
        task=get_task("pp_message_pcp"),
        server_state=sm.get_state(sid),
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"cancelling existing apt should fail: {result}"
