#!/usr/bin/env python3
"""
Effect-Anchored Ontology Telemetry Receiver
============================================

Accepts POST /ping with JSON body:
    {"version": "0.1.0-alpha", "python_version": "3.12", "anon_id": "a1b2c3d4e5f6"}

Logs to JSON lines file for analytics.
Privacy-preserving: anonymous ID is SHA256 hash, not IP or PII.
"""

import json
import time
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

LOG_DIR = Path("/var/log/effect-anchored-telemetry")
LOG_DIR.mkdir(parents=True, exist_ok=True)


class TelemetryHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/ping":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error": "invalid json"}')
            return

        # Enrich with server timestamp only
        data["received_at"] = int(time.time())

        # Write to today's log file
        today = time.strftime("%Y-%m-%d")
        log_file = LOG_DIR / f"pings-{today}.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(data) + "\n")

        # Also write an aggregated unique-install file
        uniques_file = LOG_DIR / "unique-installs.json"
        uniques = {}
        if uniques_file.exists():
            try:
                uniques = json.loads(open(uniques_file).read())
            except (json.JSONDecodeError, FileNotFoundError):
                pass

        anon_id = data.get("anon_id", "unknown")
        version = data.get("version", "unknown")
        if anon_id not in uniques:
            uniques[anon_id] = {
                "first_seen": data["received_at"],
                "version": version,
                "python_version": data.get("python_version", "unknown"),
            }
            with open(uniques_file, "w") as f:
                json.dump(uniques, f, indent=2)

        # Respond
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress access logs (privacy)


def main():
    port = 8765
    server = HTTPServer(("127.0.0.1", port), TelemetryHandler)
    print(f"Telemetry receiver listening on 127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
