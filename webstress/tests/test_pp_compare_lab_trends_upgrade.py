"""Solvability proof for the hardened pp_compare_lab_trends task.

The upgraded task (re-tiered easy -> hard) requires the agent to:
  1. RE-DERIVE the most-recent-pair HbA1c trend direction from lab history
     (NOT stated in the instruction). The seed trend 6.9 -> 7.4 -> 8.1 is
     WORSENING (most recent pair 7.4 -> 8.1 increases) so the worsening branch
     (endocrinology) is the only satisfiable oneof branch.
  2. Book with the endocrinologist (gated on an APPROVED referral) at the
     earliest endocrinology slot, with reason exactly "HbA1c worsening review".
  3. Complete the two-step confirm workflow (endocrinology is in
     auto_confirm_specialties so the appointment lands pending and must be
     confirmed).
  4. Avoid touching prescriptions, messages, referrals (incl. the pending
     decoy referral), claims, etc.

The CORRECT path is driven through the REAL backend endpoints via the ASGI
app (TestClient): list labs/referrals/slots, create the endocrinology
appointment, then confirm it. A near-miss (PCP/improving branch) is asserted
to FAIL.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.security import CONTROLLER_SECRET_HEADER
from webstress.backend.state import SessionManager
from webstress.injector.middleware import clear_all_degradations
from webstress.runner import ensure_controller_secret
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_compare_lab_trends"
ENV = "patient_portal"


@pytest.fixture(autouse=True)
def _clean_degradation_state():
    clear_all_degradations()
    yield
    clear_all_degradations()


@pytest.fixture()
def client() -> TestClient:
    app.state.controller_secret = ensure_controller_secret()
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {CONTROLLER_SECRET_HEADER: app.state.controller_secret}


def _create(client: TestClient, seed: int = 42) -> dict:
    resp = client.post(
        f"/api/env/{ENV}/session",
        json={"task_id": TASK_ID, "seed": seed},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "resolved_targets" in data, "controller create must expose resolved_targets"
    return data


def _earliest_slot(client: TestClient, sid: str, provider_id: str) -> str:
    resp = client.get(
        f"/api/env/{ENV}/appointments/available-slots",
        params={"session_id": sid, "provider_id": provider_id},
    )
    assert resp.status_code == 200, resp.text
    slots = resp.json()["items"]
    assert slots, f"provider {provider_id} has no available slots"
    return min(s["datetime"] for s in slots)


# ---------------------------------------------------------------------------
# Correct solution — driven through the REAL backend endpoints.
# ---------------------------------------------------------------------------

def test_correct_trajectory_passes_via_real_endpoints(client: TestClient):
    session = _create(client)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    endo_ids = targets["endo_provider_ids"]
    endo_id = endo_ids[0]

    # Confirm the seeded trend is worsening (sanity: agent's derivation target).
    labs = client.get(
        f"/api/env/{ENV}/labs/trend/{targets['trend_test_name']}",
        params={"session_id": sid},
    ).json()["items"]
    resulted = [l for l in labs if l["status"] == "resulted"]
    resulted.sort(key=lambda l: l["collected_at"])
    assert float(resulted[-1]["value"]) > float(resulted[-2]["value"]), "seed must be worsening"

    # An approved endocrinology referral must exist so the gate accepts the booking.
    refs = client.get(f"/api/env/{ENV}/referrals", params={"session_id": sid}).json()["items"]
    assert any(r["to_specialty"] == "endocrinology" and r["status"] == "approved" for r in refs)

    earliest = _earliest_slot(client, sid, endo_id)

    # Create the endocrinology follow-up at the earliest slot.
    create_resp = client.post(
        f"/api/env/{ENV}/appointments/create",
        json={
            "session_id": sid,
            "provider_id": endo_id,
            "slot_datetime": earliest,
            "type": "in-person",
            "reason": "HbA1c worsening review",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    apt = create_resp.json()
    # endocrinology is in auto_confirm_specialties -> lands pending.
    assert apt["requires_confirmation"] is True
    assert apt["confirmation_state"] == "pending"

    # Two-step confirm.
    confirm_resp = client.post(
        f"/api/env/{ENV}/appointments/{apt['id']}/confirm",
        json={"session_id": sid},
    )
    assert confirm_resp.status_code == 200, confirm_resp.text
    assert confirm_resp.json()["confirmation_state"] == "confirmed"

    # Evaluate via the unified evaluator over the SAME session state.
    sm: SessionManager = app.state.session_manager
    state = sm.get(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(state.resolved_targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"


# ---------------------------------------------------------------------------
# Near-miss A — booked the PCP (improving branch) instead of endo. The seed is
# worsening, so the improving branch's provider expr is unsatisfiable and the
# worsening branch's provider expr rejects the PCP -> FAIL.
# ---------------------------------------------------------------------------

def test_wrong_provider_pcp_improving_branch_fails(client: TestClient):
    session = _create(client)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    pcp_id = targets["pcp_id"]

    earliest = _earliest_slot(client, sid, pcp_id)
    create_resp = client.post(
        f"/api/env/{ENV}/appointments/create",
        json={
            "session_id": sid,
            "provider_id": pcp_id,
            "slot_datetime": earliest,
            "type": "in-person",
            "reason": "HbA1c improving review",
        },
    )
    assert create_resp.status_code == 200, create_resp.text

    sm: SessionManager = app.state.session_manager
    state = sm.get(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(state.resolved_targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"PCP/improving branch should fail: {result}"


# ---------------------------------------------------------------------------
# Near-miss B — booked endo at the earliest slot with the right reason but
# never confirmed (confirmation_state stays pending) -> FAIL.
# ---------------------------------------------------------------------------

def test_endo_not_confirmed_fails(client: TestClient):
    session = _create(client)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    endo_id = targets["endo_provider_ids"][0]

    earliest = _earliest_slot(client, sid, endo_id)
    create_resp = client.post(
        f"/api/env/{ENV}/appointments/create",
        json={
            "session_id": sid,
            "provider_id": endo_id,
            "slot_datetime": earliest,
            "type": "in-person",
            "reason": "HbA1c worsening review",
        },
    )
    assert create_resp.status_code == 200, create_resp.text
    assert create_resp.json()["confirmation_state"] == "pending"
    # Intentionally do NOT confirm.

    sm: SessionManager = app.state.session_manager
    state = sm.get(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(state.resolved_targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"unconfirmed endo appt should fail: {result}"


# ---------------------------------------------------------------------------
# Near-miss C — correct endo booking + confirm, but the agent also mutated the
# pending decoy referral path by requesting a NEW referral (collateral damage
# on the referrals collection, guarded critical) -> FAIL.
# ---------------------------------------------------------------------------

def test_correct_but_requested_extra_referral_fails(client: TestClient):
    session = _create(client)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    endo_id = targets["endo_provider_ids"][0]

    earliest = _earliest_slot(client, sid, endo_id)
    apt = client.post(
        f"/api/env/{ENV}/appointments/create",
        json={
            "session_id": sid,
            "provider_id": endo_id,
            "slot_datetime": earliest,
            "type": "in-person",
            "reason": "HbA1c worsening review",
        },
    ).json()
    client.post(f"/api/env/{ENV}/appointments/{apt['id']}/confirm", json={"session_id": sid})

    # Collateral: request an unsolicited referral (forbidden by the instruction).
    ref_resp = client.post(
        f"/api/env/{ENV}/referrals/request",
        json={"session_id": sid, "to_specialty": "cardiology", "reason": "extra"},
    )
    assert ref_resp.status_code == 200, ref_resp.text

    sm: SessionManager = app.state.session_manager
    state = sm.get(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(state.resolved_targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"extra referral should fail: {result}"
