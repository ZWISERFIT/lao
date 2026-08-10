"""
Effect Anchoring (E-Function)
=============================

Trust scoring based on OBSERVED effects, not MODEL CLAIMS.

Architecture Principle:
    "not pretending every model has identical semantics" — richardchen874-sys
    "honest provider/model capability metadata"

    Every model claims to support streaming, tools, structured output.
    Only observation tells you which ones ACTUALLY work, under what
    conditions, with what failure modes.

120-Day Lesson:
    Our streaming and batch shared the same timeout profile.
    DeepSeek's streaming was fine at 900s, but Qwen's batch timeout
    was silently failing at the same setting. The model "claimed" to
    support batch at our config — the observed effect said otherwise.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict
import json


@dataclass
class CapabilityObservation:
    """A single observation of a model's capability in production."""
    capability: str  # "streaming" | "tools" | "structured_output" | "timeout"
    provider: str
    model: str
    success: bool
    latency_ms: Optional[float] = None
    error_type: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[str] = None


@dataclass
class TrustProfile:
    """Accumulated trust data for a model×capability pair."""
    provider: str
    model: str
    capability: str
    total_observations: int = 0
    successes: int = 0
    failures: int = 0
    mean_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    failure_modes: Dict[str, int] = field(default_factory=dict)  # error_type → count
    trust_score: float = 1.0  # starts high, drops with failures
    last_updated: Optional[str] = None
    model_claim: Optional[str] = None  # what the model VENDOR says
    observed_reality: Optional[str] = None  # what we ACTUALLY see

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "capability": self.capability,
            "total_observations": self.total_observations,
            "successes": self.successes,
            "failures": self.failures,
            "mean_latency_ms": self.mean_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "failure_modes": self.failure_modes,
            "trust_score": round(self.trust_score, 4),
            "last_updated": self.last_updated,
            "model_claim": self.model_claim,
            "observed_reality": self.observed_reality,
        }


class EffectAnchoring:
    """
    E-Function: Build trust from observations, not model marketing.

    Key design decisions:
        - Trust decays FAST on failure, recovers SLOWLY with success.
          (asymmetric: trust is harder to rebuild than destroy)
        
        - Observations are NEVER aggregated across models/providers.
          DeepSeek's streaming trust ≠ Qwen's streaming trust.
          
        - Failure modes are categorized and tracked independently.
          "timeout" ≠ "schema_error" ≠ "rate_limit" — each has its own
          trust profile within the same capability.

    Usage:
        effect = EffectAnchoring()
        effect.record(CapabilityObservation(
            capability="streaming",
            provider="deepseek",
            model="v4-pro",
            success=True,
            latency_ms=3200,
        ))
        profile = effect.get_profile("deepseek", "v4-pro", "streaming")
        # → TrustProfile(trust_score=0.98, p95_latency_ms=4500, ...)
    """

    def __init__(
        self,
        decay_rate: float = 0.3,  # how fast trust drops on failure
        recovery_rate: float = 0.02,  # how fast trust recovers on success
        min_observations: int = 10,  # minimum N before trust score is meaningful
        profiles_path: Optional[str] = None,
    ):
        self._decay = decay_rate
        self._recovery = recovery_rate
        self._min_n = min_observations
        self._profiles: Dict[str, TrustProfile] = {}  # key = provider:model:capability
        self._latencies: Dict[str, List[float]] = defaultdict(list)
        self._path = profiles_path

        if profiles_path:
            self._load()

    def record(self, observation: CapabilityObservation) -> TrustProfile:
        """
        Record a single capability observation and update trust score.

        Args:
            observation: A single observed success or failure.

        Returns:
            Updated TrustProfile for the model×capability pair.
        """
        key = self._make_key(
            observation.provider, observation.model, observation.capability
        )
        profile = self._profiles.get(key)

        if profile is None:
            profile = TrustProfile(
                provider=observation.provider,
                model=observation.model,
                capability=observation.capability,
            )
            self._profiles[key] = profile

        # Update counts
        profile.total_observations += 1
        if observation.success:
            profile.successes += 1
        else:
            profile.failures += 1
            if observation.error_type:
                profile.failure_modes[observation.error_type] = (
                    profile.failure_modes.get(observation.error_type, 0) + 1
                )

        # Update latency
        if observation.latency_ms is not None:
            self._latencies[key].append(observation.latency_ms)
            profile.mean_latency_ms = sum(self._latencies[key]) / len(self._latencies[key])
            profile.p95_latency_ms = self._compute_p95(self._latencies[key])

        # Update trust score (asymmetric: decay fast, recover slow)
        if profile.total_observations < self._min_n:
            # Not enough data — trust score is provisional
            profile.trust_score = 0.5
        elif observation.success:
            profile.trust_score = min(
                1.0, profile.trust_score + self._recovery
            )
        else:
            profile.trust_score = max(
                0.0, profile.trust_score - self._decay
            )

        if self._path:
            self._save()

        return profile

    def get_profile(
        self, provider: str, model: str, capability: str
    ) -> Optional[TrustProfile]:
        """Get trust profile for a specific model×capability."""
        key = self._make_key(provider, model, capability)
        return self._profiles.get(key)

    def get_all_profiles(
        self, provider: Optional[str] = None, capability: Optional[str] = None
    ) -> List[TrustProfile]:
        """Get all profiles, optionally filtered."""
        results = []
        for profile in self._profiles.values():
            if provider and profile.provider != provider:
                continue
            if capability and profile.capability != capability:
                continue
            results.append(profile)
        return results

    def compare(
        self, capability: str, providers: Optional[List[str]] = None
    ) -> Dict[str, TrustProfile]:
        """Compare trust profiles for a capability across providers."""
        results = {}
        for key, profile in self._profiles.items():
            if profile.capability != capability:
                continue
            if providers and profile.provider not in providers:
                continue
            results[profile.provider] = profile
        return results

    def export_provider_matrix(self) -> List[Dict[str, Any]]:
        """
        Export the full provider capability matrix.
        
        This IS the "honest provider/model capability metadata" that
        Richard described. Not what the model vendor claims — what we observed.
        """
        return [p.to_dict() for p in self._profiles.values()]

    @staticmethod
    def _make_key(provider: str, model: str, capability: str) -> str:
        return f"{provider}:{model}:{capability}"

    @staticmethod
    def _compute_p95(latencies: List[float]) -> float:
        if not latencies:
            return 0.0
        sorted_lat = sorted(latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    def _load(self) -> None:
        if self._path:
            try:
                with open(self._path, "r") as f:
                    data = json.load(f)
                    for p_data in data.get("profiles", []):
                        profile = TrustProfile(**p_data)
                        key = self._make_key(
                            profile.provider, profile.model, profile.capability
                        )
                        self._profiles[key] = profile
            except (FileNotFoundError, json.JSONDecodeError):
                pass

    def _save(self) -> None:
        if self._path:
            profiles_list = [p.to_dict() for p in self._profiles.values()]
            with open(self._path, "w") as f:
                json.dump({"profiles": profiles_list}, f, ensure_ascii=False, indent=2)
