"""
Self Audit (S-Function)
=======================

Meta-audit: is the rule system itself correct?

S-function audits the H/M/A/E/C functions — not their outputs, but their
correctness. This is the "process supervision" function that Stella currently
performs manually.

Architecture Principle:
    H-function checks AGENT output against rules.
    S-function checks RULE correctness against reality.
    
    If H says "FAIL: knee_pain→squat forbidden" — S asks:
    - Is "knee_pain→squat forbidden" still the right rule?
    - Are any rules stale (not triggered in N days)?
    - Are any rules too broad (high false-positive rate)?
    - Are there rules that SHOULD exist but don't?

120-Day Connection:
    Stella is the first instance of S-function — she audits agent behavior
    independently. S-function encodes that independence as a deterministic
    periodic process with LLM-generated reports.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
import json


@dataclass
class AuditFinding:
    """A single audit finding about the rule system."""
    finding_id: str
    severity: str  # "critical" | "warning" | "info"
    category: str  # "staleness" | "overreach" | "gap" | "contradiction" | "integrity"
    description: str
    affected_rules: List[str] = field(default_factory=list)
    recommendation: Optional[str] = None
    timestamp: Optional[str] = None


@dataclass
class AuditReport:
    """Complete audit report for a check cycle."""
    report_id: str
    timestamp: str
    auditor: str  # "stella" | "cron_self_audit"
    scope: str  # "all_functions" | "H_only" | "rulespace"
    findings: List[AuditFinding] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    overall_status: str = "pass"  # "pass" | "warning" | "fail"
    signature: Optional[str] = None  # for on-chain audit signature

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "auditor": self.auditor,
            "scope": self.scope,
            "findings": [f.__dict__ for f in self.findings],
            "metrics": self.metrics,
            "overall_status": self.overall_status,
            "signature": self.signature,
        }


class SelfAudit:
    """
    S-Function: Periodic meta-audit of the rule system.

    Audit dimensions:
        1. Staleness: Rules not triggered in > N days → review
        2. Overreach: Rules with high false-positive rate → tighten
        3. Gap detection: Violation patterns with NO matching rule → create
        4. Contradiction: Rules that conflict with each other → resolve
        5. Integrity: Rule chain verifiability (M-function hash checks)

    This function is designed to be triggered by CRON, not by events.
    It runs independently of H/M/A/E/C — separate audit space.
    """

    def __init__(
        self,
        staleness_days: int = 30,
        overreach_threshold: float = 0.3,  # false-positive rate threshold
        report_path: Optional[str] = None,
    ):
        self._staleness_days = staleness_days
        self._overreach_threshold = overreach_threshold
        self._report_path = report_path
        self._reports: List[AuditReport] = []

    def audit(
        self,
        rules: Dict[str, Any],
        rule_stats: Dict[str, Dict[str, int]],  # rule_id → {triggers, false_positives, last_triggered}
        violation_patterns: List[Dict[str, Any]],
        auditor: str = "s_function",
    ) -> AuditReport:
        """
        Run a complete audit cycle.

        Args:
            rules: Current rule space (from A-function or M-function export).
            rule_stats: Per-rule statistics (triggers, false positives, last triggered).
            violation_patterns: Recent violations that passed through ALL gates
                               (potential gaps in rule coverage).
            auditor: Identity of the auditor (for audit trail).

        Returns:
            AuditReport with findings and recommendations.
        """
        report = AuditReport(
            report_id=f"audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            timestamp=datetime.now().isoformat(),
            auditor=auditor,
            scope="all_functions",
        )

        # Check 1: Staleness
        stale_rules = self._check_staleness(rules, rule_stats)
        for rule_id in stale_rules:
            report.findings.append(AuditFinding(
                finding_id=f"stale_{rule_id}",
                severity="warning",
                category="staleness",
                description=f"Rule '{rule_id}' not triggered in {self._staleness_days}+ days",
                affected_rules=[rule_id],
                recommendation="Review and consider deprecation",
            ))

        # Check 2: Overreach
        overreaching = self._check_overreach(rule_stats)
        for rule_id, fp_rate in overreaching:
            report.findings.append(AuditFinding(
                finding_id=f"overreach_{rule_id}",
                severity="warning",
                category="overreach",
                description=f"Rule '{rule_id}' has {fp_rate:.1%} false-positive rate",
                affected_rules=[rule_id],
                recommendation="Tighten rule pattern or reduce scope",
            ))

        # Check 3: Gaps
        gaps = self._check_gaps(violation_patterns, rules)
        for gap in gaps:
            report.findings.append(AuditFinding(
                finding_id=f"gap_{gap['pattern']}",
                severity="critical",
                category="gap",
                description=f"No rule covers violation pattern: {gap['pattern']}",
                recommendation="Trigger A-function to derive rule for this gap",
            ))

        # Check 4: Contradictions (MVP: simple overlap detection)
        contradictions = self._check_contradictions(rules)
        for rule_a, rule_b in contradictions:
            report.findings.append(AuditFinding(
                finding_id=f"contra_{rule_a}_{rule_b}",
                severity="critical",
                category="contradiction",
                description=f"Rules '{rule_a}' and '{rule_b}' have overlapping scope with different actions",
                affected_rules=[rule_a, rule_b],
                recommendation="Resolve conflict: merge or prioritize one rule",
            ))

        # Metrics
        report.metrics = {
            "total_rules": len(rules),
            "stale_rules": len(stale_rules),
            "overreaching_rules": len(overreaching),
            "gaps": len(gaps),
            "contradictions": len(contradictions),
            "active_rules": len(rules) - len(stale_rules),
            "health_score": self._compute_health_score(report),
        }

        # Overall status
        if any(f.severity == "critical" for f in report.findings):
            report.overall_status = "fail"
        elif any(f.severity == "warning" for f in report.findings):
            report.overall_status = "warning"
        else:
            report.overall_status = "pass"

        self._reports.append(report)

        if self._report_path:
            self._save_report(report)

        return report

    def get_reports(
        self, limit: int = 10, min_severity: Optional[str] = None
    ) -> List[AuditReport]:
        """Get historical audit reports."""
        reports = self._reports[-limit:]
        if min_severity:
            severity_order = {"critical": 0, "warning": 1, "info": 2}
            threshold = severity_order.get(min_severity, 2)
            reports = [
                r for r in reports
                if any(
                    severity_order.get(f.severity, 2) <= threshold
                    for f in r.findings
                )
            ]
        return reports

    def _check_staleness(
        self,
        rules: Dict[str, Any],
        stats: Dict[str, Dict[str, int]],
    ) -> List[str]:
        """Find rules that haven't been triggered recently."""
        stale = []
        now = datetime.now()
        for rule_id in rules:
            rule_stat = stats.get(rule_id, {})
            last_triggered = rule_stat.get("last_triggered")
            if last_triggered:
                last = datetime.fromisoformat(last_triggered)
                if (now - last).days > self._staleness_days:
                    stale.append(rule_id)
        return stale

    def _check_overreach(
        self, stats: Dict[str, Dict[str, int]]
    ) -> List[tuple]:
        """Find rules with high false-positive rates."""
        overreaching = []
        for rule_id, stat in stats.items():
            triggers = stat.get("triggers", 0)
            false_positives = stat.get("false_positives", 0)
            if triggers > 0:
                fp_rate = false_positives / triggers
                if fp_rate > self._overreach_threshold:
                    overreaching.append((rule_id, fp_rate))
        return overreaching

    def _check_gaps(
        self,
        violation_patterns: List[Dict[str, Any]],
        rules: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Find violation patterns with no matching rule."""
        gaps = []
        for pattern in violation_patterns:
            matched = False
            for rule_id, rule in rules.items():
                rule_pattern = rule.get("rule_pattern", "")
                if rule_pattern and rule_pattern.lower() in str(pattern).lower():
                    matched = True
                    break
            if not matched:
                gaps.append(pattern)
        return gaps

    def _check_contradictions(
        self, rules: Dict[str, Any]
    ) -> List[tuple]:
        """Find rules that conflict with each other (MVP: scope overlap)."""
        contradictions = []
        rule_items = list(rules.items())
        for i in range(len(rule_items)):
            for j in range(i + 1, len(rule_items)):
                r1_id, r1 = rule_items[i]
                r2_id, r2 = rule_items[j]
                # Simple check: same scope, similar pattern, different action
                if (r1.get("scope") == r2.get("scope") and
                    r1.get("rule_action") != r2.get("rule_action")):
                    p1 = r1.get("rule_pattern", "")
                    p2 = r2.get("rule_pattern", "")
                    # Check for term overlap
                    if p1 and p2 and any(t in p2 for t in p1.split("|")):
                        contradictions.append((r1_id, r2_id))
        return contradictions

    def _compute_health_score(self, report: AuditReport) -> float:
        """Compute overall rulespace health score."""
        m = report.metrics
        total = m.get("total_rules", 1)
        critical = sum(1 for f in report.findings if f.severity == "critical")
        warnings = sum(1 for f in report.findings if f.severity == "warning")
        
        score = 1.0
        score -= critical * 0.2  # each critical finding: -0.2
        score -= warnings * 0.05  # each warning: -0.05
        return max(0.0, min(1.0, score))

    def _save_report(self, report: AuditReport) -> None:
        if self._report_path:
            with open(self._report_path, "a") as f:
                f.write(json.dumps(report.to_dict(), ensure_ascii=False) + "\n")
