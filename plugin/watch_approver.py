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
import time
import urllib.request
import urllib.parse
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HOOK_DIR = Path(__file__).parent
LEGACY_CONFIG = HOOK_DIR / "config.json"
USER_CONFIG = Path.home() / ".config" / "claude-afk" / "config.json"
FALLBACK_CONFIG = Path.home() / ".claude-afk-config.json"


def load_config() -> dict:
    config_path = None
    
    if USER_CONFIG.exists():
        config_path = USER_CONFIG
    elif FALLBACK_CONFIG.exists():
        config_path = FALLBACK_CONFIG
    elif LEGACY_CONFIG.exists():
        config_path = LEGACY_CONFIG
        
    if not config_path:
        _fatal(
            f"Config not found! Please create {USER_CONFIG}.\n"
            f"See https://github.com/fomyio/claude-afk for instructions."
        )
        return {} # never reached

    try:
        with open(config_path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        _fatal(f"Invalid JSON in {config_path}: {e}")
        return {}


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

    result = None        # "approve" | "always" | "reject"
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
# Auto-approve logic
# ---------------------------------------------------------------------------

def _is_auto_approved(hook_data: dict, config: dict) -> bool:
    """Check if the requested action matches user-defined safe patterns."""
    import fnmatch
    
    # Only auto-approve Bash commands for now, as that's 99% of the noise
    tool_name = hook_data.get("tool_name")
    if tool_name != "Bash":
        return False
        
    cmd = hook_data.get("tool_input", {}).get("command", "")
    if not cmd:
        return False
        
    # Default safe read-only commands if user hasn't configured any
    default_rules = [
        "ls*", "cat*", "pwd", "whoami", "echo*",
        "git status*", "git branch*", "git log*", "git diff*", "git show*"
    ]
    
    rules = config.get("auto_approve", default_rules)
    
    # Check if the command matches any glob pattern
    # Clean up the command (strip trailing newlines)
    cmd = cmd.strip()
    return any(fnmatch.fnmatch(cmd, rule) for rule in rules)


# ---------------------------------------------------------------------------
# ntfy.sh notification
# ---------------------------------------------------------------------------

def _send_ntfy(summary: str, port: int, config: dict):
    ntfy_cfg = config.get("ntfy", {})
    server = ntfy_cfg.get("server", "https://ntfy.sh").rstrip("/")
    topic = ntfy_cfg.get("topic", "")

    if not topic:
        return None

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
    
    # Store message ID if we want to refer to this specific notification later
    # ntfy returns the message ID in the response headers/JSON
    message_id = None

    url = f"{server}/{urllib.parse.quote(topic, safe='')}"
    data = summary.encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 201, 204):
                _fatal(f"ntfy.sh returned HTTP {resp.status}")
            # Try to grab the message ID so we can edit/clear it later if needed
            try:
                resp_data = json.loads(resp.read().decode('utf-8'))
                message_id = resp_data.get('id')
            except Exception:
                pass
    except Exception as e:
        _fatal(f"Failed to send ntfy notification: {e}")
        
    return message_id


def _send_ntfy_resolution(summary: str, result_icon: str, original_id, config: dict) -> None:
    """Send a follow-up notification or update to show the final decision."""
    ntfy_cfg = config.get("ntfy", {})
    server = ntfy_cfg.get("server", "https://ntfy.sh").rstrip("/")
    topic = ntfy_cfg.get("topic", "")

    if not topic:
        return
        
    headers = {
        "Title": "ClaudeCode (Resolved)",
        "Priority": "default", 
        "Tags": result_icon,
        "Content-Type": "text/plain; charset=utf-8",
    }
    
    url = f"{server}/{urllib.parse.quote(topic, safe='')}"
    data = summary.encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    
    try:
        # Fire and forget; don't fail the approval if this fails
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Stage 1 — terminal keypress (default) and macOS dialog (opt-in)
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


# ANSI colours — fall back gracefully on terminals that don't support them
_BOLD   = "\033[1m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"


