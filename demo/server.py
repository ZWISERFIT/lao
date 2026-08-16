#!/usr/bin/env python3
"""
Effect-Anchored Ontology — Interactive Demo Server v1.0

Runs all six functions behind a simple HTTP API + static HTML frontend.
Zero dependencies beyond the standard library + effect_anchored package.

Start:  python demo/server.py
Port:   9001 (default)
"""

import json
import sys
import os
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from io import BytesIO

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from effect_anchored import (
    HallucinationGate, HResult, GateResult,
    MemoryAnchor, MResult,
    ContextRebuilder, Event,
    AdaptiveConstraint, Violation,
    EffectAnchoring, CapabilityObservation,
    SelfAudit, AuditFinding, AuditReport,
    __version__,
)

# Shared state for the demo session
DEMO_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(__file__).resolve().parent.parent
ANCHORS_PATH = str(DATA_DIR / "anchors" / "example_facts.json")
CONSTRAINTS_PATH = str(DATA_DIR / "constraints" / "example_medical_rules.json")


class DemoAPI(BaseHTTPRequestHandler):
    """HTTP API for interactive demo."""

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        return json.loads(body)

    def _serve_static(self, path):
        """Serve static files (HTML, CSS, JS)."""
        safe_path = path.lstrip("/")
        if safe_path == "" or safe_path == "/":
            safe_path = "index.html"

        file_path = DEMO_DIR / "static" / safe_path
        if not file_path.exists():
            self._send_json({"error": "Not found"}, 404)
            return

        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".svg": "image/svg+xml",
        }.get(file_path.suffix, "text/plain")

        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/api/status":
            self._send_json({
                "package": "effect-anchored-ontology",
                "version": __version__,
                "demo_ready": True,
                "functions": ["H", "M", "C", "E", "A", "S"],
            })
            return

        if path == "/api/anchor/keys":
            mem = MemoryAnchor(anchor_db_path=ANCHORS_PATH)
            keys = mem.keys()
            self._send_json({"keys": keys})
            return

        if path == "/api/anchor/lookup":
            qs = self.path.split("?")[-1] if "?" in self.path else ""
            params = dict(p.split("=") for p in qs.split("&") if "=" in p)
            key = params.get("key", "")
            mem = MemoryAnchor(anchor_db_path=ANCHORS_PATH)
            result = mem.lookup(key)
            self._send_json(result.to_dict())
            return

        if path == "/api/effect/profiles":
            # Return sample profiles for demo
            profiles = _build_demo_profiles()
            self._send_json({"profiles": [p.to_dict() for p in profiles]})
            return

        # Serve static file
        self._serve_static(path)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/api/gate/check":
            data = self._read_json()
            gate = HallucinationGate(
                constraints_path=CONSTRAINTS_PATH,
                anchors_path=ANCHORS_PATH,
            )
            result = gate.check(
                llm_output=data.get("output", ""),
                context=data.get("context", {}),
            )
            self._send_json(result.to_dict())
            return

        if path == "/api/rebuilder/record":
            data = self._read_json()
            recon = ContextRebuilder(session_id=data.get("session_id", "demo"))
            evt = Event(
                event_id=data.get("event_id", f"evt_{len(data)}"),
                timestamp=data.get("timestamp", "2026-07-27T00:00:00"),
                speaker=data.get("speaker", "demo"),
                event_type=data.get("event_type", "test"),
                subject=data.get("subject", "Demo event"),
                summary=data.get("summary", ""),
                content_hash=data.get("content_hash", ""),
                parent_events=data.get("parent_events", []),
            )
            recon.record(evt)
            self._send_json({"recorded": evt.event_id, "total": len(recon.export())})
            return

        if path == "/api/adaptive/derive":
            data = self._read_json()
            adaptive = AdaptiveConstraint()
            v = Violation(
                violation_id=data.get("violation_id", "v_demo"),
                layer=data.get("layer", "fact"),
                description=data.get("description", ""),
                llm_output_snippet=data.get("llm_output_snippet", ""),
                anchors_violated=data.get("anchors_violated", []),
            )
            rule = adaptive.derive(v)
            self._send_json({
                "derived_rule": rule.to_dict(),
                "h_export": adaptive.export_for_h_function(),
                "m_export": adaptive.export_for_m_function(),
            })
            return

        if path == "/api/effect/record":
            data = self._read_json()
            effect = _get_effect_store()
            obs = CapabilityObservation(
                capability=data.get("capability", "streaming"),
                provider=data.get("provider", "deepseek"),
                model=data.get("model", "v4-pro"),
                success=data.get("success", True),
                latency_ms=data.get("latency_ms"),
                error_type=data.get("error_type"),
            )
            profile = effect.record(obs)
            self._send_json(profile.to_dict())
            return

        if path == "/api/audit/run":
            data = self._read_json()
            sa = SelfAudit()
            rules = data.get("rules", {
                "r1": {"rule_pattern": "knee_pain|back_pain", "rule_action": "block", "scope": "agent"},
                "r2": {"rule_pattern": "streaming_timeout", "rule_action": "flag", "scope": "system"},
            })
            stats = data.get("rule_stats", {
                "r1": {"triggers": 15, "false_positives": 2, "last_triggered": "2026-07-27T10:00:00"},
                "r2": {"triggers": 0, "false_positives": 0, "last_triggered": "2026-01-01T00:00:00"},
            })
            violations = data.get("violation_patterns", [
                {"pattern": "agent_suggested_leg_press_for_hip_pain", "count": 3}
            ])
            report = sa.audit(rules=rules, rule_stats=stats, violation_patterns=violations)
            self._send_json(report.to_dict())
            return

        self._send_json({"error": "Unknown endpoint"}, 404)


