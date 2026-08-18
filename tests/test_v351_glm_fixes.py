# test file for v3.5.1-glm fixes
"""
Tests for LAO v3.5.1-glm fixes.
Covers R3, R4, R5, R1, A1-A3 acceptance criteria.
All network calls are mocked — no real external requests.

Run: python3 -m pytest tests/test_v351_glm_fixes.py -v
"""

import json
import time
from unittest.mock import patch, MagicMock

import pytest

from lao.effect_anchored.routing.model_router import ModelRouter, RouteSelection
from lao.effect_anchored.optimization.detector import AnomalyDetector
from lao.effect_anchored.feedback_bus import FeedbackBus, FeedbackEvent
from lao.effect_anchored.validation.fixture_pair import (
    FixturePair,
    FixturePairValidator,
    replay_pairs,
)
from lao.effect_anchored.cognitive_anchor import make_decision_anchor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_urlopen_response(model_ids):
    """Build a mock urlopen context-manager returning a /models payload."""
    payload = json.dumps({"data": [{"id": mid} for mid in model_ids]}).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = payload
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _make_anchor(anchor_id="test-anchor"):
    return make_decision_anchor(
        anchor_id=anchor_id,
        principle="test principle",
        trigger_condition="test trigger",
        action_rule="test action",
    )


# ---------------------------------------------------------------------------
# R3 — ModelRouter._verify_model_exists()
# ---------------------------------------------------------------------------

class TestR3VerifyModelExists:
    """R3: cross-provider model existence verification with TTL cache."""

    def setup_method(self):
        # 实例级缓存隔离(缓存为实例属性·非类级)
        self._router = ModelRouter()
        self._router._model_cache.clear()

    def test_known_model_returns_true(self):
        """Mock urlopen returns a model list containing the target → True."""
        router = ModelRouter()
        resp = _mock_urlopen_response(["deepseek-v4-pro", "deepseek-v4-flash"])
        with patch("urllib.request.urlopen", return_value=resp):
            result = router._verify_model_exists("deepseek", "deepseek-v4-pro")
        assert result is True

    def test_unreachable_returns_false(self):
        """urlopen raises → False (fail-open, no exception propagated)."""
        router = ModelRouter()
        with patch("urllib.request.urlopen", side_effect=ConnectionError("unreachable")):
            result = router._verify_model_exists("deepseek", "deepseek-v4-pro")
        assert result is False

    def test_ttl_cache_no_repeat_request(self):
        """Second call within TTL window does not invoke urlopen again."""
        router = ModelRouter()
        resp = _mock_urlopen_response(["deepseek-v4-pro"])
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            r1 = router._verify_model_exists("deepseek", "deepseek-v4-pro")
            assert r1 is True
            assert mock_open.call_count == 1

            r2 = router._verify_model_exists("deepseek", "deepseek-v4-pro")
            assert r2 is True
            assert mock_open.call_count == 1  # cache hit — no new request

    def test_unknown_provider_returns_false(self):
        """Provider not in PROVIDER_BASE_URLS → False without network."""
        router = ModelRouter()
        with patch("urllib.request.urlopen") as mock_open:
            result = router._verify_model_exists("nonexistent_provider", "any-model")
        assert result is False
        mock_open.assert_not_called()


# ---------------------------------------------------------------------------
# R4 — AnomalyDetector._usage_missing()
# ---------------------------------------------------------------------------

class TestR4UsageMissing:
    """R4: usage-field-absence anomaly detection."""

    def test_single_missing_mid_severity(self):
        """usage_missing_count=1 → detected=True, severity='mid'."""
        anomaly = AnomalyDetector()._usage_missing({"usage_missing_count": 1})
        assert anomaly.detected is True
        assert anomaly.severity == "mid"

    def test_three_missing_high_severity(self):
        """usage_missing_count=3 → detected=True, severity='high'."""
        anomaly = AnomalyDetector()._usage_missing({"usage_missing_count": 3})
        assert anomaly.detected is True
        assert anomaly.severity == "high"

    def test_responses_without_usage_triggers(self):
        """responses_without_usage=1 alone → detected=True."""
        anomaly = AnomalyDetector()._usage_missing({"responses_without_usage": 1})
        assert anomaly.detected is True

    def test_both_zero_not_detected(self):
        """Both signals 0 → detected=False, severity='none'."""
        anomaly = AnomalyDetector()._usage_missing(
            {"usage_missing_count": 0, "responses_without_usage": 0}
        )
        assert anomaly.detected is False
        assert anomaly.severity == "none"


# ---------------------------------------------------------------------------
# R5 — SwitchAuditor wiring in ModelRouter.route()
# ---------------------------------------------------------------------------