def _wait_for_terminal_keypress(base_url: str, token: str, delay: int, summary: str, is_configured: bool = True) -> bool:
    """Print a one-line prompt to stderr and wait up to `delay` seconds for a keypress.

    Reads directly from /dev/tty so it works even though Claude Code has
    redirected stdin.  Returns True if the user responded, False on timeout.
    Silently returns False when no TTY is available (CI, Windows, piped input).

    Keys:
        a / y  → approve
        n / r  → reject
        w      → skip to Watch immediately
        Enter  → approve (default)
    """
    import select
    try:
        import tty as _tty
        import termios as _termios
    except ImportError:
        return False  # Windows or no termios — skip silently

    try:
        tty_file = open("/dev/tty", "rb", buffering=0)  # noqa: WPS515
    except OSError:
        return False  # no controlling TTY (e.g. running in background)

    approve_url = f"{base_url}/approve?token={token}"
    reject_url  = f"{base_url}/reject?token={token}"

    # Print prompt to stderr (stdout carries the JSON decision)
    if is_configured:
        prompt = (
            f"\r{_YELLOW}{_BOLD}⚡ claude-afk{_RESET} › {summary}  "
            f"{_GREEN}[A]{_RESET}pprove  "
            f"{_RED}[R]{_RESET}eject  "
            f"{_DIM}[W]atch ({delay}s)…{_RESET}  "
        )
    else:
        prompt = (
            f"\r{_YELLOW}{_BOLD}⚡ claude-afk{_RESET} › {summary}  "
            f"{_GREEN}[A]{_RESET}pprove  "
            f"{_RED}[R]{_RESET}eject  "
            f"{_DIM}(ntfy unconfigured){_RESET}  "
        )
    print(prompt, end="", flush=True, file=sys.stderr)

    fd = tty_file.fileno()
    old_attrs = _termios.tcgetattr(fd)
    responded = False
    try:
        _tty.setraw(fd)
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            ready, _, _ = select.select([tty_file], [], [], min(0.2, remaining))
            if not ready:
                continue
            ch = tty_file.read(1).decode("utf-8", errors="ignore").lower()
            if ch in ("a", "y", "\r", "\n"):      # approve
                urllib.request.urlopen(approve_url, timeout=3)
                responded = True
                break
            elif ch in ("n", "r"):                # reject
                urllib.request.urlopen(reject_url, timeout=3)
                responded = True
                break
            elif ch == "w":                       # skip to Watch now
                break
            elif ch in ("\x03", "\x04"):          # Ctrl-C / Ctrl-D — reject
                urllib.request.urlopen(reject_url, timeout=3)
                responded = True
                break
    except Exception:
        pass
    finally:
        try:
            _termios.tcsetattr(fd, _termios.TCSADRAIN, old_attrs)
        except Exception:
            pass
        tty_file.close()
        # Clear the prompt line
        print(f"\r{' ' * 80}\r", end="", flush=True, file=sys.stderr)

    return responded


def _build_inline_summary(hook_data: dict, project_name: str) -> str:
    """Fast, no-API summary for the terminal prompt (shown before the delay)."""
    tool = hook_data.get("tool_name", "Unknown")
    ti   = hook_data.get("tool_input", {})
    cmd  = ti.get("command", ti.get("file_path", ""))
    cmd_str = str(cmd).strip() if cmd else ""
    if cmd_str and len(cmd_str) > 80:
        cmd_str = cmd_str[:77] + "…"
    return f"[{project_name}] {tool}: {cmd_str}" if cmd_str else f"[{project_name}] {tool} permission requested"


