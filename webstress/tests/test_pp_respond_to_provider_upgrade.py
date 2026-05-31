"""Solvability proof + near-miss for the upgraded pp_respond_to_provider task.

The medium->hard upgrade adds three genuine discriminators on top of the
original "read the bp message + book the PCP follow-up" task:

  1. Two distinct PCPs exist (prov_1 = assigned PCP / message sender, plus a
     same-specialty decoy). The agent must book with the message *sender*
     (target.pcp_id == prov_1), not "any PCP".
  2. The booked slot must be the assigned PCP's earliest IN-PERSON slot, and
     the appointment must be in-person (telehealth fails).
  3. New PCP appointments land in confirmation_state="pending"
     (auto_confirm_specialties: [pcp]); the agent must also call
     POST /appointments/{id}/confirm so the appointment becomes "confirmed".

The CORRECT solution is driven through the REAL backend route handlers
(mark_message_read, create_appointment, confirm_appointment) so the slot-match
and two-step confirmation gates are actually exercised — not bypassed by direct
state mutation. Direct state mutation is used only in the wrong/near-miss case
to fabricate a deliberately-broken final state.
"""

from __future__ import annotations

from webstress.backend.routes.patient_portal import (
    CancelAppointmentRequest,  # noqa: F401  (kept for parity / future use)
    CreateAppointmentRequest,
    SessionScopedRequest,
    confirm_appointment,
    create_appointment,
    mark_message_read,
)
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_respond_to_provider"


def _earliest_in_person_slot(state, provider_id: str) -> str:
    provider = next(p for p in state.providers if p.id == provider_id)
    slots = [s.datetime for s in provider.available_slots if s.type == "in-person"]
    assert slots, f"provider {provider_id} has no in-person slot"
    return min(slots).isoformat()


def _new_session():
    sm = SessionManager()
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id=TASK_ID, seed=42
    )
    return sm, sid, dict(targets)


def test_seed_shape_has_two_pcps_and_confirmation_gate():
    """Sanity-check the discriminators the upgrade depends on actually exist."""
    sm, sid, targets = _new_session()
    state = sm.get_state(sid)

    # Two distinct PCPs; the assigned PCP is prov_1 and is the bp message sender.
    pcp_ids = [p.id for p in state.providers if p.specialty == "pcp"]
    assert len(pcp_ids) >= 2, f"expected >=2 PCPs, got {pcp_ids}"
    assert targets["pcp_id"] in pcp_ids
    assert set(targets["pcp_provider_ids"]) == set(pcp_ids)

    bp_msg = state.get_message(targets["bp_msg_id"])
    assert bp_msg is not None
    assert bp_msg.provider_id == targets["pcp_id"], "bp message must be from assigned PCP"
    assert bp_msg.is_read is False, "bp message must start unread"

    # The confirmation workflow is armed for PCP appointments.
    assert "pcp" in (state.auto_confirm_specialties or [])


def test_correct_trajectory_evaluates_to_pass():
    """Drive the correct solution through the real backend endpoints -> pass."""
    sm, sid, targets = _new_session()
    state = sm.get_state(sid)

    pcp_id = targets["pcp_id"]
    slot_iso = _earliest_in_person_slot(state, pcp_id)

    # 1) Read the bp message via the real endpoint.
    mark_message_read(
        targets["bp_msg_id"],
        SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )

    # 2) Schedule the in-person follow-up with the assigned PCP at the earliest
    #    in-person slot, with the exact required reason. The real route
    #    consumes the slot and (because pcp is in auto_confirm_specialties)
    #    lands the appointment in confirmation_state="pending".
    created = create_appointment(
        CreateAppointmentRequest(
            session_id=sid,
            provider_id=pcp_id,
            slot_datetime=slot_iso,
            type="in-person",
            reason="Blood pressure medication adjustment follow-up",
        ),
        session_manager=sm,
    )
    apt_id = created["id"]
    assert created["confirmation_state"] == "pending", (
        "seed should require confirmation for PCP appointments"
    )

    # 3) Confirm the appointment via the real two-step endpoint.
    confirmed = confirm_appointment(
        apt_id,
        SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )
    assert confirmed["confirmation_state"] == "confirmed"

    state = sm.get_state(sid)
    task = get_task(TASK_ID)
    result = evaluate(
        task=task,
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"result: {result}"
    # The upgraded diff is richer than a 2-check eval (read + create +
    # confirmation + provider identity + earliest-in-person + invariants).
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_unconfirmed_appointment_fails():
    """Scheduling without confirming (the most tempting shortcut) must fail."""
    sm, sid, targets = _new_session()
    state = sm.get_state(sid)
    pcp_id = targets["pcp_id"]
    slot_iso = _earliest_in_person_slot(state, pcp_id)

    mark_message_read(
        targets["bp_msg_id"], SessionScopedRequest(session_id=sid), session_manager=sm
    )
    created = create_appointment(
        CreateAppointmentRequest(
            session_id=sid,
            provider_id=pcp_id,
            slot_datetime=slot_iso,
            type="in-person",
            reason="Blood pressure medication adjustment follow-up",
        ),
        session_manager=sm,
    )
    # Deliberately DO NOT confirm.
    assert created["confirmation_state"] == "pending"

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"unconfirmed should fail: {result}"


def test_wrong_pcp_fails():
    """Booking the decoy PCP (not the message sender) must fail."""
    sm, sid, targets = _new_session()
    state = sm.get_state(sid)

    pcp_id = targets["pcp_id"]
    wrong_pcp = next(
        pid for pid in targets["pcp_provider_ids"] if pid != pcp_id
    )
    wrong_slot = _earliest_in_person_slot(state, wrong_pcp)

    mark_message_read(
        targets["bp_msg_id"], SessionScopedRequest(session_id=sid), session_manager=sm
    )
    created = create_appointment(
        CreateAppointmentRequest(
            session_id=sid,
            provider_id=wrong_pcp,
            slot_datetime=wrong_slot,
            type="in-person",
            reason="Blood pressure medication adjustment follow-up",
        ),
        session_manager=sm,
    )
    confirm_appointment(
        created["id"], SessionScopedRequest(session_id=sid), session_manager=sm
    )

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"wrong PCP should fail: {result}"
