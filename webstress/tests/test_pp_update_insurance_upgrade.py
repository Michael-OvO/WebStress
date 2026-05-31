"""Solvability proof for the upgraded pp_update_insurance task.

The upgraded task moves the authoritative new-insurance values OUT of the
instruction and into the most-recent billing message body. The agent must open
that message ("New Insurance Card on File"), disambiguate it from an older
superseded billing message, extract the exact plan name / member ID / group
number plus new contact phone & email, and apply them via the real profile
endpoints.

This test drives the CORRECT solution through the real backend endpoints
(POST /profile/insurance and POST /profile/demographics) via the FastAPI
TestClient, then evaluates with the canonical_diff evaluator. It also proves a
near-miss (applying the STALE superseded card values) fails.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


def _create_session(client: TestClient) -> tuple[str, dict]:
    """Create a session via the REST endpoint and return (session_id, targets).

    Targets are read back from the app's own SessionManager so the test drives
    mutations against the same session the evaluator will read.
    """
    resp = client.post(
        "/api/env/patient_portal/session",
        json={"task_id": "pp_update_insurance", "seed": 42},
    )
    assert resp.status_code == 200, resp.text
    session_id = resp.json()["session_id"]
    targets = dict(app.state.session_manager.get_targets(session_id))
    return session_id, targets


def test_correct_trajectory_via_real_endpoints_passes():
    """Applying the most-recent-card values through the real routes passes."""
    client = TestClient(app)
    session_id, targets = _create_session(client)

    # Sanity: the new values are genuinely distinct from the stale decoy and
    # from the patient's seeded values (otherwise the task would be trivial).
    assert targets["new_plan_name"] != targets["stale_plan_name"]
    assert targets["new_plan_name"] != targets["old_plan_name"]
    assert targets["new_member_id"] != targets["stale_member_id"]
    assert targets["new_member_id"] != targets["old_member_id"]

    # 1) Update insurance plan/member/group to the NEW card values.
    r1 = client.post(
        "/api/env/patient_portal/profile/insurance",
        json={
            "session_id": session_id,
            "plan_name": targets["new_plan_name"],
            "member_id": targets["new_member_id"],
            "group_number": targets["new_group_number"],
        },
    )
    assert r1.status_code == 200, r1.text

    # 2) Update contact phone & email to the NEW contact details.
    r2 = client.post(
        "/api/env/patient_portal/profile/demographics",
        json={
            "session_id": session_id,
            "phone": targets["new_phone"],
            "email": targets["new_email"],
        },
    )
    assert r2.status_code == 200, r2.text

    state = app.state.session_manager.get_state(session_id)
    result = evaluate(
        task=get_task("pp_update_insurance"),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # canonical_diff is richer than the legacy 2-check eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_stale_card_values_near_miss_fails():
    """Applying the STALE (superseded) card's values must fail evaluation."""
    client = TestClient(app)
    session_id, targets = _create_session(client)

    # Apply the stale decoy's plan/member/group plus the correct contact info.
    # Choosing the wrong (superseded) insurance card is the intended trap.
    r1 = client.post(
        "/api/env/patient_portal/profile/insurance",
        json={
            "session_id": session_id,
            "plan_name": targets["stale_plan_name"],
            "member_id": targets["stale_member_id"],
            "group_number": targets["stale_group_number"],
        },
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/api/env/patient_portal/profile/demographics",
        json={
            "session_id": session_id,
            "phone": targets["new_phone"],
            "email": targets["new_email"],
        },
    )
    assert r2.status_code == 200, r2.text

    state = app.state.session_manager.get_state(session_id)
    result = evaluate(
        task=get_task("pp_update_insurance"),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"stale-card near-miss should fail: {result}"


def test_missing_contact_update_near_miss_fails():
    """Updating only insurance but forgetting the contact phone/email fails."""
    client = TestClient(app)
    session_id, targets = _create_session(client)

    r1 = client.post(
        "/api/env/patient_portal/profile/insurance",
        json={
            "session_id": session_id,
            "plan_name": targets["new_plan_name"],
            "member_id": targets["new_member_id"],
            "group_number": targets["new_group_number"],
        },
    )
    assert r1.status_code == 200, r1.text
    # Intentionally do NOT update phone/email.

    state = app.state.session_manager.get_state(session_id)
    result = evaluate(
        task=get_task("pp_update_insurance"),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"missing-contact near-miss should fail: {result}"
