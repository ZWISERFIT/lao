"""
LAO v3.5.1 fix acceptance tests.

Covers:
- R3: _verify_model_exists cross-provider model existence check.
- R4: _usage_missing anomaly detector case.
- R5: SwitchAuditor wiring on routing fallback.
- R1: TimeoutMatrix integration in FeedbackBus.emit.
- A1-A3: FixturePair regression replay verdicts.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from lao.effect_anchored.cognitive_anchor import make_decision_anchor
from lao.effect_anchored.feedback_bus import FeedbackBus, FeedbackEvent
from lao.effect_anchored.optimization.detector import AnomalyDetector
from lao.effect_anchored.routing.model_router import ModelRouter
from lao.effect_anchored.validation.fixture_pair import (
    FixturePair,
    FixturePairValidator,
)


# ---------------------------------------------------------------------------
# R3: _verify_model_exists
# ---------------------------------------------------------------------------

class _MockResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_r3_verify_model_exists_found():
    router = ModelRouter()
    payload = json.dumps({"data": [{"id": "deepseek-v4-pro"}]}).encode()
    with patch("urllib.request.urlopen", return_value=_MockResponse(payload)):
        assert router._verify_model_exists("deepseek", "deepseek-v4-pro") is True


def test_r3_verify_model_exists_not_found():
    router = ModelRouter()
    payload = json.dumps({"data": [{"id": "other-model"}]}).encode()
    with patch("urllib.request.urlopen", return_value=_MockResponse(payload)):
        assert router._verify_model_exists("deepseek", "deepseek-v4-pro") is False


def test_r3_verify_model_exists_timeout():
    router = ModelRouter()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timed out")):
        assert router._verify_model_exists("deepseek", "deepseek-v4-pro") is False


def test_r3_verify_model_exists_unknown_provider():
    router = ModelRouter()
    assert router._verify_model_exists("nonexistent-provider", "deepseek-v4-pro") is False


# ---------------------------------------------------------------------------
# R4: _usage_missing
# ---------------------------------------------------------------------------

def test_r4_usage_missing_detected():
    detector = AnomalyDetector()
    anomalies = detector.detect({"usage_missing_count": 1})
    assert len(anomalies) == 1
    assert anomalies[0].type == "usage_missing"
    assert anomalies[0].severity == "mid"
    assert anomalies[0].detected is True


def test_r4_usage_missing_not_detected():
    detector = AnomalyDetector()
    anomalies = detector.detect({"usage_missing_count": 0})
    assert all(a.type != "usage_missing" for a in anomalies)


# ---------------------------------------------------------------------------
# R5: SwitchAuditor wiring
# ---------------------------------------------------------------------------

def test_r5_switch_auditor_called_on_degrade():
    """Primary model not found -> fallback chosen -> SwitchAuditor.record called."""
    router = ModelRouter()

    call_count = {"n": 0}

    def _fake_verify(provider: str, model: str) -> bool:
        call_count["n"] += 1
        # First call checks primary model; force it to fail.
        # Subsequent calls check fallback candidates; let them pass.
        return call_count["n"] > 1

    with patch.object(router, "_verify_model_exists", side_effect=_fake_verify):
        with patch.object(router._switch_auditor, "record") as mock_record:
            router.route("light")
            assert mock_record.called


def test_r5_switch_auditor_not_called_without_degrade():
    """Primary model verified -> no fallback switch -> record not called."""
    router = ModelRouter()
    with patch.object(router, "_verify_model_exists", return_value=True):
        with patch.object(router._switch_auditor, "record") as mock_record:
            router.route("light")
            assert not mock_record.called


# ---------------------------------------------------------------------------
# R1: TimeoutMatrix integration
# ---------------------------------------------------------------------------

def test_r1_timeout_matrix_emits_conflict_on_slow():
    bus = FeedbackBus()
    bus.emit(
        FeedbackEvent(
            event_type="route_result",
            source="l1_router",
            payload={
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "mode": "translation",
                "elapsed_ms": 800,  # above translation soft_timeout (500ms)
            },
        )
    )
    conflict_types = [e.event_type for e in bus._events]
    assert "conflict" in conflict_types
    conflict = next(e for e in bus._events if e.event_type == "conflict")
    assert conflict.source == "timeout_matrix"
    assert conflict.payload.get("timeout_verdict", {}).get("action") == "slow"


def test_r1_timeout_matrix_no_conflict_on_normal():
    bus = FeedbackBus()
    bus.emit(
        FeedbackEvent(
            event_type="route_result",
            source="l1_router",
            payload={
                "provider": "deepseek",
                "model": "deepseek-v4-pro",
                "mode": "translation",
                "elapsed_ms": 100,  # below soft_timeout (500ms)
            },
        )
    )
    assert all(e.event_type != "conflict" for e in bus._events)


# ---------------------------------------------------------------------------
# A1-A3: FixturePair
# ---------------------------------------------------------------------------

def _make_pair() -> FixturePair:
    return FixturePair(
        pair_id="fp-001",
        anchor_id="anchor-001",
        bad_path_context={"bad": True},
        valid_path_context={"bad": False},
    )


def _route_fn(context):
    return "BLOCK" if context.get("bad") else "PASS"


def test_a1_a3_fixture_pair_pass():
    pair = _make_pair()
    anchor = make_decision_anchor(
        anchor_id="anchor-001",
        principle="block bad contexts",
        trigger_condition="bad is True",
        action_rule="BLOCK",
    )
    validator = FixturePairValidator()
    result = validator.validate_pair(pair, anchor, _route_fn)
    assert result.bad_path_result == "BLOCK"
    assert result.valid_path_result == "PASS"
    assert result.verdict == "pass"


def test_a1_a3_fixture_pair_false_negative():
    pair = _make_pair()
    anchor = make_decision_anchor(
        anchor_id="anchor-001",
        principle="block bad contexts",
        trigger_condition="bad is True",
        action_rule="BLOCK",
    )
    validator = FixturePairValidator()

    def leaky_route(context):
        return "PASS"

    result = validator.validate_pair(pair, anchor, leaky_route)
    assert result.bad_path_result == "PASS"
    assert result.verdict == "fail"


def test_a1_a3_fixture_pair_false_positive():
    pair = _make_pair()
    anchor = make_decision_anchor(
        anchor_id="anchor-001",
        principle="block bad contexts",
        trigger_condition="bad is True",
        action_rule="BLOCK",
    )
    validator = FixturePairValidator()

    def over_block_route(context):
        return "BLOCK"

    result = validator.validate_pair(pair, anchor, over_block_route)
    assert result.valid_path_result == "BLOCK"
    assert result.verdict == "fail"