class TestR5SwitchAuditor:
    """R5: route() records agent_binding switches; audit failure is non-blocking."""

    def _make_router(self):
        router = ModelRouter()
        router._verify_model_exists = MagicMock(return_value=True)
        return router

    def test_agent_binding_triggers_audit(self):
        """route() with agent='baron' → SwitchAuditor.record called (reason=agent_binding)."""
        router = self._make_router()
        router._switch_auditor = MagicMock()
        selection = router.route("light", agent="baron")
        assert isinstance(selection, RouteSelection)
        assert router._switch_auditor.record.called
        reasons = [
            c.args[0].reason
            for c in router._switch_auditor.record.call_args_list
            if c.args
        ]
        assert "agent_binding" in reasons

    def test_audit_failure_does_not_block_route(self):
        """SwitchAuditor.record raises → route() still returns a valid selection."""
        router = self._make_router()
        auditor = MagicMock()
        auditor.record.side_effect = RuntimeError("audit DB down")
        router._switch_auditor = auditor
        selection = router.route("light", agent="baron")
        assert isinstance(selection, RouteSelection)
        assert selection.model


# ---------------------------------------------------------------------------
# R1 — FeedbackBus TimeoutMatrix integration
# ---------------------------------------------------------------------------

class TestR1TimeoutMatrix:
    """R1: route_result timeout → auto conflict event; None matrix is safe."""

    def test_route_result_timeout_produces_conflict(self):
        """route_result with elapsed > hard threshold → conflict event emitted."""
        bus = FeedbackBus()
        conflicts = []
        bus.subscribe("conflict", lambda e: conflicts.append(e))
        bus.emit(FeedbackEvent(
            event_type="route_result",
            source="l1_router",
            payload={
                "mode": "heartbeat",
                "elapsed_ms": 5000,
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
            },
        ))
        assert len(conflicts) == 1
        assert conflicts[0].event_type == "conflict"
        assert conflicts[0].source == "timeout_matrix"

    def test_none_timeout_matrix_no_error(self):
        """_timeout_matrix is None → emit route_result does not raise."""
        bus = FeedbackBus()
        bus._timeout_matrix = None
        bus.emit(FeedbackEvent(
            event_type="route_result",
            source="l1_router",
            payload={"mode": "heartbeat", "elapsed_ms": 5000},
        ))


# ---------------------------------------------------------------------------
# A1-A3 — fixture_pair.py + cognitive_anchor.py
# ---------------------------------------------------------------------------

class TestA1A3FixturePair:
    """A1-A3: regression replay fixture pairs and anchor integration."""

    def test_validate_pair_pass(self):
        """Bad path BLOCK + valid path PASS → verdict='pass'."""
        pair = FixturePair(
            pair_id="fp-pass",
            anchor_id="a1",
            bad_path_context={"scenario": "bad"},
            valid_path_context={"scenario": "good"},
        )

        def route_fn(ctx):
            return "BLOCK" if ctx["scenario"] == "bad" else "PASS"

        result = FixturePairValidator().validate_pair(pair, _make_anchor(), route_fn)
        assert result.verdict == "pass"
        assert result.bad_path_result == "BLOCK"
        assert result.valid_path_result == "PASS"

    def test_timeout_protection(self):
        """route_fn sleeping 6s → ERROR (exceeds 5s timeout)."""
        pair = FixturePair(
            pair_id="fp-timeout",
            anchor_id="a2",
            bad_path_context={},
            valid_path_context={},
        )

        def slow_fn(_ctx):
            time.sleep(6)
            return "PASS"

        result = FixturePairValidator().validate_pair(pair, _make_anchor(), slow_fn)
        assert result.bad_path_result == "ERROR"
        assert result.valid_path_result == "ERROR"

    def test_replay_pairs_stats(self):
        """replay_pairs returns correct pass/fail/error/total counts."""
        pairs = [
            FixturePair(
                pair_id="fp-ok", anchor_id="a",
                bad_path_context={"v": "bad"}, valid_path_context={"v": "good"},
            ),
            FixturePair(
                pair_id="fp-fail", anchor_id="b",
                bad_path_context={"v": "good"}, valid_path_context={"v": "good"},
            ),
            FixturePair(
                pair_id="fp-err", anchor_id="c",
                bad_path_context={"v": "raise"}, valid_path_context={"v": "raise"},
            ),
        ]

        def route_fn(ctx):
            v = ctx["v"]
            if v == "raise":
                raise RuntimeError("boom")
            return "BLOCK" if v == "bad" else "PASS"

        stats = replay_pairs(pairs, _make_anchor(), route_fn)
        assert stats["total"] == 3
        assert stats["pass"] == 1
        assert stats["fail"] == 1
        assert stats["error"] == 1

    def test_run_fixture_replay_no_id_skipped(self):
        """Anchor with fixture_pair_id=None → {'skipped': True}."""
        anchor = _make_anchor()
        anchor.fixture_pair_id = None
        result = anchor.run_fixture_replay(lambda _ctx: "PASS")
        assert result.get("skipped") is True
