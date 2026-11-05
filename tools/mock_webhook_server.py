#!/usr/bin/env python3
"""Controlled local webhook endpoint for verifying the alert dispatch.

Chapter 4 of the training report exercised the dispatch path against a local
endpoint returning HTTP 200, so the round trip is deterministic and no test
payload ever reaches the live operations channel. This script is that endpoint.

Usage::

    python tools/mock_webhook_server.py --port 8080
    python run_pipeline.py --webhook http://127.0.0.1:8080/v1/compliance/webhook

Useful switches for the induced-failure tests (report T-08 / T-09)::

    --status 500        answer every POST with HTTP 500
    --hang              never answer, so the client's timeout is what fires
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Shared counters, mutated by the request handler below.
STATS: dict[str, int] = {"requests": 0, "bytes": 0, "alerts": 0}


class WebhookHandler(BaseHTTPRequestHandler):
    """Accept a compliance alert payload and acknowledge it with HTTP 200."""

    server_version = "LawyeredMockWebhook/1.0"

    def do_POST(self) -> None:
        """Record the payload and reply according to ``--status``."""
        status = int(getattr(self.server, "reply_status", 200))
        if bool(getattr(self.server, "hang", False)):
            time.sleep(60)  # deliberately exceed the client timeout

        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        alerts = 0
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            alerts = int(payload.get("total_critical", len(payload.get("alerts", []))))
        except (ValueError, UnicodeDecodeError):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":"invalid JSON body"}')
            return

        STATS["requests"] += 1
        STATS["bytes"] += len(raw)
        STATS["alerts"] += alerts
        print(
            f"[MOCK-WEBHOOK] #{STATS['requests']} POST {self.path} "
            f"{len(raw):,} bytes, total_critical={alerts}, "
            f"first={_preview(payload)} -> replying {status}",
            flush=True,
        )

        body = json.dumps(
            {
                "status": "accepted" if status == 200 else "rejected",
                "received": alerts,
                "endpoint": self.path,
            }
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Health probe."""
        body = json.dumps({"ok": True, **STATS}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Silence the default per-request stderr logging; we print our own."""


def _preview(payload: dict[str, object]) -> str:
    """Return a one-line preview of the first alert object in ``payload``."""
    alerts = payload.get("alerts") or [{}]
    first = alerts[0] if isinstance(alerts, list) and alerts else {}
    if not isinstance(first, dict):
        return "-"
    return ", ".join(f"{key}={first.get(key)}" for key in list(first)[:4])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8080, help="TCP port to listen on")
    parser.add_argument(
        "--host", default="0.0.0.0", help="bind address (default: all interfaces)"
    )
    parser.add_argument(
        "--status", type=int, default=200, help="HTTP status to answer with"
    )
    parser.add_argument(
        "--hang",
        action="store_true",
        help="accept the connection but never answer, to exercise the client timeout",
    )
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), WebhookHandler)
    server.reply_status = args.status  # type: ignore[attr-defined]
    server.hang = args.hang  # type: ignore[attr-defined]
    print(
        f"[MOCK-WEBHOOK] listening on http://{args.host}:{args.port}"
        f" (replying HTTP {args.status}{', hanging' if args.hang else ''})",
        flush=True,
    )
    print("[MOCK-WEBHOOK] stop with Ctrl+C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MOCK-WEBHOOK] shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
