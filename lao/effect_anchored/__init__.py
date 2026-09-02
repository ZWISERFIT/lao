"""
Lineage-Anchored Ontology Engine
=================================

Six deterministic libraries that operate outside the LLM's reasoning space.
Formerly "Effect-Anchored Ontology" — renamed 2026-07-29 per founder directive:
the ontology anchors on agent decision lineage, not just observed effects.

Layers:
    L1 Routing — 31-model autonomous selection, cost-aware task → model routing
    H  - HallucinationGate: schema/fact validation, outside LLM probability space
    M  - MemoryAnchor: hard-coded anchor retrieval, deterministic (not semantic)
    C  - ContextRebuilder: structured event recording + post-compaction reconstruction
    A  - AdaptiveConstraint: violation → equivalence class → rule generation
    E  - EffectAnchoring: capability trust scoring from observed effects (not model claims)
    S  - SelfAudit: meta-audit of the rule system itself
    L4 - InteractionGate: human-in-the-loop confirmation for low-confidence results
         UI Backends: CLI, SDK, API confirmation interfaces

Built from 120+ days of 9-agent autonomous operation in a physical retail store.
"""

__version__ = "0.2.0"

# Ontology stage: honest labeling of current data sources
# phase_1 = static rules + manual error archiving + bash constraint generation
# phase_2 = dynamic experience extraction + Python code generation + rule registry
ONTOLOGY_STAGE = "phase_1"
__all__ = [
    "HallucinationGate",
    "HInterceptEvent",
    "MemoryAnchor",
    "ContextRebuilder",
    "AdaptiveConstraint",
    "EffectAnchoring",
    "SelfAudit",
    "TaskClassifier",
    "ModelRouter",
    "RouteSelection",
    "CostTracker",
    # L4 Interaction Layer
    "InteractionGate",
    "ConfirmationResult",
    "CLIBackend",
    "SDKBackend",
    "APIBackend",
    # L3 Evolution
    "ExperienceExtractor",
    "ErrorPattern",
    "ExtractionInput",
    "ConstraintGenerator",
    "RuleRegistry",
    "ExperienceAtomEngine",
    "PreferenceFirewall",
    "FirewallResult",
    "ExperienceAtom",
    # 三层Loop闭环 (2026-08-16 创始人令)
    "ExperienceLoop",
    "FeedbackBus",
    "FeedbackEvent",
    "ErrorImmunity",
    "ExperienceContract",
    "ExperienceContractRegistry",
    "ErgeWriter",
]
from .hallucination_gate import HallucinationGate, HInterceptEvent, HResult, GateResult
from .memory_anchor import MemoryAnchor, MResult
from .context_rebuilder import ContextRebuilder, Event
from .adaptive_constraint import AdaptiveConstraint, Violation, DerivedRule
from .effect_anchoring import EffectAnchoring, CapabilityObservation, TrustProfile
from .self_audit import SelfAudit, AuditFinding, AuditReport
from .routing.task_classifier import TaskClassifier
from .routing.model_router import ModelRouter, RouteSelection
from .routing.cost_tracker import CostTracker

# L3 Evolution
from .evolution import ExperienceExtractor, ErrorPattern, ExtractionInput, ConstraintGenerator, RuleRegistry, ExperienceAtomEngine, ExperienceAtom
from .preference_firewall import PreferenceFirewall, FirewallResult, FirewallVerdict

# L4 Interaction Layer
from .interaction import InteractionGate, ConfirmationResult
from .interaction import CLIBackend, SDKBackend, APIBackend

# 三层Loop闭环 (2026-08-16 创始人令): L1命中率↔L2经验工厂↔L3确权交易
from .feedback_bus import FeedbackBus, FeedbackEvent, ErrorImmunity
from .experience_contract import ExperienceContract, ExperienceContractRegistry
from .erge_writer import ErgeWriter
from .experience_loop import ExperienceLoop

