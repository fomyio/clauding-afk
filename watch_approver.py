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
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
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
    """Tiny HTTP server that listens for one tap from the ntfy action button.

    Security: each request includes a random token generated at server start.
    The server rejects any request that doesn't carry the correct token,
    preventing other devices on the LAN from spoofing approve/reject.
    """

    result: str | None = None        # "approve" | "always" | "reject"
    _lock = threading.Event()
    _expected_token: str = ""        # set per-request in _start_callback_server

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        token = params.get("token", [""])[0]

        # ── Token check ────────────────────────────────────────────────────
        if not secrets.compare_digest(token, _CallbackHandler._expected_token):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"Forbidden")
            return

        action = parsed.path.strip("/").lower()
        if action in ("approve", "always", "reject"):
            _CallbackHandler.result = action
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            _CallbackHandler._lock.set()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # Suppress request logs


def _get_callback_port(config: dict) -> int:
    """Use a fixed port from config so macOS firewall rules stay stable.
    Falls back to a random free port if not configured."""
    fixed = config.get("callback_port")
    if fixed:
        return int(fixed)
    # Dynamic fallback (useful for testing, but firewall may block LAN access)
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]


def _start_callback_server(port: int) -> http.server.HTTPServer:
    # Generate a fresh random token for this request
    _CallbackHandler._expected_token = secrets.token_urlsafe(16)
    # Reset state
    _CallbackHandler.result = None
    _CallbackHandler._lock.clear()
    # Bind to 0.0.0.0 so iPhone/Watch on the same LAN can reach us
    server = http.server.HTTPServer(("0.0.0.0", port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# ntfy.sh notification
# ---------------------------------------------------------------------------

def _send_ntfy(summary: str, port: int, config: dict) -> None:
    ntfy_cfg = config.get("ntfy", {})
    server = ntfy_cfg.get("server", "https://ntfy.sh").rstrip("/")
    topic = ntfy_cfg.get("topic", "")

    if not topic:
        _fatal("ntfy topic not set in config.json.")

    # Get Mac's LAN IP via UDP trick (no data sent)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as _s:
            _s.connect(("8.8.8.8", 80))
            local_ip = _s.getsockname()[0]
    except Exception:
        local_ip = "127.0.0.1"
    base_url = f"http://{local_ip}:{port}"
    token = _CallbackHandler._expected_token  # set by _start_callback_server

    headers = {
        # Headers must be latin-1 safe — no emojis here.
        # ntfy prepends Tags as emoji icons before the title (robot=🤖, key=🔑).
        "Title": "ClaudeCode",
        "Priority": "high",
        "Tags": "robot,key",
        # 'http' action type: ntfy sends a background HTTP request from the app.
        # Works on Apple Watch via companion app (unlike 'view' which opens a browser).
        # Token in URL prevents LAN spoofing — only the notification recipient
        # who received the exact URL can trigger approve/reject.
        "Actions": (
            f"http, Approve, {base_url}/approve?token={token}, method=GET, clear=true; "
            f"http, Always Allow, {base_url}/always?token={token}, method=GET, clear=true; "
            f"http, Reject, {base_url}/reject?token={token}, method=GET, clear=true"
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
# macOS native dialog (Stage 1 — local escalation)
# ---------------------------------------------------------------------------

def _show_macos_dialog(summary: str, base_url: str, token: str, delay: int) -> subprocess.Popen:  # type: ignore[type-arg]
    """Show a native macOS dialog in a background subprocess.

    When the user clicks Approve or Reject the dialog uses `curl` to hit the
    local callback server — exactly the same endpoint ntfy uses for Stage 2.
    The main loop is agnostic to which stage produced the response.

    Args:
        summary:  Short description of the permission request.
        base_url: Loopback URL (http://127.0.0.1:PORT) — same machine, no LAN needed.
        token:    Per-request secret token to include in callback URL.
        delay:    Seconds the dialog stays open before auto-dismissing (giving up after).
    """
    # Escape single quotes in summary so AppleScript string literals are safe.
    safe = summary.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\u2019")

    approve_url = f"{base_url}/approve?token={token}"
    reject_url  = f"{base_url}/reject?token={token}"

    # Write to a temp file to avoid shell-escaping nightmares with -e
    script = f'''
tell application "System Events"
    set theResult to display dialog "{safe}" ¬
        with title "Claude Code" ¬
        buttons {{"Reject", "Approve"}} ¬
        default button "Approve" ¬
        giving up after {delay}
end tell
if gave up of theResult is false then
    set btn to button returned of theResult
    if btn is "Approve" then
        do shell script "curl -sf '" & "{approve_url}" & "' &>/dev/null &"
    else
        do shell script "curl -sf '" & "{reject_url}" & "' &>/dev/null &"
    end if
end if
'''
    tmp = tempfile.NamedTemporaryFile(suffix=".applescript", mode="w", delete=False)
    tmp.write(script)
    tmp.flush()
    return subprocess.Popen(
        ["osascript", tmp.name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

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
        tool    = hook_data.get("tool_name", "Unknown")
        ti      = hook_data.get("tool_input", {})
        cmd     = ti.get("command", ti.get("file_path", ""))
        cmd_str = str(cmd)
        summary = f"{tool}: {cmd_str[:80]}" if cmd else f"{tool} permission requested"

    # 4. Start local callback server
    port   = _get_callback_port(config)
    server = _start_callback_server(port)
    token  = _CallbackHandler._expected_token

    total_timeout    = config.get("timeout_seconds", 60)
    escalation_delay = config.get("escalation_delay_seconds", 10)

    macos_proc: subprocess.Popen | None = None  # type: ignore[type-arg]

    try:
        local_base = f"http://127.0.0.1:{port}"

        # ── Stage 1 (optional): macOS native dialog ──────────────────────────
        # Disabled by default — enable with "macos_dialog": true in config.json.
        # Only runs on macOS. Skipped on Linux / Windows automatically.
        # When enabled: shows a modal Approve/Reject dialog for `escalation_delay`
        # seconds, then escalates to ntfy (phone/Watch) if not answered.
        use_dialog = config.get("macos_dialog", False) and sys.platform == "darwin"

        if use_dialog:
            macos_proc = _show_macos_dialog(summary, local_base, token, escalation_delay)
            tapped = _CallbackHandler._lock.wait(timeout=escalation_delay)
        else:
            tapped = False  # skip straight to ntfy

        if not tapped:
            # ── Stage 2: ntfy → phone / Apple Watch ──────────────────────────
            _send_ntfy(summary, port, config)
            remaining = total_timeout - (escalation_delay if use_dialog else 0)
            tapped = _CallbackHandler._lock.wait(timeout=max(remaining, 5))

        result = _CallbackHandler.result if tapped else None

    finally:
        server.shutdown()
        # Kill the macOS dialog if it’s still open (user responded via Watch)
        if macos_proc is not None:
            try:
                macos_proc.terminate()
            except Exception:
                pass

    # 5. Output decision
    if result == "approve":
        _output_decision("allow")
    elif result == "always":
        _output_decision("allow", always=True)
    else:
        timeout_action = config.get("timeout_action", "deny")
        reason = (
            "Rejected from Apple Watch."
            if result == "reject"
            else "Approval timed out — defaulting to deny."
        )
        if result is None and timeout_action == "allow":
            _output_decision("allow", reason="Timed out — auto-approved per config.")
        else:
            _output_decision("deny", reason=reason)


if __name__ == "__main__":
    main()
