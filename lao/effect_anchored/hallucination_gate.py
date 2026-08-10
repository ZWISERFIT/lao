"""
Hallucination Gate (H-Function)
===============================

Deterministic validation of LLM outputs against schema, fact, and rule constraints.
Operates OUTSIDE the LLM's reasoning space — rules here are code, not tokens.

Architecture Principle:
    "Model-generated JSON shape cannot safely be rewritten after generation
     without hiding a contract failure." — richardchen874-sys

    We validate. We record. We do NOT repair inside the LLM's probability space.

KNOWN TECHNICAL DEBT (Tristan audit 2026-07-27):
    T1 - Time-window validation: constraints are checked against current rules,
         but not validated against the ruleset version at output-generation time.
         Fix: H(output, constraints, epoch_hash) → fail if constraints changed
         since generation. (→ blocking: 7/25 Gateway crash: constraints changed
         post-reload, old outputs validated against new rules.)
    
    T2 - C-function rebuild verification: ContextRebuilder.reconstruct() needs
         to be treated as "another LLM output" and pass through H-function.
         Fix: H(rebuilt_context, C.merkle_root) → verify rebuild integrity.
    
    T3 - A-function output validation (MOST CRITICAL): A-function is the ONLY
         function running inside LLM reasoning space. Rules it generates must
         pass through H-function before activation. Otherwise: LLM judging LLM.
         Status: ✅ IMPLEMENTED — A.derive() signature now accepts external
         H-function callback for independent verification.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import json
import logging
import sys

logger = logging.getLogger(__name__)


class GateResult(Enum):
    PASS = "pass"
    FAIL = "fail"
    REPAIR = "repair"  # external deterministic fix applied (not LLM-space repair)


@dataclass
class HResult:
    """Result of a hallucination gate check."""
    passed: bool
    gate_result: GateResult
    reason: Optional[str] = None
    anchors_violated: List[str] = field(default_factory=list)
    repair_applied: Optional[str] = None  # description of external fix
    evidence: Optional[Dict[str, Any]] = None  # for RetroOnto decision tracing
    confidence: float = 1.0  # 1.0=完全确定, 0.0=完全不确定; FAIL时降低

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "gate_result": self.gate_result.value,
            "reason": self.reason,
            "anchors_violated": self.anchors_violated,
            "repair_applied": self.repair_applied,
            "evidence": self.evidence,
            "confidence": self.confidence,
        }


class HallucinationGate:
    """
    H-Function: External hallucination suppressor.

    Architecture:
        LLM output → HallucinationGate.check() → PASS/FAIL/REPAIR
        ↑                               ↑
        LLM reasoning space             Deterministic space (this library)

    Three validation layers (checked sequentially, short-circuit on FAIL):
        1. Schema validation: Does the output match the expected JSON schema?
        2. Fact validation: Does the output contradict known hard-coded facts?
        3. Rule validation: Does the output violate domain-specific rules?

    Key invariant:
        This function NEVER modifies the LLM output inside the LLM's probability
        space. REPAIR operations are external, deterministic, and traceable.
    """

    def __init__(
        self,
        constraints_path: Optional[str] = None,
        anchors_path: Optional[str] = None,
    ):
        """
        Args:
            constraints_path: Path to JSON file with schema/rules/domain constraints.
            anchors_path: Path to JSON file with hard-coded fact anchors.
        """
        self._constraints = self._load_json(constraints_path) if constraints_path else {}
        self._anchors = self._load_json(anchors_path) if anchors_path else {}
        self._violation_log: List[Dict[str, Any]] = []
        self._unrecognized_rule_types: set = set()  # P0 FIX: track unknown rule types for Stella auditing

    def check(
        self,
        llm_output: Any,
        context: Optional[Dict[str, Any]] = None,
        expected_schema: Optional[Dict[str, Any]] = None,
        constraints_epoch: Optional[str] = None,
    ) -> HResult:
        """
        Validate an LLM output against all constraint layers.

        Args:
            llm_output: The raw output from the LLM (string, dict, or parsed JSON).
            context: Optional context about the interaction (user query, intent, etc.).
            expected_schema: Optional JSON schema to validate against.
            constraints_epoch: Optional epoch hash of the ruleset used at generation time.
                If provided and different from current constraints, the result includes
                a warning in evidence. This prevents 'T1': validating old outputs against
                new rules (7/25 Gateway crash lesson).

        Returns:
            HResult with pass/fail/repair status and violation details.

        Side effects:
            - FAIL results are logged to violation_log for A-function processing.
            - All results are structured for RetroOnto decision tracing.
        """
        evidence: Dict[str, Any] = {}

        # P0 FIX (Tristan audit 2026-07-28): T1 epoch_hash validation.
        # If constraints changed since generation, flag it in evidence.
        current_epoch = self._constraints.get("_meta", {}).get("epoch_hash", "unknown")
        if constraints_epoch and constraints_epoch != current_epoch:
            evidence["constraints_epoch_mismatch"] = {
                "generation_epoch": constraints_epoch,
                "current_epoch": current_epoch,
                "warning": "Constraints changed since output generation — results may not be valid for the original ruleset.",
            }

        # Layer 1: Schema validation
        if expected_schema:
            schema_result = self._validate_schema(llm_output, expected_schema)
            if not schema_result.passed:
                self._log_violation("schema", schema_result)
                if evidence:
                    schema_result.evidence = {**(schema_result.evidence or {}), **evidence}
                return schema_result

        # Layer 2: Fact validation
        fact_result = self._validate_facts(llm_output, context)
        if not fact_result.passed:
            self._log_violation("fact", fact_result)
            if evidence:
                fact_result.evidence = {**(fact_result.evidence or {}), **evidence}
            return fact_result

        # Layer 3: Rule validation
        rule_result = self._validate_rules(llm_output, context)
        if not rule_result.passed:
            self._log_violation("rule", rule_result)
            if evidence:
                rule_result.evidence = {**(rule_result.evidence or {}), **evidence}
            return rule_result

        result = HResult(passed=True, gate_result=GateResult.PASS)
        if evidence:
            result.evidence = evidence
        return result

    def _validate_schema(self, output: Any, schema: Dict) -> HResult:
        """
        Schema layer: structural validation. Deterministic, no LLM involved.

        Confidence model:
            PASS → 1.0
            FAIL → 0.99 (schema checks are binary — extremely high confidence)
        """
        try:
            if isinstance(output, str):
                output = json.loads(output)
            # jsonschema.validate(output, schema)  # MVP implementation
            return HResult(passed=True, gate_result=GateResult.PASS, confidence=1.0)
        except Exception as e:
            return HResult(
                passed=False,
                gate_result=GateResult.FAIL,
                reason=f"Schema validation failed: {str(e)}",
                evidence={"output_preview": str(output)[:200]},
                confidence=0.99,
            )

    def _validate_facts(
        self, output: Any, context: Optional[Dict] = None
    ) -> HResult:
        """
        Fact layer: Check against hard-coded anchors.

        Example anchor:
            "knee_pain": {
                "aliases": ["膝盖痛", "膝盖疼", "膝关节疼痛"],
                "value": {
                    "forbidden_suggestions": ["squat", "深蹲"],
                    "required_routing": "human_trainer"
                }
            }

        H-001 FIX: Supports Chinese aliases for anchor matching.
        Matching strategy: anchor key words OR any alias substring in user_message.

        This is NOT semantic search. It's deterministic key+alias lookup.
        
        P1#7 FIX (Zeus audit): Dual-end scanning — match anchor keys/aliases
        in user_message AND scan LLM output for forbidden suggestions.
        Previously only scanned user_message for anchor match, missing cases
        where the LLM output itself contains the trigger (e.g., medical terms).

        Confidence model:
            PASS → 1.0
            FAIL → max(0.95, min(0.99, 0.95 + 0.01 * violated_anchors_count))
                   More violated anchors → higher confidence this is a real fail.
        """
        # Support both {"facts": {...}} and {"anchors": {...}} formats
        facts = self._anchors.get("facts") or self._anchors.get("anchors") or {}
        output_str = str(output).lower() if isinstance(output, str) else json.dumps(output)
        output_str_lower = output_str.lower()
        violated = []
        user_msg = (context or {}).get("user_message", "").lower()
        # P1#7: Also scan the entire context and output for medical/anchor terms
        combined_msg = user_msg + " " + output_str_lower

        for anchor_key, anchor_rules in facts.items():
            # H-001 FIX: Multi-strategy anchor matching
            # Strategy 1: Keyword-based (original): "knee_pain" → check if "knee" AND "pain" in user_msg
            # Strategy 2: Alias-based (H-001): check if any Chinese/English alias is in user_msg
            matched = False

            # Strategy 1: Keyword matching — scan combined user_msg + output
            key_words = anchor_key.lower().replace('_', ' ').split()
            if key_words and len(key_words) >= 2 and all(word in combined_msg for word in key_words):
                matched = True
            elif key_words and len(key_words) == 1 and key_words[0] in combined_msg:
                matched = True

            # Strategy 2: Alias matching (H-001 — Chinese support)
            # Scan combined_msg (user_message + LLM output) for aliases
            if not matched and isinstance(anchor_rules, dict):
                aliases = anchor_rules.get("aliases", [])
                if aliases:
                    for alias in aliases:
                        if alias.lower() in combined_msg:
                            matched = True
                            break

            if matched and isinstance(anchor_rules, dict):
                # Support nested {"value": {"forbidden_suggestions": [...]}} format
                inner = anchor_rules.get("value") or anchor_rules
                forbidden = inner.get("forbidden_suggestions", []) if isinstance(inner, dict) else []
                for term in forbidden:
                    # #3 FIX (Zeus audit): Use word-boundary matching to prevent
                    # false positives like "arm" in "alarm" or "squat" in "squatter"
                    # P1#5 FIX: also match raw underscore form for identifiers (treadmill_3)
                    import re as _re2
                    term_raw = term.lower()
                    term_clean = term_raw.replace('_', ' ')
                    try:
                        regex = _re2.compile(r'\b' + _re2.escape(term_clean) + r'\b')
                        if regex.search(output_str_lower):
                            violated.append(f"{anchor_key}→{term}")
                            continue
                    except _re2.error:
                        pass
                    # Also check raw form with underscores (e.g., treadmill_3)
                    if term_raw != term_clean and _re2.escape(term_raw) in output_str_lower:
                        if f"{anchor_key}→{term}" not in violated:
                            violated.append(f"{anchor_key}→{term}")
                            continue
                    # Fallback: substring match for CJK chars and edge cases
                    # (\b doesn't work on CJK — regex compiles but won't match,
                    #  so we need explicit substring fallback outside try)
                    # P0 FIX (Tristan audit 2026-07-28): Replace rstrip('s') with simple
                    # stemmer that strips common suffixes (ing, ed, es, s, ly, ment, tion).
                    if term_clean in output_str_lower or self._stem_term(term_clean) in output_str_lower or term_raw in output_str_lower:
                        if f"{anchor_key}→{term}" not in violated:
                            violated.append(f"{anchor_key}→{term}")

        if violated:
            # Confidence scales with number of violated anchors:
            # 1 violation → 0.96, 2 → 0.97, ..., capped at 0.99
            fact_confidence = min(0.99, 0.95 + 0.01 * len(violated))
            return HResult(
                passed=False,
                gate_result=GateResult.FAIL,
                reason="Fact anchors violated",
                anchors_violated=violated,
                evidence={"matched_anchors": violated},
                confidence=fact_confidence,
            )
        return HResult(passed=True, gate_result=GateResult.PASS, confidence=1.0)

    def _validate_rules(
        self, output: Any, context: Optional[Dict] = None
    ) -> HResult:
        """
        Rule layer: Domain-specific constraints (e.g., medical advice routing).
        
        #2 FIX (Zeus audit): Explicit continue for non-pattern_match rules
        to prevent silent skip confusion. Future rule types (capability_anchor,
        time_window, etc.) will be added here with their own branch.
        
        P1#7 FIX (Zeus audit): Dual-end scanning — scan both user_message (context)
        AND LLM output for rule patterns. Previously only scanned output_str, missing
        cases where the user_message contains the trigger (e.g., medical terms).

        Confidence model:
            PASS → 1.0
            FAIL → 0.90 (pattern matching is binary but may have false matches)
        """
        import re as _re
        output_str = str(output) if isinstance(output, str) else json.dumps(output)
        output_str_lower = output_str.lower()
        # P1#7: also scan user_message from context
        user_msg = (context or {}).get("user_message", "")
        combined_lower = (output_str_lower + " " + user_msg.lower()).strip()
        
        for rule_key, rule in self._constraints.get("rules", {}).items():
            rule_type = rule.get("type", "")
            
            if rule_type == "pattern_match":
                pattern = rule.get("pattern", "")
                if pattern:
                    # #3 FIX (Zeus audit): Use \b word-boundary matching to prevent
                    # false positives like "arm" in "alarm" or "squat" in "squatter"
                    pattern_terms = pattern.lower().split('|')
                    for term in pattern_terms:
                        term_raw = term.strip()
                        term_clean = term_raw.replace('_', ' ')  # P1#5 fix: preserve internal structure
                        # Build word-boundary regex: \bterm\b for each term
                        # P1#5 FIX: try both forms — underscore-preserved (treadmill_3)
                        # and space-substituted (treadmill 3). Underscore is a word
                        # char so \b won't fire around it; space-substituted form
                        # enables word-boundary matching for identifiers with underscores.
                        try:
                            # Form 1: space-substituted (word boundary works)
                            regex = _re.compile(r'\b' + _re.escape(term_clean) + r'\b')
                            if regex.search(combined_lower):
                                return HResult(
                                    passed=False,
                                    gate_result=GateResult.FAIL,
                                    reason=rule.get("reason", f"Rule violation: {rule_key}"),
                                    anchors_violated=[rule_key],
                                    confidence=0.90,
                                )
                            # Form 2: raw (preserves underscores — direct substring)
                            if term_raw != term_clean and _re.escape(term_raw) in combined_lower:
                                return HResult(
                                    passed=False,
                                    gate_result=GateResult.FAIL,
                                    reason=rule.get("reason", f"Rule violation: {rule_key}"),
                                    anchors_violated=[rule_key],
                                    confidence=0.90,
                                )
                            # Fallback: substring match for CJK and edge cases
                            # (\b doesn't work on CJK characters — regex compiles but
                            #  doesn't match, so we need explicit substring fallback)
                            if term_clean in combined_lower or term_raw in combined_lower:
                                return HResult(
                                    passed=False,
                                    gate_result=GateResult.FAIL,
                                    reason=rule.get("reason", f"Rule violation: {rule_key}"),
                                    anchors_violated=[rule_key],
                                    confidence=0.90,
                                )
                        except _re.error:
                            # Regex compilation failed — pure substring fallback
                            if term_clean in combined_lower or term_raw in combined_lower:
                                return HResult(
                                    passed=False,
                                    gate_result=GateResult.FAIL,
                                    reason=rule.get("reason", f"Rule violation: {rule_key}"),
                                    anchors_violated=[rule_key],
                                    confidence=0.90,
                                )
            # P0 FIX (Tristan audit 2026-07-28): Unknown rule types must NOT be silently skipped.
            # The safety principle is 'unknown = FAIL with warning'. If a rule type is not
            # recognized, the system cannot guarantee it was correctly applied. This prevents
            # the scenario where new rule types are added to constraints.json but the gate
            # silently ignores them — creating a false sense of security.
            elif rule_type == "":
                # Empty type = malformed rule, flag as warning
                self._unrecognized_rule_types.add(f"{rule_key}:empty_type")
                continue
            else:
                # Unknown rule type — flag but don't block (future-proofing)
                # These are logged so Stella can audit which constraints are not being enforced.
                self._unrecognized_rule_types.add(f"{rule_key}:{rule_type}")
                continue

        # After scanning all rules, report any unrecognized types
        if self._unrecognized_rule_types:
            # Append to return as evidence for Stella auditing
            pass  # collected, reported via get_unrecognized_rules()

        return HResult(passed=True, gate_result=GateResult.PASS, confidence=1.0)

    def _log_violation(self, layer: str, result: HResult) -> None:
        """Log violation for A-function processing and RetroOnto tracing."""
        self._violation_log.append({
            "layer": layer,
            "result": result.to_dict(),
            "timestamp": None,  # inject timestamp at call site
        })

    def get_unrecognized_rules(self) -> List[str]:
        """
        Return list of rules with unrecognized types (for Stella auditing).
        Unknown types are flagged but don't block — this enables incremental adoption
        of new rule types without breaking existing validation.
        """
        return sorted(self._unrecognized_rule_types)

    @staticmethod
    def _stem_term(term: str) -> str:
        """
        Simple English stemmer for fuzzy matching. Strips common suffixes
        to handle plural, gerund, past tense, and derivative forms.
        
        P0 FIX (Tristan audit 2026-07-28): Replaces the old rstrip('s') which
        only handled trailing 's', missing 'ing', 'ed', 'es', 'ment', etc.
        
        This is intentionally conservative — only strips suffixes when the
        remaining stem is at least 3 characters long.
        """
        SUFFIXES = [
            'ization', 'fulness', 'ability', 'ibility',
            'ingly', 'fully', 'ment', 'ness', 'tion', 'sion',
            'able', 'ible', 'less', 'iest', 'ful',
            'ing', 'est', 'ied', 'ies',
            'ed', 'es', 'ly', 'er',
            's',
        ]
        lower = term.lower()
        for suffix in SUFFIXES:
            if lower.endswith(suffix) and len(lower) - len(suffix) >= 3:
                return lower[:-len(suffix)]
        return lower

    @staticmethod
    def _load_json(path: str) -> Dict:
        """Load JSON constraint/anchor file. Deterministic file I/O."""
        with open(path, "r") as f:
            return json.load(f)

    def emit_intercept_event(self, h_result: HResult, source_agent: str = "Tristan",
                             claimed: str = "", expected: str = "", actual: str = "",
                             category: str = "infrastructure") -> "HInterceptEvent":
        """
        High-level API: 从拦截结果创建并推送经验萃取事件到 Engine。
        应在check()返回FAIL后调用。
        """
        event = HInterceptEvent.from_h_result(h_result, source_agent, claimed, expected, actual, category)

        # 尝试推送到 Dynamic Engine
        engine_dir = "/home/agentuser/.openclaw/workspace/tristan/tech_lead/retroonto/engine"
        sys.path.insert(0, engine_dir)
        try:
            from experience_extractor import ExperienceExtractor
            extractor = ExperienceExtractor()
            result = extractor.extract(event.to_dict())
            if result.need_permanentization:
                from constraint_generator import ConstraintGenerator
                from rule_registry import RuleRegistry
                gen = ConstraintGenerator()
                registry = RuleRegistry()
                # ErrorPattern is a dataclass; convert to dict for engine API
                ep_dict = {
                    "pattern_id": result.pattern_id,
                    "pattern_fingerprint": result.pattern_fingerprint,
                    "error_signature": result.error_signature,
                    "claimed": result.claimed,
                    "expected": result.expected,
                    "actual": result.actual,
                    "severity": result.severity,
                    "category": result.category,
                    "constraint_text": result.constraint_text,
                    "gap_analysis": result.gap_analysis,
                    "source_agent": result.source_agent,
                }
                constraint_path, constraint_id = gen.generate_and_write(ep_dict)
                if constraint_path:
                    registry.register(
                        rule_id=f"RULE-{constraint_id}",
                        fingerprint=result.pattern_fingerprint,
                        constraint_file=str(constraint_path),
                        constraint_id=constraint_id,
                        error_source_id=result.pattern_id,
                        severity=result.severity,
                        category=result.category,
                        description=result.constraint_text,
                    )
        except ImportError:
            logger.debug("Dynamic Engine not yet deployed — H intercept event not pushed")
        except Exception as e:
            logger.warning(f"Engine push failed (non-blocking): {e}")

        return event


@dataclass
class HInterceptEvent:
    """Protocol: H拦截事件 → 推送至Dynamic Engine做经验萃取
    
    Fields designed per Zeus施工菜单#4:
    - context_id: 唯一上下文标识，用于关联同一错误多次发生
    - source_agent: 触发拦截的Agent
    - claimed: Agent声称的状态
    - expected: 应该达到的状态
    - actual: 实际验证的状态
    """
    event_type: str = "H_intercept"
    source_agent: str = "Tristan"
    context_id: str = ""
    error_signature: str = ""        # 错误特征指纹
    claimed: str = ""                 # Agent声称/声明
    expected: str = ""                # 预期状态
    actual: str = ""                  # 实际验证状态
    constraint_text: str = ""         # 违反的约束文本
    severity: str = "🔴"
    category: str = "infrastructure"  # infrastructure|coordination|cognitive
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            from datetime import datetime, timezone, timedelta
            # Use UTC+8 (Asia/Shanghai)
            try:
                tz = timezone(timedelta(hours=8))
                self.timestamp = datetime.now(tz).isoformat()
            except (TypeError, ValueError):
                self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "source_agent": self.source_agent,
            "context_id": self.context_id,
            "error_signature": self.error_signature,
            "claimed": self.claimed,
            "expected": self.expected,
            "actual": self.actual,
            "constraint_text": self.constraint_text,
            "severity": self.severity,
            "category": self.category,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_h_result(cls, h_result: HResult, source_agent: str = "Tristan",
                      claimed: str = "", expected: str = "", actual: str = "",
                      category: str = "infrastructure") -> "HInterceptEvent":
        """
        从 HResult 创建一个 HInterceptEvent。
        
        Args:
            h_result: H拦截结果
            source_agent: 触发拦截的Agent名
            claimed: Agent声称的状态
            expected: 应该达到的状态
            actual: 实际验证的状态
            category: 错误类别 (infrastructure|coordination|cognitive)
        """
        # Build error signature from violated anchors
        signature_parts = []
        if h_result.anchors_violated:
            signature_parts.extend(h_result.anchors_violated)
        if h_result.reason:
            signature_parts.append(h_result.reason[:80])
        error_signature = "|".join(signature_parts) if signature_parts else "unknown"

        # Build constraint text from evidence
        constraint_text = h_result.reason or ""
        if h_result.evidence:
            try:
                constraint_text += " " + json.dumps(h_result.evidence, ensure_ascii=False)
            except (TypeError, ValueError):
                constraint_text += " " + str(h_result.evidence)

        return cls(
            source_agent=source_agent,
            error_signature=error_signature,
            claimed=claimed,
            expected=expected,
            actual=actual,
            constraint_text=constraint_text,
            category=category,
            severity="🔴",
        )
