"""LAO P0-② Agent Reliability Score engine.

Scoring on four dimensions, each 0-100:
  Memory     — evidence of durable anchors/atoms (the compounding base)
  Compliance — adherence to Safety Gate / self-audit (no violations)
  Cost       — token efficiency (inverse of per-agent cost vs peers)
  Recovery   — speed/completeness of repair after failures

Aggregates Trust Events (P0-③ atoms) + Stella's per-agent cost data +
Safety Gate compliance signals. Pure python, file-backed.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

# ── defaults / overridable ─────────────────────────────────────────────────
DEFAULT_ATOMS = "live/experience-atoms.json"
DEFAULT_COST = None  # injected per-agent breakdown


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ReliabilityReport:
    agent: str
    memory: float = 0.0
    compliance: float = 0.0
    cost: float = 0.0
    recovery: float = 0.0
    overall: float = 0.0
    breakdown: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)      # today's trust events
    fixed: list = field(default_factory=list)       # fixed list (anchors)
    timestamp: str = field(default_factory=_utc)

    def to_dict(self) -> dict:
        return asdict(self)


class ReliabilityScorer:
    """Computes the four-dimension reliability score from available signals."""

    def __init__(self, atoms_db: str | None = None):
        self.atoms_db = atoms_db or os.environ.get("LAO_ATOMS", DEFAULT_ATOMS)
        self.cost_data: dict = {}

    def load_cost(self, path: str | None = None) -> dict:
        """Load per-agent token cost breakdown (Stella format)."""
        if path and os.path.exists(path):
            with open(path) as f:
                self.cost_data = json.load(f)
        return self.cost_data

    def load_atoms(self, agents: list[str], atoms_db: str | None = None) -> dict[str, list]:
        """Return {agent: [atoms]} from the atom ledger."""
        db = atoms_db or self.atoms_db
        if not os.path.exists(db):
            return {a: [] for a in agents}
        try:
            with open(db) as f:
                raw = json.load(f)
            atoms = raw.get("atoms", raw) if isinstance(raw, dict) else raw
        except (json.JSONDecodeError, OSError):
            return {a: [] for a in agents}
        out = {a: [] for a in agents}
        for at in atoms:
            a = agents[0]  # fallback: single-agent ledger
            out.setdefault(a, []).append(at)
        return out

    # ── dimension scorers (0-100 heuristics) ───────────────────────────────
    def score_memory(self, atoms: list) -> float:
        """Memory: anchors + atoms = compounding base."""
        if not atoms:
            return 10.0
        n_anchor = sum(1 for a in atoms if a.get("status") == "anchor")
        n_atom = len(atoms)
        # anchors strong signal (permanent immunity), atoms show accumulation
        return min(100.0, 15 + n_atom * 5 + n_anchor * 20)

    def score_compliance(self, violations: int = 0, total_checks: int = 0) -> float:
        """Compliance: violations vs checks (Safety Gate / SelfAudit)."""
        if total_checks <= 0:
            return 80.0  # unknown → neutral-high (no evidence of violation)
        violation_rate = violations / total_checks
        return max(0.0, 100.0 - violation_rate * 200)

    def score_cost(self, agent: str, agents: list[str]) -> float:
        """Cost: inverse of agent's cost share vs peers (lower=better)."""
        if not self.cost_data:
            return 70.0  # neutral when no cost data
        costs = self._agent_costs(agents)
        if not costs:
            return 70.0
        max_c = max(costs.values())
        c = costs.get(agent, 0.0)
        # worst cost → 30, best (lowest) → 100
        if max_c <= 0:
            return 90.0
        return round(30 + 70 * (1 - (c / max_c)), 1)

    def score_recovery(self, atoms: list) -> float:
        """Recovery: fraction of failures with repair + anchors (closed loop)."""
        if not atoms:
            return 50.0
        closed = sum(1 for a in atoms
                     if a.get("status") == "anchor" or a.get("evidence"))
        return min(100.0, 40 + (closed / len(atoms)) * 60)

    # ── helpers ────────────────────────────────────────────────────────────
    def _agent_costs(self, agents: list[str]) -> dict[str, float]:
        """Extract per-agent CNY cost from Stella breakdown.

        Keys look like 'ZWISERFIT-Tristan' or 'Tristan' — normalize to the
        bare agent name for matching.
        """
        if not isinstance(self.cost_data, dict):
            return {}
        pac = self.cost_data.get("per_api_key_cost_cny") or {}
        if not isinstance(pac, dict):
            return {}
        # map normalized agent name → cost
        normalized = {}
        for k, v in pac.items():
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            key = str(k).split("-")[-1].strip()  # ZWISERFIT-Tristan → Tristan
            if key and key.lower().startswith("zwiserfit"):
                continue
            normalized[key] = fv
        return normalized

    def score(self, agent: str, agents: list[str] | None = None,
              violations: int = 0, total_checks: int = 0,
              atoms: list | None = None) -> ReliabilityReport:
        if atoms is None:
            atoms_map = self.load_atoms([agent])
            atoms = atoms_map.get(agent, [])
        mem = self.score_memory(atoms)
        comp = self.score_compliance(violations, total_checks)
        cost = self.score_cost(agent, agents or [agent])
        rec = self.score_recovery(atoms)
        # weights: memory .25, compliance .3, cost .2, recovery .25
        overall = round(mem * 0.25 + comp * 0.3 + cost * 0.2 + rec * 0.25, 1)
        anchors = [a for a in atoms if a.get("status") == "anchor"]
        return ReliabilityReport(
            agent=agent,
            memory=round(mem, 1),
            compliance=round(comp, 1),
            cost=round(cost, 1),
            recovery=round(rec, 1),
            overall=round(overall, 1),
            breakdown={
                "weights": {"memory": 0.25, "compliance": 0.3, "cost": 0.2, "recovery": 0.25},
                "memory_details": f"{len(atoms)} atoms / {len(anchors)} anchors",
            },
            events={"today": len(atoms)},
            fixed=[a.get("anchor_id", "") for a in anchors],
        )
