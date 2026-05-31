"""Solvability proof for the upgraded (medium-tier) pp_update_phone task.

The upgraded task converts the easy single-field phone update into a
multi-field contact-information update: phone + email + emergency contact
(name/phone/relationship), all written through the ONLY legitimate endpoint
(POST /profile/demographics), with every other patient singleton field frozen
and a wall of sibling-collection invariants plus disambiguation constraints.

This test drives the CORRECT solution through the real backend mutation
endpoint via TestClient (so it also confirms the action clears every gate),
then evaluates with the unified evaluator. It also asserts that several
wrong/near-miss trajectories fail.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


TASK_ID = "pp_update_phone"


def _new_session() -> tuple[TestClient, str, dict]:
    """Create a session inside the APP's session manager and return targets."""
    client = TestClient(app)
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id=TASK_ID, seed=42
    )
    return client, sid, dict(targets)


def _evaluate(sid: str, targets: dict) -> dict:
    sm = app.state.session_manager
    state = sm.get_state(sid)
    return evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=targets,
        trajectory=[],
    )


def _post_demographics(client: TestClient, sid: str, body: dict) -> None:
    resp = client.post(
        "/api/env/patient_portal/profile/demographics",
        json={"session_id": sid, **body},
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Correct solution
# ---------------------------------------------------------------------------

def test_correct_trajectory_passes() -> None:
    client, sid, targets = _new_session()

    # Single legitimate call updating exactly the three contact field groups.
    _post_demographics(
        client,
        sid,
        {
            "phone": targets["new_phone"],
            "email": targets["new_email"],
            "emergency_contact": {
                "name": targets["ec_name"],
                "phone": targets["ec_new_phone"],
                "relationship": targets["ec_relationship"],
            },
        },
    )

    result = _evaluate(sid, targets)
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # Richer than the legacy 1-check eval: at least the update + constraints.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


# ---------------------------------------------------------------------------
# Wrong / near-miss trajectories must FAIL
# ---------------------------------------------------------------------------

def test_phone_only_partial_fails() -> None:
    """Updating only the phone (the OLD easy answer) is now insufficient."""
    client, sid, targets = _new_session()
    _post_demographics(client, sid, {"phone": targets["new_phone"]})
    result = _evaluate(sid, targets)
    assert result.get("success") is False, f"phone-only should fail: {result}"


def test_cross_wired_phones_fail() -> None:
    """Cross-wiring the patient phone with the emergency-contact phone fails."""
    client, sid, targets = _new_session()
    _post_demographics(
        client,
        sid,
        {
            # Patient phone accidentally set to the EC number (cross-wire).
            "phone": targets["ec_new_phone"],
            "email": targets["new_email"],
            "emergency_contact": {
                "name": targets["ec_name"],
                "phone": targets["ec_new_phone"],
                "relationship": targets["ec_relationship"],
            },
        },
    )
    result = _evaluate(sid, targets)
    assert result.get("success") is False, f"cross-wired should fail: {result}"


def test_collateral_message_side_effect_fails() -> None:
    """A correct contact update PLUS an unsolicited message must fail.

    Over-acting (sending a message) trips the high-severity messages invariant
    even though the primary update is correct.
    """
    client, sid, targets = _new_session()
    _post_demographics(
        client,
        sid,
        {
            "phone": targets["new_phone"],
            "email": targets["new_email"],
            "emergency_contact": {
                "name": targets["ec_name"],
                "phone": targets["ec_new_phone"],
                "relationship": targets["ec_relationship"],
            },
        },
    )
    # Send an unsolicited message to a real provider.
    sm = app.state.session_manager
    state = sm.get_state(sid)
    provider_id = state.providers[0].id
    resp = client.post(
        "/api/env/patient_portal/messages/send",
        json={
            "session_id": sid,
            "provider_id": provider_id,
            "subject": "Updated my contact info",
            "body": "FYI I changed my phone and email.",
        },
    )
    assert resp.status_code == 200, resp.text

    result = _evaluate(sid, targets)
    assert result.get("success") is False, f"collateral message should fail: {result}"
