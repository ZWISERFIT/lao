"""
End-to-end tests for all six EAO functions.
Run: pytest -v tests/test_six_functions.py
"""
import pytest
import json
import tempfile
import os
from lao.effect_anchored import (
    HallucinationGate, HResult, GateResult,
    MemoryAnchor, MResult,
    ContextRebuilder, Event,
    AdaptiveConstraint, Violation,
    EffectAnchoring, CapabilityObservation,
    SelfAudit,
)

ANCHORS_PATH = os.path.join(os.path.dirname(__file__), "..", "anchors", "example_facts.json")
CONSTRAINTS_PATH = os.path.join(os.path.dirname(__file__), "..", "constraints", "example_medical_rules.json")


class TestHallucinationGate:
    """H-Function: Deterministic validation of LLM outputs."""

    def test_safe_output_passes(self):
        gate = HallucinationGate(constraints_path=CONSTRAINTS_PATH, anchors_path=ANCHORS_PATH)
        result = gate.check({"advice": "drink more water"}, context={})
        assert result.passed is True
        assert result.gate_result == GateResult.PASS

    def test_knee_pain_blocks_squat(self):
        gate = HallucinationGate(constraints_path=CONSTRAINTS_PATH, anchors_path=ANCHORS_PATH)
        result = gate.check("Let's do some squats", context={"user_message": "I have knee pain"})
        assert result.passed is False
        assert "knee_pain→squat" in result.anchors_violated

    def test_medical_rule_triggers_pattern_match(self):
        gate = HallucinationGate(constraints_path=CONSTRAINTS_PATH, anchors_path=ANCHORS_PATH)
        result = gate.check("For your back pain, try these stretches", context={"user_message": "my back hurts"})
        assert result.passed is False
        assert result.reason is not None

    def test_schema_validation_passes(self):
        gate = HallucinationGate()
        result = gate.check({"advice": "rest"}, context={}, expected_schema={"type": "object", "properties": {"advice": {"type": "string"}}})
        assert result.passed is True

    def test_result_to_dict(self):
        gate = HallucinationGate(constraints_path=CONSTRAINTS_PATH, anchors_path=ANCHORS_PATH)
        result = gate.check("Let's do squats", context={"user_message": "I have knee pain"})
        d = result.to_dict()
        assert d["passed"] is False
        assert len(d["anchors_violated"]) > 0  # violation anchors present


class TestMemoryAnchor:
    """M-Function: Deterministic key-value retrieval."""

    def test_lookup_found(self):
        mem = MemoryAnchor(anchor_db_path=ANCHORS_PATH)
        result = mem.lookup("founder_first_store_location")
        assert result.found is True
        assert result.value == "东莞市万江街道"

    def test_lookup_miss_returns_none(self):
        mem = MemoryAnchor(anchor_db_path=ANCHORS_PATH)
        result = mem.lookup("nonexistent_key")
        assert result.found is False
        assert result.value is None

    def test_put_and_read_back(self):
        mem = MemoryAnchor()
        mem.put("test_key", "test_value", source="pytest")
        result = mem.lookup("test_key")
        assert result.found is True
        assert result.value == "test_value"
        assert result.content_hash is not None

    def test_verify_integrity(self):
        mem = MemoryAnchor()
        mem.put("integ_key", {"nested": [1, 2, 3]}, source="pytest")
        assert mem.verify("integ_key") is True

    def test_keys_list(self):
        mem = MemoryAnchor(anchor_db_path=ANCHORS_PATH)
        keys = mem.keys()
        assert len(keys) >= 6
        assert "knee_pain" in keys or "founder_first_store_location" in keys

    def test_multi_lookup(self):
        mem = MemoryAnchor(anchor_db_path=ANCHORS_PATH)
        results = mem.multi_lookup(["founder_first_store_location", "nonexistent"])
        assert results["founder_first_store_location"].found is True
        assert results["nonexistent"].found is False


