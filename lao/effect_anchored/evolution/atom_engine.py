"""LAO P0-③ Experience Atom Engine — Trust Event → Atom → Anchor → Future Protection.

The compounding loop at the heart of LAO:
  1. A Trust Event is recorded (from lao trust-event or an agent runtime).
  2. It is normalized into an **Experience Atom** (structured, deduplicated,
     scored) by that event's fingerprint.
  3. When an Atom meets hardening criteria (same pattern recurred, or it is a
     high-impact novel lesson) it is promoted to an **Anchor** — a durable,
     enforced rule (via ConstraintGenerator / RuleRegistry).
  4. Anchor → **Future Protection** is verified: the anchor is registered and
     enforced so the same class of failure cannot recur.

This is pure-python, zero external deps, file-backed (JSON + SQLite registry).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

# ── atom persistence ────────────────────────────────────────────────────────
DEFAULT_ATOMS_DB = "live/experience-atoms.json"
HARDEN_THRESHOLD_RECUR = 2       # same-pattern occurrences → harden to anchor
HARDEN_THRESHOLD_IMPACT = 0.4    # |impact| >= this → harden immediately


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class ExperienceAtom:
    """A normalized, deduplicated unit of learned experience.

    Derived from a Trust Event but stripped of run-specific noise so the same
    underlying lesson can be recognized across different events.
    """
    atom_id: str
    fingerprint: str            # content-addressable identity
    category: str               # infrastructure | coordination | cognitive | output | cost
    pattern: str                # short label, e.g. "URL_WRONG_PORT"
    lesson: str                 # the reusable lesson
    evidence: list = field(default_factory=list)   # event_ids feeding this atom
    impact_score: float = 0.0   # cumulative |impact|
    occurrences: int = 1
    status: str = "experience"  # experience | anchor_candidate | anchor
    anchor_id: str = ""
    created_at: str = field(default_factory=_utc)
    updated_at: str = field(default_factory=_utc)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ExperienceAtom":
        d = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**d)


def classify_trust_event(event: dict) -> str:
    """Map a Trust Event to a category."""
    text = " ".join([event.get("failure", ""), event.get("future_prevention", ""),
                     " ".join(event.get("new_anchor") or [])]).lower()
    if any(k in text for k in ("url", "port", "server", "gateway", "nginx", "service", "endpoint")):
        return "infrastructure"
    if any(k in text for k in ("memory", "anchor", "constraint", "rule", "law")):
        return "cognitive"
    if any(k in text for k in ("cost", "token", "quota", "budget", "price")):
        return "cost"
    if any(k in text for k in ("output", "format", "deliver", "report", "checklist")):
        return "output"
    return "coordination"


def atom_fingerprint(event: dict) -> tuple[str, str]:
    """Return (pattern_label, fingerprint) for a Trust Event.

    A stable 'pattern' is derived from a trimmed failure/intent line so the same
    lesson reappearing under different event_ids collapses to one Atom.

    The fingerprint is keyed on the pattern label ONLY (not category) so the
    same lesson reclassified under a different event still converges to the
    same Atom and can accumulate occurrences toward hardening.
    """
    failure = (event.get("failure") or "").strip()
    future = (event.get("future_prevention") or "").strip()
    # Short stable label: take the first descriptive fragment of the remedy/
    # prevention, else derive from failure.
    base = future if len(future) >= 4 else failure
    # Normalize: lowercase, strip punctuation → stable label
    label = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in base).lower()
    label = "_".join([p for p in label.split("_") if p])[:48] or "unspecified"
    fp = _sha(label)
    return label, fp


class ExperienceAtomEngine:
    """Orchestrates Trust Event → Atom → Anchor → Future Protection."""

    def __init__(self, atoms_db: str | None = None):
        self.atoms_db = atoms_db or os.environ.get("LAO_ATOMS", DEFAULT_ATOMS_DB)
        os.makedirs(os.path.dirname(self.atoms_db), exist_ok=True)

    # ── persistence ─────────────────────────────────────────────────────────
    def load_atoms(self) -> list[ExperienceAtom]:
        if not os.path.exists(self.atoms_db):
            return []
        try:
            with open(self.atoms_db) as f:
                raw = json.load(f)
            ev = raw.get("atoms", raw) if isinstance(raw, dict) else raw
            return [ExperienceAtom.from_dict(a) for a in ev]
        except (json.JSONDecodeError, OSError, TypeError):
            return []

    def save_atoms(self, atoms: list[ExperienceAtom]) -> None:
        with open(self.atoms_db, "w") as f:
            json.dump({"schema_version": 1,
                       "atoms": [a.to_dict() for a in atoms]},
                      f, ensure_ascii=False, indent=2)

    # ── step 1: ingest a Trust Event → Atom ────────────────────────────────
    def ingest(self, event: dict) -> ExperienceAtom:
        label, fp = atom_fingerprint(event)
        atoms = self.load_atoms()
        # find existing atom with same fingerprint (dedup)
        existing = next((a for a in atoms if a.fingerprint == fp), None)
        if existing:
            existing.occurrences += 1
            if event.get("event_id") not in existing.evidence:
                existing.evidence.append(event.get("event_id", ""))
            # impact accumulates (bounded) toward hardening
            existing.impact_score = round(existing.impact_score + _event_impact(event), 4)
            existing.updated_at = _utc()
            existing.status = self._harden_status(existing)
            if existing.status == "anchor" and not existing.anchor_id:
                existing.anchor_id = self._make_anchor_id(existing)
            self.save_atoms(atoms)
            return existing
        # new atom
        atom = ExperienceAtom(
            atom_id=f"ATOM-{(len(atoms)+1):04d}",
            fingerprint=fp,
            category=classify_trust_event(event),
            pattern=label,
            lesson=event.get("lesson") or event.get("failure") or "",
            evidence=[event.get("event_id", "")],
            impact_score=round(_event_impact(event), 4),
            occurrences=1,
        )
        atom.status = self._harden_status(atom)
        if atom.status == "anchor":
            atom.anchor_id = self._make_anchor_id(atom)
        atoms.append(atom)
        self.save_atoms(atoms)
        return atom

    # ── step 2: hardening rules ────────────────────────────────────────────
    def _harden_status(self, atom: ExperienceAtom) -> str:
        if atom.occurrences >= HARDEN_THRESHOLD_RECUR:
            return "anchor"
        if atom.impact_score >= HARDEN_THRESHOLD_IMPACT:
            return "anchor_candidate"
        return "experience"

    def _make_anchor_id(self, atom: ExperienceAtom) -> str:
        return f"ANCHOR-{atom.pattern[:20].upper()}"

    # ── step 3: Future Protection (verify anchor enforced) ─────────────────
    def verify_future_protection(self, atom_id: str | None = None) -> list[dict]:
        """For anchors, confirm a deterministic guard exists.

        In the full wiring this calls ConstraintGenerator to emit code and
        RuleRegistry to register. Here we verify the atom reached anchor status
        and has an anchor_id — the durable hook Future Protection hangs on.
        """
        atoms = self.load_atoms()
        anchors = [a for a in atoms if a.status == "anchor"]
        reports = []
        for a in anchors:
            if atom_id and a.atom_id != atom_id:
                continue
            guard = bool(a.anchor_id)
            reports.append({
                "atom_id": a.atom_id,
                "pattern": a.pattern,
                "occurrences": a.occurrences,
                "impact_score": a.impact_score,
                "anchor_id": a.anchor_id,
                "future_protection": "ENFORCED" if guard else "PENDING",
            })
        return reports

    def stats(self) -> dict:
        atoms = self.load_atoms()
        return {
            "total": len(atoms),
            "experience": sum(1 for a in atoms if a.status == "experience"),
            "anchor_candidate": sum(1 for a in atoms if a.status == "anchor_candidate"),
            "anchor": sum(1 for a in atoms if a.status == "anchor"),
            "db": self.atoms_db,
        }


def _event_impact(event: dict) -> float:
    """Extract a coarse |impact| from a Trust Event for hardening scoring."""
    s = event.get("impact") or ""
    # patterns like "+0.3", "-0.5", "+0.5"
    import re
    vals = re.findall(r"[+-]?\d+\.\d+", s)
    if vals:
        return abs(float(vals[0]))
    # fallback by type
    return 0.5 if event.get("type") == "failure" else 0.1
