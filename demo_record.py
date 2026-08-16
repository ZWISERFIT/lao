#!/usr/bin/env python3
"""
Effect-Anchored Ontology — Demo Script v1.0
Record this terminal session and upload as YC Demo Video.

Usage:
  cd effect-anchored-ontology/
  pip install -e .
  python3 demo_record.py
"""

def print_header(title):
    print()
    print(f"━━━ {title} ━━━")
    print()

def main():
    print("╔══════════════════════════════════════════╗")
    print("║  Effect-Anchored Ontology Demo v0.1.0   ║")
    print("║  pip install. Zero dependencies.         ║")
    print("╚══════════════════════════════════════════╝")

    print_header("Import all 6 functions")
    from effect_anchored import (
        HallucinationGate, MemoryAnchor,
        ContextRebuilder, Event,
        AdaptiveConstraint, Violation,
        EffectAnchoring, CapabilityObservation,
        SelfAudit
    )
    print("✅ HallucinationGate   — deterministic hallucination guard")
    print("✅ MemoryAnchor        — no guessing, just facts")
    print("✅ ContextRebuilder    — recover from session loss")
    print("✅ AdaptiveConstraint  — learn from mistakes automatically")
    print("✅ EffectAnchoring     — trust scoring (asymmetric)")
    print("✅ SelfAudit           — five-dimension auto-audit")

    print_header("1. MemoryAnchor — deterministic facts, no hallucination")
    m = MemoryAnchor()
    m.put("store_location", "东莞市万江街道, Dongguan, China")
    m.put("knee_pain_rule", "No squats. Safe: swimming, cycling.")
    r1 = m.lookup("store_location")
    r2 = m.lookup("competitor_price")
    r3 = m.lookup("knee_pain_rule")
    print(f"  lookup('store_location')  → {r1.value}")
    print(f"  lookup('competitor_price') → {r2.value}  ← Honest 'I don't know'")
    print(f"  lookup('knee_pain_rule')   → {r3.value}")

    print_header("2. HallucinationGate — rules OUTSIDE the LLM")
    gate = HallucinationGate(
        constraints_path="constraints/example_medical_rules.json",
        anchors_path="anchors/example_facts.json"
    )
    r1 = gate.check("Light walking recommended for knee recovery.", {"user_message": "my knee hurts"})
    r2 = gate.check("Do heavy barbell squats!", {"user_message": "my knee hurts"})
    print(f"  Safe advice:      passed={r1.passed} ✅")
    print(f"  Dangerous advice:  passed={r2.passed} ❌  → {r2.reason}")
    print("  Not prompts. Not RLHF. Deterministic code.")

    print_header("3. EffectAnchoring — asymmetric trust model")
    ea = EffectAnchoring()
    p1 = ea.record(CapabilityObservation(
        provider="demo-model", capability="medical_advice", model="test-v1",
        success=False, latency_ms=450, error_type="hallucination"
    ))
    print(f"  After 1 failure:    trust = {p1.trust_score:.2f}")
    for _ in range(12):
        p2 = ea.record(CapabilityObservation(
            provider="demo-model", capability="medical_advice", model="test-v1",
            success=True, latency_ms=200
        ))
    print(f"  After 12 successes: trust = {p2.trust_score:.2f}")
    print("  Trust erodes fast. Rebuilds slow. Like the real world.")

    print_header("4. SelfAudit — the rules audit THEMSELVES")
    sa = SelfAudit()
    report = sa.audit(
        rules={"medical": {"knee_pain": "no squats"}, "nutrition": {"diabetic": "no sugar"}},
        rule_stats={"medical": {"triggered": 15, "violated": 2}, "nutrition": {"triggered": 8, "violated": 0}},
        violation_patterns=[]
    )
    print(f"  Status:   {report.overall_status}")
    print(f"  Health:   {report.metrics['health_score']:.2f}")
    print(f"  Findings: {len(report.findings)} (0=clean)")
    print(f"  Dimensions: staleness, overreach, gaps, contradictions, integrity")
    print("  Every 24h, the system audits its own rules.")

    print()
    print("╔══════════════════════════════════════════╗")
    print("║  Six functions. pip install. Apache 2.0. ║")
    print("║  github.com/ZWISERFIT/effect-anchored-ontology ║")
    print("║  Born from 120 days of production.       ║")
    print("╚══════════════════════════════════════════╝")

if __name__ == "__main__":
    main()
