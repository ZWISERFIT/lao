"""
Adaptive Constraint (A-Function)
================================

Violation → equivalence class → rule generation.
The ONLY function that operates INSIDE the LLM's reasoning space —
because equivalence class derivation requires semantic understanding.

Architecture Principle:
    A single violation is not just "this event". It's a representative
    of an equivalence class [v] — all future events that share the same
    structural pattern. Deriving [v] requires semantic reasoning (LLM).
    
    BUT: once the rule is generated, it is immediately externalized to
    M-function (hard-coded anchor) and H-function (deterministic check).
    
    This is "compound interest on errors":
    rule_space(t+1) ⊇ rule_space(t) — coverage grows monotonically.

120-Day Lesson:
    19-day silent proxy disconnect → A derives: "ALL proxy health checks
    must heartbeat every 5 min, not just at startup" → rule written to M.
    Next time ANY proxy fails → caught within 5 min, not 19 days.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Callable
import json
import hashlib
import logging
import sys

logger = logging.getLogger(__name__)


@dataclass
class Violation:
    """A structured violation record — input to A-function."""
    violation_id: str
    layer: str  # "schema" | "fact" | "rule"
    description: str
    llm_output_snippet: str  # what the LLM actually said
    context: Dict[str, Any] = field(default_factory=dict)
    anchors_violated: List[str] = field(default_factory=list)
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "violation_id": self.violation_id,
            "layer": self.layer,
            "description": self.description,
            "llm_output_snippet": self.llm_output_snippet,
            "context": self.context,
            "anchors_violated": self.anchors_violated,
            "timestamp": self.timestamp,
        }


@dataclass
class DerivedRule:
    """A rule derived from a violation equivalence class."""
    rule_id: str
    violation_source: str  # violation_id that triggered this
    equivalence_class: str  # human-readable description of [v]
    rule_pattern: str  # pattern to match in H-function
    rule_action: str  # "block" | "reroute" | "flag" | "log"
    scope: str  # "agent" | "provider" | "system"
    confidence: float  # 0.0-1.0, how certain the equivalence class is correct
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "violation_source": self.violation_source,
            "equivalence_class": self.equivalence_class,
            "rule_pattern": self.rule_pattern,
            "rule_action": self.rule_action,
            "scope": self.scope,
            "confidence": self.confidence,
            "created_at": self.created_at,
        }


class AdaptiveConstraint:
    """
    A-Function: Violation → Equivalence Class → Rule.

    This function takes a detected violation (from H-function, M-function, or
    infrastructure monitoring) and:
    
    1. Calls LLM to derive the equivalence class [v]:
       "This violation is an instance of what GENERAL class of problem?"
    
    2. Generates a rule that covers ALL of [v], not just this instance:
       "block knee_pain→squat_advice" → "block knee_pain→ALL lower_body_load_advice"
    
    3. Externalizes the rule:
       - Writes to M-function anchor space (deterministic lookup)
       - Updates H-function constraint rules (automatic enforcement)
       - Logs to RetroOnto decision trace

    Key safety property:
        A-function can propose rules. Gatekeeper (S-function or human review)
        can approve/reject before rules go live. Default: rules with confidence
        ≥ 0.7 auto-activate; below 0.7 require review.

    NOT architecture:
        ❌ "Agent learns from mistake → never makes it again"
        ✅ "One violation → derive equivalence class → generate rule
           → future violations in same class are blocked deterministically"
    """

    def __init__(
        self,
        llm_call: Optional[Callable] = None,
        rule_store_path: Optional[str] = None,
        auto_activate_threshold: float = 0.7,
    ):
        """
        Args:
            llm_call: Function to call LLM for equivalence class derivation.
                      Signature: llm_call(prompt: str) -> str
            rule_store_path: Path to store derived rules.
            auto_activate_threshold: Confidence above which rules auto-activate.
        """
        self._llm_call = llm_call
        self._rules: Dict[str, DerivedRule] = {}
        self._path = rule_store_path
        self._threshold = auto_activate_threshold
        self._violation_history: List[Violation] = []

        if rule_store_path:
            self._load_rules()

    def derive(self, violation: Violation, h_verify: Optional[Callable] = None) -> DerivedRule:
        """
        Given a violation, derive its equivalence class and generate a rule.

        Args:
            violation: The violation detected by H-function, M-function, or monitor.
            h_verify: Optional external H-function callback for independent
                      verification of the derived rule. TRISTRAN T3 FIX:
                      A-function generates rules (LLM reasoning space) →
                      H-function independently verifies them (deterministic space)
                      → only then activate. Prevents "LLM judging LLM" loop.
                      
                      Accepts TWO calling conventions (auto-detected):
                      1. HResult verify(llm_output, context) — native H.gate.check signature
                      2. HResult verify(output, context) — direct named-param call

        Returns:
            DerivedRule covering the equivalence class [v].
            If h_verify is provided and fails → rule.auto_activate = False,
            confidence is halved, rule tagged for human review.

        Raises:
            ValueError: If h_verify is provided but not callable.
        """
        self._violation_history.append(violation)

        # Step 1: Derive equivalence class (requires LLM semantic reasoning)
        equivalence_class = self._derive_equivalence_class(violation)

        # Step 2: Generate rule from equivalence class
        rule = self._generate_rule(violation, equivalence_class)

        # Step 3: TRISTAN T3 FIX — Independent H-function verification
        if h_verify is not None:
            h_verify_output = {
                "equivalence_class": rule.equivalence_class,
                "rule_pattern": rule.rule_pattern,
            }
            h_verify_context = {"violation": violation.to_dict()}
            try:
                # Auto-detect calling convention: try both (output, context)
                # and (llm_output, context) to support both H.gate.check
                # and custom callbacks
                import inspect as _inspect
                try:
                    _sig = _inspect.signature(h_verify)
                    _params = list(_sig.parameters.keys())
                except (ValueError, TypeError):
                    _params = []
                # A-001 FIX: if first param is 'llm_output', use HallucinationGate.check convention
                if _params and _params[0] == 'llm_output':
                    h_result = h_verify(
                        llm_output=h_verify_output,
                        context=h_verify_context,
                    )
                elif _params and _params[0] == 'output':
                    h_result = h_verify(
                        output=h_verify_output,
                        context=h_verify_context,
                    )
                else:
                    # Fallback: try output= first (common wrapper convention)
                    h_result = h_verify(
                        output=h_verify_output,
                        context=h_verify_context,
                    )
                if not h_result.passed:
                    # H-function rejected the rule → flag for human review
                    rule.confidence = rule.confidence / 2
                    rule.rule_action = "pending_review"
                    rule.scope = f"{rule.scope}:h_rejected"
            except Exception:
                # H-function unavailable → conservative: halve confidence
                rule.confidence = rule.confidence / 2

        # Step 4: Store rule
        self._rules[rule.rule_id] = rule

        if self._path:
            self._save_rules()

        return rule

    def get_rules(
        self,
        scope: Optional[str] = None,
        active_only: bool = False,
        min_confidence: float = 0.0,
    ) -> List[DerivedRule]:
        """Query derived rules with filters."""
        results = []
        for rule in self._rules.values():
            if scope and rule.scope != scope:
                continue
            if rule.confidence < min_confidence:
                continue
            results.append(rule)
        return results

    def export_for_m_function(self) -> Dict[str, Any]:
        """Export rules in M-function anchor format for deterministic lookup."""
        anchors = {}
        for rule in self._rules.values():
            anchors[rule.rule_id] = {
                "value": {
                    "pattern": rule.rule_pattern,
                    "action": rule.rule_action,
                    "equivalence_class": rule.equivalence_class,
                },
            }
        return {"anchors": anchors}

    def export_for_h_function(self) -> Dict[str, Any]:
        """Export rules in H-function constraint format for deterministic check."""
        rules = {}
        for rule in self._rules.values():
            rules[rule.rule_id] = {
                "type": "pattern_match",
                "pattern": rule.rule_pattern,
                "reason": rule.equivalence_class,
                "scope": rule.scope,
            }
        return {"rules": rules}

    def _derive_equivalence_class(self, violation: Violation) -> str:
        """
        Derive the equivalence class [v] for a violation.
        This is the ONLY step in the framework that requires LLM reasoning.

        Prompt template (the LLM sees):
            "Given this violation: {violation.description}
             Context: {violation.context}
             
             This violation is an instance of what GENERAL class of problem?
             
             Generate an equivalence class description.
             An equivalence class should cover ALL future violations
             that share the same structural pattern, not just this instance.

             Example:
             - Violation: 'Agent suggested squats for knee pain'
             - Equivalence class: 'Agent provides exercise advice for any
               medical symptom without routing to human first'
             
             Output ONLY the equivalence class description, nothing else."
        """
        if self._llm_call:
            prompt = self._build_equivalence_prompt(violation)
            result = self._llm_call(prompt)
            return result.strip()
        
        # Fallback: literal violation description as the class
        return violation.description

    def _generate_rule(
        self, violation: Violation, equivalence_class: str
    ) -> DerivedRule:
        """
        Generate a concrete rule from the equivalence class.
        The rule pattern is designed to be used by H-function for pattern matching.
        """
        rule_id = self._make_rule_id(violation)
        
        # Generate rule pattern from violation + equivalence class
        pattern = self._build_rule_pattern(violation, equivalence_class)
        
        return DerivedRule(
            rule_id=rule_id,
            violation_source=violation.violation_id,
            equivalence_class=equivalence_class,
            rule_pattern=pattern,
            rule_action="block",
            scope=self._infer_scope(violation),
            confidence=self._estimate_confidence(violation),
        )

    def _build_equivalence_prompt(self, violation: Violation) -> str:
        """Build the LLM prompt for equivalence class derivation."""
        return f"""Given this violation:
Description: {violation.description}
LLM Output: {violation.llm_output_snippet}
Context: {json.dumps(violation.context, ensure_ascii=False)}
Layer: {violation.layer}
Anchors Violated: {violation.anchors_violated}

This violation is an instance of what GENERAL class of problem?

Generate an equivalence class description. An equivalence class should cover
ALL future violations that share the same structural pattern, not just this instance.

Example:
- Violation: 'Agent suggested squats for knee pain'
- Equivalence class: 'Agent provides exercise advice for any medical symptom without routing to human first'

Output ONLY the equivalence class description. Nothing else."""

    def _build_rule_pattern(
        self, violation: Violation, equivalence_class: str
    ) -> str:
        """Build a pattern string for H-function to match against."""
        # Extract key terms from the violation for pattern matching
        terms = []
        for anchor in violation.anchors_violated:
            # e.g., "knee_pain→squat" → extract "knee_pain" and "squat"
            parts = anchor.split("→")
            terms.extend(parts)
        
        if terms:
            return "|".join(terms)
        return equivalence_class.lower().replace(" ", "_")

    def _infer_scope(self, violation: Violation) -> str:
        """Infer the scope of the rule from violation context."""
        if "provider" in violation.context:
            return "agent"
        if violation.layer == "fact":
            return "system"
        return "agent"

    def _estimate_confidence(self, violation: Violation) -> float:
        """
        Estimate confidence in the derived equivalence class.
        
        Simple heuristic (MVP):
        - More anchors violated → higher confidence (multiple signals agree)
        - First-time violation type → lower confidence (less data)
        """
        base = 0.6
        anchor_bonus = min(len(violation.anchors_violated) * 0.1, 0.3)
        
        # Check if similar violations exist in history
        similar_count = sum(
            1 for v in self._violation_history
            if v.layer == violation.layer
            and any(a in violation.anchors_violated for a in v.anchors_violated)
        )
        history_bonus = min(similar_count * 0.05, 0.2)
        
        return min(base + anchor_bonus + history_bonus, 1.0)

    @staticmethod
    def _make_rule_id(violation: Violation) -> str:
        """Generate a stable rule ID from violation data."""
        seed = f"{violation.layer}|{violation.description}"
        h = hashlib.sha256(seed.encode()).hexdigest()[:8]
        return f"rule_{violation.violation_id}_{h}"

    def _load_rules(self) -> None:
        if self._path:
            try:
                with open(self._path, "r") as f:
                    data = json.load(f)
                    for rule_data in data.get("rules", []):
                        rule = DerivedRule(**rule_data)
                        self._rules[rule.rule_id] = rule
            except (FileNotFoundError, json.JSONDecodeError):
                pass

    def _save_rules(self) -> None:
        if self._path:
            rules_list = [r.to_dict() for r in self._rules.values()]
            with open(self._path, "w") as f:
                json.dump({"rules": rules_list}, f, ensure_ascii=False, indent=2)

    def generate_code(self, error_pattern: Dict[str, Any]) -> Optional[str]:
        """
        A-function 新增方法：从错误模式→生成Python约束代码
        
        Per Zeus施工菜单#6: A函数从"推导规则"扩展到"生成代码"
        
        Args:
            error_pattern: {
                "pattern_id": str,
                "pattern_fingerprint": str,
                "error_signature": str,
                "claimed": str,
                "expected": str,
                "actual": str,
                "severity": str,
                "category": str,
                "constraint_text": str,
            }
        
        Returns:
            生成的约束代码文件路径，或None
        """
        engine_dir = "/home/agentuser/.openclaw/workspace/tristan/tech_lead/retroonto/engine"
        sys.path.insert(0, engine_dir)
        try:
            from constraint_generator import ConstraintGenerator
            from rule_registry import RuleRegistry

            gen = ConstraintGenerator()
            registry = RuleRegistry()

            constraint_path, constraint_id = gen.generate_and_write(error_pattern)
            if constraint_path:
                registry.register(
                    rule_id=f"RULE-{constraint_id}",
                    fingerprint=error_pattern.get("pattern_fingerprint", ""),
                    constraint_file=str(constraint_path),
                    constraint_id=constraint_id,
                    error_source_id=error_pattern.get("pattern_id", ""),
                    severity=error_pattern.get("severity", "🔴"),
                    category=error_pattern.get("category", "infrastructure"),
                    description=error_pattern.get("constraint_text", ""),
                )
                logger.info(f"Generated constraint code at: {constraint_path}")
                return str(constraint_path)
        except ImportError:
            # Engine not yet deployed — log and continue
            logger.debug("Dynamic Engine not available for code generation")
        except Exception as e:
            logger.error(f"Code generation failed: {e}")

        return None
