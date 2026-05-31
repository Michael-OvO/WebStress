"""Solvability proof for the hardened ``pp_dispute_claim`` task.

The upgraded task asks the agent to appeal the *2 most-recent eligible* denied
claims (denied + EOB available + appeal deadline not passed), ordered by service
date descending (claim-id descending tiebreak), and to leave every other claim
untouched. This module proves:

  1. The intended answer (seed target ``recent_appealable_claim_ids``) is
     achievable through the REAL backend appeal endpoint past its guards
     (status==denied, eob_available, deadline>=now), and that driving exactly
     that set makes the canonical_diff evaluator pass with score >= 0.99.
  2. Near-miss trajectories fail: (a) appealing only ONE of the two required
     claims (under-action breaks the bijection), and (b) appealing an
     ineligible / lower-recency denied claim (over-action trips the critical
     out-of-scope invariant).

Method: the correct solution is driven through the real HTTP endpoint
``POST /api/env/patient_portal/claims/{id}/appeal`` via starlette TestClient,
sharing the app's SessionManager so the post-appeal state is the one evaluated.
Near-miss cases mirror the same endpoint effect (status->appealed) to keep the
proof fast and deterministic.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


def _make_session() -> tuple[SessionManager, str, dict]:
    """Create a pp_dispute_claim session on a fresh SessionManager.

    The same manager is installed on the TestClient app so endpoint mutations
    and the evaluator read identical state.
    """
    sm = SessionManager()
    sid, targets, _ = sm.create_session(
        env_id="patient_portal",
        task_id="pp_dispute_claim",
        seed=42,
    )
    return sm, sid, dict(targets)


def test_correct_trajectory_via_real_appeal_endpoint_passes() -> None:
    """Appealing exactly the 2 most-recent eligible denied claims passes."""
    sm, sid, targets = _make_session()
    recent = list(targets["recent_appealable_claim_ids"])
    assert len(recent) == 2, f"expected 2 recent appealable claims, got {recent}"

    # Sanity: the intended answer is a strict subset of denied claims and
    # excludes every ineligible denied claim (the decoys).
    assert set(recent).issubset(set(targets["denied_claim_ids"]))
    assert not set(recent) & set(targets["ineligible_denied_ids"])

    app.state.session_manager = sm
    client = TestClient(app)

    for clm_id in recent:
        resp = client.post(
            f"/api/env/patient_portal/claims/{clm_id}/appeal",
            json={"session_id": sid, "reason": "Documentation submitted for reconsideration."},
        )
        assert resp.status_code == 200, f"appeal {clm_id} failed: {resp.text}"
        assert resp.json()["status"] == "appealed"

    state = sm.get_state(sid)
    appealed = {c.id for c in state.claims if c.status == "appealed"}
    assert appealed == set(recent), f"appealed set {appealed} != target {set(recent)}"

    result = evaluate(
        task=get_task("pp_dispute_claim"),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # canonical_diff richness: bijection update + many invariants.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_partial_appeal_under_action_fails() -> None:
    """Appealing only one of the two required claims fails the bijection."""
    sm, sid, targets = _make_session()
    recent = list(targets["recent_appealable_claim_ids"])

    app.state.session_manager = sm
    client = TestClient(app)

    # Only appeal the first of the two required claims.
    resp = client.post(
        f"/api/env/patient_portal/claims/{recent[0]}/appeal",
        json={"session_id": sid, "reason": "Partial."},
    )
    assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task("pp_dispute_claim"),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"under-action should fail: {result}"


def test_over_action_on_ineligible_claim_fails() -> None:
    """Appealing the right 2 PLUS an out-of-scope denied claim trips invariants.

    The ineligible denied claims (past-deadline / no-EOB) cannot be appealed via
    the endpoint (the guards reject them with 422), so over-action is mirrored by
    directly flipping an out-of-scope denied claim's status to 'appealed' — the
    exact collateral damage the critical invariant must catch.
    """
    sm, sid, targets = _make_session()
    recent = list(targets["recent_appealable_claim_ids"])

    app.state.session_manager = sm
    client = TestClient(app)

    for clm_id in recent:
        resp = client.post(
            f"/api/env/patient_portal/claims/{clm_id}/appeal",
            json={"session_id": sid, "reason": "Reconsideration."},
        )
        assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)

    # The endpoint refuses ineligible claims (proving the guard is real)...
    ineligible_id = targets["ineligible_denied_ids"][0]
    bad = client.post(
        f"/api/env/patient_portal/claims/{ineligible_id}/appeal",
        json={"session_id": sid, "reason": "Should be rejected."},
    )
    assert bad.status_code == 422, f"ineligible appeal should 422: {bad.text}"

    # ...so mirror the collateral damage directly to exercise the invariant.
    extra = state.get_claim(ineligible_id)
    extra.status = "appealed"

    result = evaluate(
        task=get_task("pp_dispute_claim"),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"over-action should fail: {result}"


def test_wrong_recency_set_fails() -> None:
    """Appealing two ELIGIBLE-but-not-most-recent claims fails the bijection.

    All four appealable claims are valid appeal targets at the endpoint, but only
    the two most recent are the correct answer. Appealing the older two must fail
    (wrong positive set + the right set left un-appealed).
    """
    sm, sid, targets = _make_session()
    recent = set(targets["recent_appealable_claim_ids"])
    older_eligible = [c for c in targets["appealable_claim_ids"] if c not in recent]
    assert len(older_eligible) >= 2, f"need >=2 older eligible, got {older_eligible}"

    app.state.session_manager = sm
    client = TestClient(app)

    for clm_id in older_eligible[:2]:
        resp = client.post(
            f"/api/env/patient_portal/claims/{clm_id}/appeal",
            json={"session_id": sid, "reason": "Wrong recency."},
        )
        assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task("pp_dispute_claim"),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"wrong-recency set should fail: {result}"