class TestContextRebuilder:
    """C-Function: Structured event recording and reconstruction."""

    def test_record_and_reconstruct(self):
        c = ContextRebuilder(session_id="test_session")
        c.record(Event(
            event_id="evt_1", timestamp="2026-07-27T14:00:00", speaker="founder",
            event_type="decision", subject="Test", summary="Test decision",
            content_hash="abc123"
        ))
        c.record(Event(
            event_id="evt_2", timestamp="2026-07-27T14:30:00", speaker="tristan",
            event_type="answer", subject="Test reply", summary="Test answer",
            content_hash="def456", parent_events=["evt_1"]
        ))
        chain = c.reconstruct()
        assert len(chain) == 2

    def test_event_chain_parent_child(self):
        c = ContextRebuilder(session_id="test_session")
        c.record(Event(
            event_id="evt_3", timestamp="2026-07-27T15:00:00", speaker="founder",
            event_type="decision", subject="Chain test", summary="Parent",
            content_hash="parent_hash"
        ))
        c.record(Event(
            event_id="evt_4", timestamp="2026-07-27T15:01:00", speaker="tristan",
            event_type="answer", subject="Chain test", summary="Child",
            content_hash="child_hash", parent_events=["evt_3"]
        ))
        chain = c.get_event_chain("evt_3")
        assert len(chain) == 2
        assert chain[0].event_id == "evt_3"
        assert chain[1].event_id == "evt_4"

    def test_integrity_verification(self):
        c = ContextRebuilder(session_id="test_session")
        c.record(Event(
            event_id="evt_5", timestamp="2026-07-27T16:00:00", speaker="founder",
            event_type="decision", subject="Integrity", summary="Verify me",
            content_hash="known_hash"
        ))
        assert c.verify_integrity("evt_5") is False  # hash doesn't match content

    def test_filter_by_type(self):
        c = ContextRebuilder(session_id="test_session")
        c.record(Event(
            event_id="evt_6", timestamp="2026-07-27T17:00:00", speaker="founder",
            event_type="decision", subject="Filter", summary="Decision",
            content_hash="hash_1"
        ))
        c.record(Event(
            event_id="evt_7", timestamp="2026-07-27T17:01:00", speaker="tristan",
            event_type="answer", subject="Filter", summary="Answer",
            content_hash="hash_2"
        ))
        decisions = c.reconstruct(event_types=["decision"])
        assert len(decisions) == 1
        assert decisions[0].event_id == "evt_6"

    def test_export(self):
        c = ContextRebuilder(session_id="test_session")
        c.record(Event(
            event_id="evt_8", timestamp="2026-07-27T18:00:00", speaker="founder",
            event_type="decision", subject="Export", summary="Export test",
            content_hash="hash_export"
        ))
        exported = c.export()
        assert len(exported) == 1
        assert exported[0]["event_id"] == "evt_8"


class TestEffectAnchoring:
    """E-Function: Trust scoring from observed effects."""

    def test_trust_decays_on_failure(self):
        e = EffectAnchoring(min_observations=3, decay_rate=0.3, recovery_rate=0.02)
        # Build up trust first
        for _ in range(10):
            e.record(CapabilityObservation(
                capability="streaming", provider="deepseek", model="v4-pro",
                success=True, latency_ms=3000
            ))
        # Capture trust score BEFORE failure (copy the value, not the reference)
        trust_before = e.get_profile("deepseek", "v4-pro", "streaming").trust_score
        # Record a failure
        e.record(CapabilityObservation(
            capability="streaming", provider="deepseek", model="v4-pro",
            success=False, error_type="timeout", latency_ms=9000
        ))
        trust_after = e.get_profile("deepseek", "v4-pro", "streaming").trust_score
        # Trust should drop after a failure
        assert trust_after < trust_before, f"trust_before={trust_before}, trust_after={trust_after}"

    def test_min_observations_provisional(self):
        e = EffectAnchoring(min_observations=5)
        e.record(CapabilityObservation(
            capability="tools", provider="deepseek", model="v4-pro",
            success=True, latency_ms=1000
        ))
        profile = e.get_profile("deepseek", "v4-pro", "tools")
        assert profile.trust_score == 0.5  # provisional

    def test_failure_modes_tracked(self):
        e = EffectAnchoring(min_observations=3)
        for _ in range(3):
            e.record(CapabilityObservation(
                capability="streaming", provider="deepseek", model="v4-pro",
                success=True, latency_ms=2000
            ))
        e.record(CapabilityObservation(
            capability="streaming", provider="deepseek", model="v4-pro",
            success=False, error_type="rate_limit", latency_ms=500
        ))
        profile = e.get_profile("deepseek", "v4-pro", "streaming")
        assert "rate_limit" in profile.failure_modes

    def test_compare_providers(self):
        e = EffectAnchoring(min_observations=3)
        for _ in range(3):
            e.record(CapabilityObservation(
                capability="streaming", provider="deepseek", model="v4-pro",
                success=True, latency_ms=2000
            ))
        for _ in range(3):
            e.record(CapabilityObservation(
                capability="streaming", provider="qwen", model="vl-max",
                success=False, error_type="timeout", latency_ms=10000
            ))
        profiles = e.compare("streaming")
        assert "deepseek" in profiles
        assert "qwen" in profiles
        assert profiles["deepseek"].trust_score > profiles["qwen"].trust_score

    def test_export_matrix(self):
        e = EffectAnchoring(min_observations=1)
        e.record(CapabilityObservation(
            capability="streaming", provider="deepseek", model="v4-pro",
            success=True, latency_ms=2000
        ))
        matrix = e.export_provider_matrix()
        assert len(matrix) == 1
        assert matrix[0]["provider"] == "deepseek"


