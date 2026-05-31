"""Solvability proof for the hardened pp_provider_transition (frontier tier).

The upgraded task requires the agent to:
  1. Read the approved endocrinology referral and identify the SPECIFIC
     endocrinologist it names as its destination provider (to_provider_id) —
     not just "any endocrinologist accepting new patients". The seed now
     contains THREE endocrinologists, and the referral's named provider is
     frequently NOT the one with the globally-earliest slot, so a naive
     "earliest endo slot overall" strategy fails.
  2. Confirm that provider is accepting new patients.
  3. Book that exact provider's single earliest available slot, linked to the
     approved referral, with the exact reason string.
  4. Complete the two-step workflow: because endocrinology visits require
     confirmation, the new appointment must be CONFIRMED
     (confirmation_state == "confirmed").
  5. Touch nothing else (existing appointments, prescriptions, messages,
     referrals, immunizations, labs, claims).

The correct trajectory is driven through the REAL backend endpoints via a
Starlette TestClient sharing the SessionManager we created the session with
(so we can read the seed `targets` as the intended answer and confirm the
solution actually passes the create+confirm gates).
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

PREFIX = "/api/env/patient_portal"
TASK_ID = "pp_provider_transition"


def _setup():
    """Create a session via a fresh SessionManager wired into the app, so the
    TestClient drives the SAME state we can read targets / state from."""
    sm = SessionManager()
    app.state.session_manager = sm
    sid, targets, _ = sm.create_session(env_id="patient_portal", task_id=TASK_ID, seed=42)
    return sm, sid, dict(targets)


def _earliest_slot_for(state, provider_id: str) -> str:
    prov = next(p for p in state.providers if p.id == provider_id)
    return min(s.datetime.isoformat() for s in prov.available_slots)


def _book_via_endpoints(client, sid, provider_id, slot_iso, referral_id, *, confirm: bool):
    """Drive create (+ optional confirm) through the real REST endpoints."""
    create_resp = client.post(
        f"{PREFIX}/appointments/create",
        json={
            "session_id": sid,
            "provider_id": provider_id,
            "slot_datetime": slot_iso,
            "type": "in-person",
            "reason": "Endocrinology transfer of care",
            "linked_referral_id": referral_id,
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    apt_id = create_resp.json()["id"]
    if confirm:
        confirm_resp = client.post(
            f"{PREFIX}/appointments/{apt_id}/confirm",
            json={"session_id": sid},
        )
        assert confirm_resp.status_code == 200, confirm_resp.text
    return apt_id


def test_correct_trajectory_passes_via_endpoints():
    sm, sid, targets = _setup()
    state = sm.get_state(sid)

    target_prov = targets["referral_target_provider_id"]
    referral_id = targets["approved_ref_ids"][0]
    slot_iso = _earliest_slot_for(state, target_prov)

    # Sanity: the referral's named provider is one of the three endos and is
    # accepting new patients, and the pinned slot matches its earliest slot.
    assert target_prov in targets["endo_provider_ids"]
    assert slot_iso == targets["referral_target_slot"]

    with TestClient(app) as client:
        _book_via_endpoints(
            client, sid, target_prov, slot_iso, referral_id, confirm=True,
        )

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=sm.get_state(sid),
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # The richer canonical_diff has multiple positive + negative checks.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_wrong_provider_globally_earliest_endo_fails():
    """Booking the endo with the globally-earliest slot (the naive answer)
    instead of the referral's named provider must fail."""
    sm, sid, targets = _setup()
    state = sm.get_state(sid)

    target_prov = targets["referral_target_provider_id"]
    referral_id = targets["approved_ref_ids"][0]

    # Find an endo with an earlier earliest-slot than the referral's provider,
    # if one exists; otherwise pick a different endo entirely.
    endo_ids = list(targets["endo_provider_ids"])
    other_endos = [pid for pid in endo_ids if pid != target_prov]
    assert other_endos, "seed must have >1 endocrinologist for this near-miss"
    # Prefer the endo whose earliest slot is the globally-earliest (the trap).
    wrong_prov = min(other_endos, key=lambda pid: _earliest_slot_for(state, pid))
    wrong_slot = _earliest_slot_for(state, wrong_prov)

    with TestClient(app) as client:
        _book_via_endpoints(
            client, sid, wrong_prov, wrong_slot, referral_id, confirm=True,
        )

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=sm.get_state(sid),
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"wrong provider should fail: {result}"


def test_unconfirmed_appointment_fails():
    """Booking the correct provider/slot but skipping the confirm step must
    fail (the two-step confirmation workflow is required)."""
    sm, sid, targets = _setup()
    state = sm.get_state(sid)

    target_prov = targets["referral_target_provider_id"]
    referral_id = targets["approved_ref_ids"][0]
    slot_iso = _earliest_slot_for(state, target_prov)

    with TestClient(app) as client:
        _book_via_endpoints(
            client, sid, target_prov, slot_iso, referral_id, confirm=False,
        )

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=sm.get_state(sid),
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"unconfirmed appt should fail: {result}"


def test_wrong_slot_not_earliest_fails():
    """Booking the correct provider but a LATER (non-earliest) slot fails."""
    sm, sid, targets = _setup()
    state = sm.get_state(sid)

    target_prov = targets["referral_target_provider_id"]
    referral_id = targets["approved_ref_ids"][0]
    prov = next(p for p in state.providers if p.id == target_prov)
    slot_times = sorted(s.datetime.isoformat() for s in prov.available_slots)
    assert len(slot_times) >= 2, "seed must offer >1 slot for the target endo"
    later_slot = slot_times[-1]  # latest, definitely not the earliest

    with TestClient(app) as client:
        _book_via_endpoints(
            client, sid, target_prov, later_slot, referral_id, confirm=True,
        )

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=sm.get_state(sid),
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"wrong slot should fail: {result}"


def test_cancelling_existing_appointment_fails():
    """Correct booking but also cancelling a pre-existing appointment trips the
    critical 'do not modify existing appointments' invariant."""
    sm, sid, targets = _setup()
    state = sm.get_state(sid)

    target_prov = targets["referral_target_provider_id"]
    referral_id = targets["approved_ref_ids"][0]
    slot_iso = _earliest_slot_for(state, target_prov)
    existing_apt = targets["upcoming_ids"][0]

    with TestClient(app) as client:
        _book_via_endpoints(
            client, sid, target_prov, slot_iso, referral_id, confirm=True,
        )
        cancel_resp = client.post(
            f"{PREFIX}/appointments/{existing_apt}/cancel",
            json={"session_id": sid},
        )
        assert cancel_resp.status_code == 200, cancel_resp.text

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=sm.get_state(sid),
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"cancelling existing appt should fail: {result}"
