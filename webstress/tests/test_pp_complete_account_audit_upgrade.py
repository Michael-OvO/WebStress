"""Solvability + near-miss proof for the hardened pp_complete_account_audit.

The frontier upgrade requires the agent to:
  * audit the account and schedule EXACTLY ONE in-person Administration
    (front-desk) appointment,
  * at the single EARLIEST in-person slot across ALL admin providers
    (datetime, then provider-id tie-break) — a cross-provider min the agent
    must re-derive, not just read one provider's slot list,
  * with reason exactly "Account audit review", booked during the session,
  * while freezing every other collection (claims/referrals/prescriptions/
    messages/immunizations/labs/pharmacies/pre-existing appointments) and
    NOT requesting any new referral.

The CORRECT solution is driven through the REAL backend
``POST /appointments/create`` endpoint (exercising the admin-provider
no-referral path + slot consumption) using the seeded ``resolved_targets``
as the intended answer, then graded via the unified evaluator. Two
near-miss trajectories (wrong in-person slot; correct slot + forbidden
referral request) are asserted to fail.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.runner import controller_headers, ensure_controller_secret
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_complete_account_audit"
ENV = "/api/env/patient_portal"
SEED = 42


@pytest.fixture()
def client() -> TestClient:
    app.state.controller_secret = ensure_controller_secret()
    return TestClient(app)


def _session(client: TestClient, seed: int = SEED) -> dict:
    resp = client.post(
        f"{ENV}/session",
        json={"task_id": TASK_ID, "seed": seed},
        headers=controller_headers(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "resolved_targets" in data, "controller access did not expose resolved_targets"
    return data


def _create_appt(client: TestClient, sid: str, *, provider_id: str, slot_datetime: str,
                 type_: str = "in-person", reason: str = "Account audit review") -> int:
    resp = client.post(
        f"{ENV}/appointments/create",
        json={
            "session_id": sid,
            "provider_id": provider_id,
            "slot_datetime": slot_datetime,
            "type": type_,
            "reason": reason,
        },
    )
    return resp.status_code


def _evaluate(client: TestClient, sid: str) -> dict:
    resp = client.post(
        f"{ENV}/evaluate",
        json={"session_id": sid, "task_id": TASK_ID, "trajectory": []},
        headers=controller_headers(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Positive: the seeded targets ARE achievable past the real endpoint + gates.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [42, 1, 7, 2024, 999])
def test_correct_solution_passes_via_real_endpoint(client: TestClient, seed: int) -> None:
    data = _session(client, seed)
    sid = data["session_id"]
    targets = data["resolved_targets"]

    status = _create_appt(
        client, sid,
        provider_id=targets["audit_admin_provider_id"],
        slot_datetime=targets["audit_slot_datetime"],
        type_="in-person",
        reason="Account audit review",
    )
    assert status == 200, f"create_appointment failed (seed={seed}): {status}"

    result = _evaluate(client, sid)
    assert result.get("success") is True, f"seed={seed} result={result}"
    assert result.get("score", 0.0) >= 0.99, f"seed={seed} score={result.get('score')}"
    # Frontier task carries a rich set of positive + negative checks.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 5


def test_correct_solution_passes_via_direct_evaluate() -> None:
    """Mirror of the reference smoke test: drive evaluate() directly off the
    seeded SessionManager state to confirm achievability independent of REST.

    METHOD: the appointment row is appended directly to state to mirror the
    POST /appointments/create endpoint (admin providers bypass the referral
    gate), since SessionManager.create_session does not return a live REST
    session; the parametrized test above proves the REAL endpoint path.
    """
    from webstress.backend.models.patient_portal import Appointment
    from webstress.backend.models.base import utc_now

    sm = SessionManager()
    sid, targets, _ = sm.create_session(env_id="patient_portal", task_id=TASK_ID, seed=SEED)
    state = sm.get_state(sid)

    state.appointments.append(Appointment(
        id="apt_audit_new",
        provider_id=targets["audit_admin_provider_id"],
        datetime=datetime.fromisoformat(targets["audit_slot_datetime"]),
        type="in-person",
        status="scheduled",
        reason="Account audit review",
        booked_at=utc_now(),
        location="Main Campus",
    ))

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, result
    assert result.get("score", 0.0) >= 0.99


# ---------------------------------------------------------------------------
# Negative: near-miss trajectories must FAIL.
# ---------------------------------------------------------------------------

def test_wrong_inperson_slot_fails(client: TestClient) -> None:
    """Booking a LATER in-person admin slot (not the cross-provider earliest)
    fails the computed datetime predicate."""
    data = _session(client)
    sid = data["session_id"]
    targets = data["resolved_targets"]
    admin = targets["admin_provider_ids"]
    target_prov = targets["audit_admin_provider_id"]
    target_dt = targets["audit_slot_datetime"]

    providers = client.get(f"{ENV}/providers", params={"session_id": sid}).json()["items"]
    wrong: tuple[str, str] | None = None
    for p in providers:
        if p["id"] not in admin:
            continue
        for s in p["available_slots"]:
            if s["type"] != "in-person":
                continue
            dt_iso = datetime.fromisoformat(s["datetime"]).isoformat()
            if not (p["id"] == target_prov and dt_iso == target_dt):
                wrong = (p["id"], s["datetime"])
                break
        if wrong:
            break
    assert wrong is not None, "seed produced no alternative in-person admin slot to mis-book"

    status = _create_appt(client, sid, provider_id=wrong[0], slot_datetime=wrong[1])
    assert status == 200
    result = _evaluate(client, sid)
    assert result.get("success") is False
    assert result.get("score", 1.0) < 0.99


def test_correct_slot_plus_forbidden_referral_fails(client: TestClient) -> None:
    """Booking the right slot but ALSO requesting a new referral trips the
    critical 'no new referral' constraint."""
    data = _session(client)
    sid = data["session_id"]
    targets = data["resolved_targets"]

    status = _create_appt(
        client, sid,
        provider_id=targets["audit_admin_provider_id"],
        slot_datetime=targets["audit_slot_datetime"],
    )
    assert status == 200
    ref_resp = client.post(
        f"{ENV}/referrals/request",
        json={"session_id": sid, "to_specialty": "cardiology", "reason": "audit follow-up"},
    )
    assert ref_resp.status_code == 200

    result = _evaluate(client, sid)
    assert result.get("success") is False
    assert result.get("score", 1.0) < 0.99