class TestAdaptiveConstraint:
    """A-Function: Violation → equivalence class → rule."""

    def test_derive_rule_from_violation(self):
        a = AdaptiveConstraint()
        v = Violation(
            violation_id="v_001", layer="fact",
            description="Agent suggested squats for knee pain",
            llm_output_snippet="Let's do squats",
            context={"user_message": "I have knee pain"},
            anchors_violated=["knee_pain→squat"]
        )
        rule = a.derive(v)
        assert rule.rule_id.startswith("rule_")
        assert rule.rule_pattern != ""
        assert rule.confidence > 0

    def test_h_verify_rejection_lowers_confidence(self):
        a = AdaptiveConstraint()
        v = Violation(
            violation_id="v_002", layer="fact",
            description="Agent gave medical advice",
            llm_output_snippet="Take ibuprofen",
            context={}, anchors_violated=["medical_advice"]
        )
        rule_normal = a.derive(v)
        rule_with_h = a.derive(v, h_verify=lambda output, ctx: HResult(
            passed=False, gate_result=GateResult.FAIL, reason="Test reject"
        ))
        assert rule_with_h.confidence < rule_normal.confidence

    def test_export_for_m_function(self):
        a = AdaptiveConstraint()
        v = Violation(
            violation_id="v_003", layer="rule",
            description="Timeout pattern detected",
            llm_output_snippet="timeout", context={}, anchors_violated=[]
        )
        a.derive(v)
        m_export = a.export_for_m_function()
        assert len(m_export["anchors"]) >= 1

    def test_export_for_h_function(self):
        a = AdaptiveConstraint()
        v = Violation(
            violation_id="v_004", layer="rule",
            description="Pattern violation",
            llm_output_snippet="pattern", context={}, anchors_violated=[]
        )
        a.derive(v)
        h_export = a.export_for_h_function()
        assert len(h_export["rules"]) >= 1


class TestSelfAudit:
    """S-Function: Meta-audit of the rule system."""

    def test_audit_produces_report(self):
        s = SelfAudit(staleness_days=30)
        rules = {"r1": {"rule_pattern": "test", "rule_action": "block", "scope": "agent"}}
        stats = {"r1": {"triggers": 10, "false_positives": 1, "last_triggered": "2026-07-27T10:00:00"}}
        violations = [{"pattern": "uncovered_gap", "count": 5}]
        report = s.audit(rules=rules, rule_stats=stats, violation_patterns=violations)
        assert report.overall_status in ("pass", "warning", "fail")
        assert len(report.findings) >= 1  # gap detected

    def test_stale_rule_detection(self):
        s = SelfAudit(staleness_days=30)
        rules = {"old_rule": {"rule_pattern": "old", "rule_action": "block", "scope": "agent"}}
        stats = {"old_rule": {"triggers": 0, "false_positives": 0, "last_triggered": "2026-01-01T00:00:00"}}
        report = s.audit(rules=rules, rule_stats=stats, violation_patterns=[])
        stale_findings = [f for f in report.findings if f.category == "staleness"]
        assert len(stale_findings) >= 1

    def test_overreach_detection(self):
        s = SelfAudit(overreach_threshold=0.3)
        rules = {"noisy_rule": {"rule_pattern": "noisy", "rule_action": "block", "scope": "agent"}}
        stats = {"noisy_rule": {"triggers": 10, "false_positives": 8, "last_triggered": "2026-07-27T10:00:00"}}
        report = s.audit(rules=rules, rule_stats=stats, violation_patterns=[])
        overreach_findings = [f for f in report.findings if f.category == "overreach"]
        assert len(overreach_findings) >= 1

    def test_get_reports_history(self):
        s = SelfAudit()
        s.audit(rules={}, rule_stats={}, violation_patterns=[])
        s.audit(rules={}, rule_stats={}, violation_patterns=[])
        reports = s.get_reports(limit=10)
        assert len(reports) >= 2
