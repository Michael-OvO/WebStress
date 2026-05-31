"""Solvability proof for the hardened pp_full_referral_chain task.

The upgraded task requires the agent to:
  1. Disambiguate FIVE neurology referrals and pick the single one that is
     approved AND pre-authorized (eligible_neuro_ref_id == ref_1).
  2. Book with the neurologist named on THAT referral
     (eligible_neuro_provider_id), not the other neurology provider whose
     slots may be earlier.
  3. Use that neurologist's EARLIEST open slot (computed min slot).
  4. Complete the two-step confirmation workflow (auto_confirm_specialties
     includes neurology, so the appointment lands pending and must be
     confirmed).
  5. Touch nothing else (referrals/messages/claims frozen).

The correct trajectory is driven through the REAL backend route handlers
(create_appointment + confirm_appointment) sharing the same SessionManager,
so it exercises the referral + prior-auth gate and the confirmation gate.
Several near-miss trajectories must fail.
"""

from __future__ import annotations

import pytest

from webstress.backend.routes.patient_portal import (
    CreateAppointmentRequest,
    SessionScopedRequest,
    confirm_appointment,
    create_appointment,
)
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_full_referral_chain"


def _new_session() -> tuple[SessionManager, str, dict, object]:
    sm = SessionManager()
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id=TASK_ID, seed=42
    )
    state = sm.get_state(sid)
    return sm, sid, dict(targets), state


def _earliest_slot_iso(state, provider_id: str) -> str:
    provider = state.get_provider(provider_id)
    assert provider is not None, f"provider {provider_id} not in seed"
    slots = sorted(s.datetime.isoformat() for s in provider.available_slots)
    assert slots, f"provider {provider_id} has no open slots"
    return slots[0]


def _book_correct(sm: SessionManager, sid: str, targets: dict, state) -> str:
    """Drive the canonical solution through the real backend routes."""
    provider_id = targets["eligible_neuro_provider_id"]
    ref_id = targets["eligible_neuro_ref_id"]
    earliest = _earliest_slot_iso(state, provider_id)

    created = create_appointment(
        CreateAppointmentRequest(
            session_id=sid,
            provider_id=provider_id,
            slot_datetime=earliest,
            type="in-person",
            reason="Neurology specialist consultation",
            linked_referral_id=ref_id,
        ),
        session_manager=sm,
    )
    apt_id = created["id"]
    # Two-step confirmation: neurology is in auto_confirm_specialties, so the
    # appointment lands pending and must be explicitly confirmed.
    assert created["confirmation_state"] == "pending"
    confirm_appointment(
        apt_id,
        SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )
    return apt_id


def _evaluate(state, targets: dict) -> dict:
    return evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )


# ---------------------------------------------------------------------------
# Seed shape sanity — the harder discriminators must actually exist.
# ---------------------------------------------------------------------------

def test_seed_has_multiple_neurology_referrals_with_one_eligible():
    _sm, _sid, targets, state = _new_session()
    neuro_refs = [r for r in state.referrals if r.to_specialty == "neurology"]
    assert len(neuro_refs) >= 4, "expected several neurology referrals to disambiguate"
    # Exactly one neurology referral clears the gate (approved + pre-auth ok).
    eligible = [
        r for r in neuro_refs
        if r.status == "approved"
        and (not r.prior_auth_required or r.prior_auth_status == "approved")
    ]
    assert len(eligible) == 1
    assert eligible[0].id == targets["eligible_neuro_ref_id"]
    assert targets["eligible_neuro_ref_ids"] == [targets["eligible_neuro_ref_id"]]
    # At least one decoy approved-but-pre-auth-pending neurology referral.
    assert targets["ineligible_approved_ref_ids"], "expected an ineligible approved decoy"
    # Two neurology providers so provider choice is non-trivial.
    neuro_providers = [p for p in state.providers if p.specialty == "neurology"]
    assert len(neuro_providers) == 2
    # auto-confirm wired so a confirmation step is required.
    assert "neurology" in state.auto_confirm_specialties


# ---------------------------------------------------------------------------
# Correct trajectory passes.
# ---------------------------------------------------------------------------

def test_correct_trajectory_passes():
    sm, sid, targets, state = _new_session()
    _book_correct(sm, sid, targets, state)
    result = _evaluate(state, targets)
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # Richer than a flat 2-check eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


# ---------------------------------------------------------------------------
# Near-miss trajectories fail.
# ---------------------------------------------------------------------------

def test_wrong_referral_linked_fails():
    """Linking the approved-but-pre-auth-pending neurology referral fails."""
    sm, sid, targets, state = _new_session()
    provider_id = targets["eligible_neuro_provider_id"]
    wrong_ref = targets["ineligible_approved_ref_ids"][0]
    earliest = _earliest_slot_iso(state, provider_id)
    # Backend gate still passes (first approved neurology ref is eligible), but
    # the agent links the WRONG referral id.
    created = create_appointment(
        CreateAppointmentRequest(
            session_id=sid,
            provider_id=provider_id,
            slot_datetime=earliest,
            type="in-person",
            reason="Neurology consultation",
            linked_referral_id=wrong_ref,
        ),
        session_manager=sm,
    )
    confirm_appointment(
        created["id"], SessionScopedRequest(session_id=sid), session_manager=sm
    )
    result = _evaluate(state, targets)
    assert result.get("success") is False, f"should fail on wrong referral: {result}"


def test_not_confirmed_fails():
    """Skipping the confirmation step leaves confirmation_state=pending -> fail."""
    sm, sid, targets, state = _new_session()
    provider_id = targets["eligible_neuro_provider_id"]
    ref_id = targets["eligible_neuro_ref_id"]
    earliest = _earliest_slot_iso(state, provider_id)
    create_appointment(
        CreateAppointmentRequest(
            session_id=sid,
            provider_id=provider_id,
            slot_datetime=earliest,
            type="in-person",
            reason="Neurology consultation",
            linked_referral_id=ref_id,
        ),
        session_manager=sm,
    )
    # Intentionally do NOT call confirm_appointment.
    result = _evaluate(state, targets)
    assert result.get("success") is False, f"should fail unconfirmed: {result}"


def test_non_earliest_slot_fails():
    """Booking the eligible provider/referral but a later slot fails the min-slot expr."""
    sm, sid, targets, state = _new_session()
    provider_id = targets["eligible_neuro_provider_id"]
    ref_id = targets["eligible_neuro_ref_id"]
    provider = state.get_provider(provider_id)
    slots = sorted(s.datetime.isoformat() for s in provider.available_slots)
    assert len(slots) >= 2
    later = slots[-1]
    created = create_appointment(
        CreateAppointmentRequest(
            session_id=sid,
            provider_id=provider_id,
            slot_datetime=later,
            type="in-person",
            reason="Neurology consultation",
            linked_referral_id=ref_id,
        ),
        session_manager=sm,
    )
    confirm_appointment(
        created["id"], SessionScopedRequest(session_id=sid), session_manager=sm
    )
    result = _evaluate(state, targets)
    assert result.get("success") is False, f"should fail non-earliest slot: {result}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
