#!/usr/bin/env python3
"""
watch_approver.py
Claude Code PermissionRequest hook — sends the request to your Apple Watch
via ntfy.sh and waits for an interactive tap to approve, always-approve, or reject.

Usage (configured automatically by install.sh):
    Registered as a Claude Code PermissionRequest hook. Claude Code pipes JSON
    to stdin; this script outputs a JSON decision to stdout and exits 0.
"""

import http.server
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import urllib.parse
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HOOK_DIR = Path(__file__).parent
CONFIG_PATH = HOOK_DIR / "config.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        _fatal(f"Config not found at {CONFIG_PATH}. Run install.sh first.")
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _fatal(msg: str) -> None:
    """Exit in a way that Claude Code treats as a non-blocking error."""
    print(json.dumps({"error": msg}), file=sys.stderr)
    # Output a deny decision so Claude Code doesn't hang
    _output_decision("deny", reason=f"Watch approver config error: {msg}")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Decision output
# ---------------------------------------------------------------------------

ALWAYS_ALLOW_PERMISSIONS = [{"type": "toolAlwaysAllow"}]


def _output_decision(behavior: str, reason: str = "", always: bool = False) -> None:
    decision: dict = {"behavior": behavior}
    if reason:
        decision["message"] = reason
    if always and behavior == "allow":
        decision["updatedPermissions"] = ALWAYS_ALLOW_PERMISSIONS

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision,
        }
    }
    print(json.dumps(output))


# ---------------------------------------------------------------------------
# Local callback server
# ---------------------------------------------------------------------------

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Tiny HTTP server that listens for one tap from the ntfy action button."""

    result: str | None = None  # "approve" | "always" | "reject"
    _lock = threading.Event()

    def do_GET(self):
        path = self.path.strip("/").lower()
        if path in ("approve", "always", "reject"):
            _CallbackHandler.result = path
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            _CallbackHandler._lock.set()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # Suppress request logs


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_callback_server() -> tuple[http.server.HTTPServer, int]:
    port = _find_free_port()
    # Reset state for this request
    _CallbackHandler.result = None
    _CallbackHandler._lock.clear()
    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


# ---------------------------------------------------------------------------
# ntfy.sh notification
# ---------------------------------------------------------------------------

def _send_ntfy(summary: str, port: int, config: dict) -> None:
    ntfy_cfg = config.get("ntfy", {})
    server = ntfy_cfg.get("server", "https://ntfy.sh").rstrip("/")
    topic = ntfy_cfg.get("topic", "")

    if not topic:
        _fatal("ntfy topic not set in config.json.")

    base_url = f"http://127.0.0.1:{port}"

    headers = {
        "Title": "🤖 Claude wants permission",
        "Priority": "high",
        "Tags": "robot,key",
        # ntfy actions: label, url
        "Actions": (
            f"view, ✅ Approve, {base_url}/approve, clear=true; "
            f"view, 🔁 Always, {base_url}/always, clear=true; "
            f"view, ❌ Reject, {base_url}/reject, clear=true"
        ),
        "Content-Type": "text/plain; charset=utf-8",
    }

    url = f"{server}/{urllib.parse.quote(topic, safe='')}"
    data = summary.encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 201, 204):
                _fatal(f"ntfy.sh returned HTTP {resp.status}")
    except Exception as e:
        _fatal(f"Failed to send ntfy notification: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # 1. Read hook JSON from stdin
    try:
        hook_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        _fatal(f"Invalid JSON from Claude Code: {e}")
        return

    # 2. Load config
    config = load_config()

    # 3. Summarize the request
    try:
        from summarizer import summarize  # type: ignore
        summary = summarize(hook_data, config)
    except Exception:
        # summarizer unavailable — use basic fallback
        tool = hook_data.get("tool_name", "Unknown")
        ti = hook_data.get("tool_input", {})
        cmd = ti.get("command", ti.get("file_path", ""))
        summary = f"{tool}: {str(cmd)[:80]}" if cmd else f"{tool} permission requested"

    # 4. Start local callback server
    server, port = _start_callback_server()

    try:
        # 5. Fire the ntfy notification
        _send_ntfy(summary, port, config)

        # 6. Wait for a tap or timeout
        timeout = config.get("timeout_seconds", 60)
        tapped = _CallbackHandler._lock.wait(timeout=timeout)

        result = _CallbackHandler.result if tapped else None

    finally:
        server.shutdown()

    # 7. Output decision
    if result == "approve":
        _output_decision("allow")
    elif result == "always":
        _output_decision("allow", always=True)
    else:
        # reject OR timeout
        timeout_action = config.get("timeout_action", "deny")
        reason = "Rejected from Apple Watch." if result == "reject" else "Approval timed out — defaulting to deny."
        if result is None and timeout_action == "allow":
            _output_decision("allow", reason="Timed out — auto-approved per config.")
        else:
            _output_decision("deny", reason=reason)


if __name__ == "__main__":
    main()