# In-memory demo effect store (survives across requests)
_effect_store = None

def _get_effect_store():
    global _effect_store
    if _effect_store is None:
        _effect_store = EffectAnchoring(min_observations=3)
        # Seed with demo data
        for _ in range(5):
            _effect_store.record(CapabilityObservation(
                capability="streaming", provider="deepseek", model="v4-pro",
                success=True, latency_ms=2800
            ))
        _effect_store.record(CapabilityObservation(
            capability="streaming", provider="deepseek", model="v4-pro",
            success=False, error_type="timeout", latency_ms=8500
        ))
        for _ in range(3):
            _effect_store.record(CapabilityObservation(
                capability="structured_output", provider="qwen", model="vl-max",
                success=False, error_type="schema_error", latency_ms=4200
            ))
    return _effect_store


def _build_demo_profiles():
    """Build demo effect profiles for the frontend."""
    return list(_get_effect_store().get_all_profiles())


def main():
    port = int(os.environ.get("PORT", 9001))
    host = os.environ.get("HOST", "127.0.0.1")

    server = HTTPServer((host, port), DemoAPI)

    print(f"""
╔═══════════════════════════════════════════════════════════╗
║  Effect-Anchored Ontology — Demo Server v{__version__:<7}      ║
╠═══════════════════════════════════════════════════════════╣
║  Interactive Demo:  http://{host}:{port}                   ║
║  API Base:          http://{host}:{port}/api/               ║
║                                                         ║
║  Endpoints:                                              ║
║    GET  /api/status        — Version & status            ║
║    POST /api/gate/check    — H-Function (Hallucination)  ║
║    GET  /api/anchor/lookup — M-Function (Memory)         ║
║    POST /api/rebuilder/record — C-Function (Context)     ║
║    POST /api/adaptive/derive  — A-Function (Adaptive)    ║
║    POST /api/effect/record    — E-Function (Effect)      ║
║    POST /api/audit/run        — S-Function (SelfAudit)   ║
║                                                         ║
║  Press Ctrl+C to stop.                                   ║
╚═══════════════════════════════════════════════════════════╝
""")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDemo server stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
