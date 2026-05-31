"""Solvability + near-miss proof for the hardened (expert) pp_cross_reference_labs_meds.

The upgraded task requires the agent to:
  1. Re-derive the interacting medication pair and EVERY out-of-range resulted
     lab (flag in {abnormal, critical}) from the cabinet / lab panel.
  2. Book exactly ONE PCP appointment at the EARLIEST IN-PERSON slot (not the
     earliest slot overall, which could be telehealth) via the real
     POST /appointments/create route.
  3. CONFIRM that appointment (auto_confirm_specialties=[pcp] makes the new
     appointment land confirmation_state="pending") via the real
     POST /appointments/{id}/confirm route — a genuine two-step workflow.
  4. Use a reason that starts with the fixed prefix AND:
       - names both interacting medications verbatim (name + dosage),
       - for EVERY out-of-range resulted lab, quotes "<test_name> <value> <unit>"
         (unit omitted when the lab has no unit) — a re-derivation the agent
         must read off each lab result, not merely list test names, and
       - flags the single critical lab with the exact text
         "CRITICAL: <test_name>".
  5. Touch nothing else (prescriptions, messages, labs, claims, referrals,
     pre-existing appointments — including not confirming a pre-existing one).

The correct path is driven through the REAL backend endpoints
(starlette TestClient over webstress.app:app), which share the same
SessionManager used to mint the session/targets, so passing past the
auto-confirm + slot-availability gates is genuinely exercised.
"""

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_cross_reference_labs_meds"
_API = "/api/env/patient_portal"


def _make_session():
    """Mint a session on the APP's SessionManager so the TestClient routes and
    the evaluator both operate on the same state object."""
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(env_id="patient_portal", task_id=TASK_ID, seed=42)
    return sm, sid, dict(targets)


def _earliest_in_person_slot(state, pcp_id: str) -> str:
    pcp = next(p for p in state.providers if p.id == pcp_id)
    in_person = [s.datetime for s in pcp.available_slots if s.type == "in-person"]
    return min(in_person).isoformat()


def _build_reason(targets: dict, *, include_values=True, include_critical=True,
                  drop_last_value=False) -> str:
    """Construct a reason satisfying the hardened predicate.

    The canonical_diff requires the prefix, both medications, every
    "<test_name> <value> <unit>" value label, and "CRITICAL: <critical_name>".
    Flags let near-miss tests strip individual requirements.
    """
    meds = ", ".join(targets["interacting_medications"])
    value_labels = list(targets["abnormal_lab_value_labels"])
    if drop_last_value:
        value_labels = value_labels[:-1]
    if include_values:
        labs_part = "; out-of-range labs: " + ", ".join(value_labels)
    else:
        # Names only, no values — should NOT satisfy the value-label predicate.
        labs_part = "; out-of-range labs: " + ", ".join(targets["abnormal_lab_test_names"])
    crit_part = (
        f"; CRITICAL: {targets['critical_lab_test_name']}"
        if include_critical
        else ""
    )
    return (
        "Drug interaction and abnormal lab review: "
        f"interacting pair {meds}{labs_part}{crit_part}"
    )


