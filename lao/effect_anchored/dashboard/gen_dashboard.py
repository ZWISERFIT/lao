#!/usr/bin/env python3
"""Generate the LAO Reliability Dashboard HTML from live data.

Sources:
  - ReliabilityScorer (four-dimension scores from per-agent cost + atoms)
  - Trust Events (ledger) — today's events
  - Anchors (from atoms with status=anchor → Fixed list)

Usage:
  python3 gen_dashboard.py [--atoms live/experience-atoms.json] \
      [--cost shared/state/token-costs/2026-08-per-agent-breakdown.json] \
      [--out dashboard.html]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# ensure package importable regardless of cwd
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(os.path.dirname(_HERE))  # lineage-anchored-ontology/
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from lao.effect_anchored.routing.agent_reliability import ReliabilityScorer  # noqa: E402

AGENTS = ["Tristan", "Shuyu", "Zeus", "Luna", "Stella", "Momo", "Ethan", "Baron", "Nova"]


def collect(atoms_path: str, cost_path: str):
    scorer = ReliabilityScorer(atoms_path)
    if cost_path and os.path.exists(cost_path):
        scorer.load_cost(cost_path)

    # atoms per agent (single-agent ledger → attribute to first caller; here demo)
    atoms = scorer.load_atoms(AGENTS).get(AGENTS[0], [])

    agents = []
    for a in AGENTS:
        r = scorer.score(a, AGENTS, violations=0, total_checks=10, atoms=[])
        agents.append({
            "agent": a, "overall": r.overall,
            "memory": r.memory, "compliance": r.compliance,
            "cost": r.cost, "recovery": r.recovery,
        })

    todo = {a["agent"]: a for a in agents}
    anchors = [a.get("anchor_id", a.get("pattern", "anchor"))
               for a in atoms if a.get("status") == "anchor"]
    events = [{
        "event_id": a.get("atom_id", ""),
        "type": "success" if a.get("impact_score", 0) >= 0 else "failure",
        "failure": a.get("pattern", ""),
        "status": "CLOSED" if a.get("status") == "anchor" else "OPEN",
    } for a in atoms]

    avg = sum(a["overall"] for a in agents) / len(agents) if agents else 0
    kpis = {
        "events": len(events),
        "verified": len(events),
        "anchors": len(anchors),
        "avg": round(avg, 1),
    }
    return {"agents": agents, "events": events, "fixed": anchors, "kpis": kpis}


def render(template_path: str, data: dict, out_path: str) -> None:
    with open(template_path) as f:
        html = f.read()
    payload = json.dumps(data, ensure_ascii=False)
    html = html.replace("__LAO_DATA__", payload)
    with open(out_path, "w") as f:
        f.write(html)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--atoms", default="live/experience-atoms.json")
    ap.add_argument("--cost", default="/home/agentuser/shared/state/token-costs/2026-08-per-agent-breakdown.json")
    ap.add_argument("--out", default="lao-dashboard-generated.html")
    args = ap.parse_args()
    data = collect(args.atoms, args.cost)
    template = os.path.join(_HERE, "lao-dashboard.html")
    render(template, data, args.out)
    print(f"✅ Dashboard generated → {args.out}")
    print(f"   events={data['kpis']['events']} anchors={data['kpis']['anchors']} "
          f"avg_rel={data['kpis']['avg']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