def main() -> None:
    # 1. Read hook JSON from stdin
    try:
        hook_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        _fatal(f"Invalid JSON from Claude Code: {e}")
        return

    # 2. Load config
    config = load_config()

    # 3. Check for auto-approve (skips everything else if matched)
    if _is_auto_approved(hook_data, config):
        _output_decision("allow", reason="Auto-approved by rules in config.json")
        return

    # 4. Extract project context (from cwd) — cheap, no API call
    cwd          = hook_data.get("cwd", "")
    project_name = os.path.basename(cwd) if cwd else "Unknown"

    # 5. Build a FAST inline summary for the terminal prompt (no LLM call yet).
    #    The LLM summarizer runs only if the user doesn't respond within the
    #    escalation window — i.e. only when we actually need to send to the Watch.
    terminal_summary = _build_inline_summary(hook_data, project_name)

    # 6. Start local callback server
    port   = _get_callback_port(config)
    server = _start_callback_server(port)
    token  = _CallbackHandler._expected_token

    ntfy_cfg = config.get("ntfy", {})
    topic = ntfy_cfg.get("topic", "")

    total_timeout    = config.get("timeout_seconds", 60)
    
    # If unconfigured, block in the terminal prompt for the entire timeout duration 
    # instead of escalating to missing watch notifications.
    if not topic:
        escalation_delay = total_timeout
    else:
        escalation_delay = config.get("escalation_delay_seconds", 10)

    macos_proc = None
    ntfy_msg_id = None

    try:
        local_base = f"http://127.0.0.1:{port}"

        # ── Stage 1a: Terminal keypress (default, non-modal) ──────────────────
        # Shows the prompt with the fast inline summary (no API latency).
        # The full LLM summary is only computed *after* if we need to escalate.
        tapped = _wait_for_terminal_keypress(local_base, token, escalation_delay, terminal_summary, is_configured=bool(topic))

        # ── Stage 1b (optional): macOS dialog overlay ──────────────────────────
        # Enable with "macos_dialog": true.
        # If terminal TTY was skipped, the macos dialog handles the delay.
        macos_proc = None
        if not tapped and config.get("macos_dialog", False) and sys.platform == "darwin":
            macos_proc = _show_macos_dialog(terminal_summary, local_base, token, escalation_delay)
            tapped = _CallbackHandler._lock.wait(timeout=escalation_delay)

        if not tapped:
            # ── Stage 1c: Plain sleep fallback ───────────────────────────────
            # If neither the TTY prompt nor the macOS dialog ran (e.g. Claude
            # Code's hook has no controlling terminal), we still honour the
            # escalation delay so the phone/Watch notification isn't instant.
            # We poll the callback lock so a response from any other stage
            # (e.g. a browser extension in future) can cut the wait short.
            tapped = _CallbackHandler._lock.wait(timeout=escalation_delay)

        if not tapped:
            # ── Stage 2: ntfy → phone / Apple Watch ──────────────────────────
            # Only NOW run the (potentially slow) LLM summarizer — only if the
            # user didn't respond during the escalation delay. This means we
            # never spend an API call on commands you approve at the terminal.
            try:
                from summarizer import summarize  # type: ignore
                watch_summary = summarize(hook_data, config, project_name)
            except Exception:
                watch_summary = terminal_summary  # fall back to inline summary

            ntfy_msg_id = _send_ntfy(watch_summary, port, config)
            remaining = total_timeout - escalation_delay
            tapped = _CallbackHandler._lock.wait(timeout=max(remaining, 5))
        else:
            # Responded before escalation — no LLM needed, no Watch buzz
            watch_summary = terminal_summary

        result = _CallbackHandler.result if tapped else None

    finally:
        server.shutdown()
        # Kill the macOS dialog if it’s still open (user responded via Watch)
        if macos_proc is not None:
            try:
                macos_proc.terminate()
            except Exception:
                pass

    # 7. Output decision and send resolution notification to phone (if ntfy was sent)
    if result == "approve":
        if ntfy_msg_id:
            _send_ntfy_resolution(watch_summary, "white_check_mark", ntfy_msg_id, config)
        _output_decision("allow")
    elif result == "always":
        if ntfy_msg_id:
            _send_ntfy_resolution(watch_summary, "white_check_mark", ntfy_msg_id, config)
        _output_decision("allow", always=True)
    else:
        timeout_action = config.get("timeout_action", "deny")
        reason = (
            "Rejected from Apple Watch."
            if result == "reject"
            else "Approval timed out — defaulting to deny."
        )
        if result is None and timeout_action == "allow":
            if ntfy_msg_id:
                _send_ntfy_resolution(watch_summary, "white_check_mark", ntfy_msg_id, config)
            _output_decision("allow", reason="Timed out — auto-approved per config.")
        else:
            if ntfy_msg_id:
                icon = "no_entry_sign" if result == "reject" else "hourglass"
                _send_ntfy_resolution(watch_summary, icon, ntfy_msg_id, config)
            _output_decision("deny", reason=reason)


if __name__ == "__main__":
    main()