def _create_and_confirm(client, sm, sid, pcp_id, slot_iso, reason, *, type_="in-person"):
    create_resp = client.post(
        f"{_API}/appointments/create",
        json={
            "session_id": sid,
            "provider_id": pcp_id,
            "slot_datetime": slot_iso,
            "type": type_,
            "reason": reason,
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    apt_id = create_resp.json()["id"]
    client.post(f"{_API}/appointments/{apt_id}/confirm", json={"session_id": sid})
    return apt_id


def test_correct_trajectory_via_real_endpoints_passes():
    sm, sid, targets = _make_session()
    state = sm.get_state(sid)
    pcp_id = targets["pcp_id"]
    slot_iso = _earliest_in_person_slot(state, pcp_id)
    reason = _build_reason(targets)

    client = TestClient(app)

    # Step 1: create the PCP appointment at the earliest in-person slot.
    create_resp = client.post(
        f"{_API}/appointments/create",
        json={
            "session_id": sid,
            "provider_id": pcp_id,
            "slot_datetime": slot_iso,
            "type": "in-person",
            "reason": reason,
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    apt_id = create_resp.json()["id"]

    # The auto_confirm_specialties=[pcp] opt-in means the new appointment is
    # NOT confirmed yet — proving the two-step workflow is real.
    pre_confirm = sm.get_state(sid).get_appointment(apt_id)
    assert pre_confirm.requires_confirmation is True
    assert pre_confirm.confirmation_state == "pending"

    # Step 2: confirm the appointment via the real confirm route.
    confirm_resp = client.post(
        f"{_API}/appointments/{apt_id}/confirm",
        json={"session_id": sid},
    )
    assert confirm_resp.status_code == 200, confirm_resp.text

    state = sm.get_state(sid)
    apt = state.get_appointment(apt_id)
    assert apt.confirmation_state == "confirmed"
    assert apt.type == "in-person"

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # Richer than legacy: create checks + invariant/constraint negatives.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_unconfirmed_appointment_fails():
    """Near-miss: correct slot + reason but the agent forgot the confirm step.

    confirmation_state stays 'pending', so the confirmation constraint fails.
    """
    sm, sid, targets = _make_session()
    state = sm.get_state(sid)
    pcp_id = targets["pcp_id"]
    slot_iso = _earliest_in_person_slot(state, pcp_id)
    reason = _build_reason(targets)

    client = TestClient(app)
    create_resp = client.post(
        f"{_API}/appointments/create",
        json={
            "session_id": sid,
            "provider_id": pcp_id,
            "slot_datetime": slot_iso,
            "type": "in-person",
            "reason": reason,
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    # Intentionally DO NOT confirm.

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False


def test_reason_with_names_only_no_values_fails():
    """Near-miss: agent books+confirms the correct in-person slot but only lists
    the lab TEST NAMES without each lab's resulted value+unit. The hardened
    value-label predicate is not satisfied, so the task fails."""
    sm, sid, targets = _make_session()
    state = sm.get_state(sid)
    pcp_id = targets["pcp_id"]
    slot_iso = _earliest_in_person_slot(state, pcp_id)
    reason = _build_reason(targets, include_values=False)

    client = TestClient(app)
    _create_and_confirm(client, sm, sid, pcp_id, slot_iso, reason)

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False


def test_reason_missing_critical_flag_fails():
    """Near-miss: agent quotes every value label but never flags the critical
    lab with 'CRITICAL: <name>'. The critical-flag predicate fails."""
    sm, sid, targets = _make_session()
    state = sm.get_state(sid)
    pcp_id = targets["pcp_id"]
    slot_iso = _earliest_in_person_slot(state, pcp_id)
    reason = _build_reason(targets, include_critical=False)

    client = TestClient(app)
    _create_and_confirm(client, sm, sid, pcp_id, slot_iso, reason)

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False


def test_telehealth_slot_and_partial_reason_fails():
    """Near-miss: agent books the earliest slot but as telehealth and drops one
    value label. Both the in-person/telehealth constraints and the value-label
    predicate fail, so the task fails even after confirming."""
    sm, sid, targets = _make_session()
    state = sm.get_state(sid)
    pcp_id = targets["pcp_id"]
    pcp = next(p for p in state.providers if p.id == pcp_id)
    tele_slots = [s.datetime for s in pcp.available_slots if s.type == "telehealth"]
    assert tele_slots, "seed should contain at least one telehealth slot"
    tele_iso = min(tele_slots).isoformat()

    partial_reason = _build_reason(targets, drop_last_value=True)

    client = TestClient(app)
    _create_and_confirm(client, sm, sid, pcp_id, tele_iso, partial_reason, type_="telehealth")

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False
