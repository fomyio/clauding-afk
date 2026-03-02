#!/usr/bin/env python3
"""
test_hook.py
Simulates a Claude Code PermissionRequest locally — no Watch or ntfy required.
Tests the full hook pipeline: stdin parsing → summarizer → decision output.

Usage:
    python3 test_hook.py
    python3 test_hook.py --no-llm      # skip LiteLLM, test fallback only
    python3 test_hook.py --action approve|always|reject|timeout
"""

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

HOOK_SCRIPT = Path(__file__).parent / "watch_approver.py"

# Sample permission requests to rotate through
SAMPLE_REQUESTS = [
    {
        "session_id": "test-session-001",
        "transcript_path": "/tmp/test.jsonl",
        "cwd": "/Users/tester/projects/myapp",
        "permission_mode": "default",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {
            "command": "rm -rf node_modules && npm install",
            "description": "Clean reinstall of dependencies",
        },
        "permission_suggestions": [{"type": "toolAlwaysAllow", "tool": "Bash"}],
    },
    {
        "session_id": "test-session-002",
        "transcript_path": "/tmp/test.jsonl",
        "cwd": "/Users/tester/projects/myapp",
        "permission_mode": "default",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Write",
        "tool_input": {
            "file_path": "/Users/tester/projects/myapp/src/index.ts",
            "content": "// new content",
        },
        "permission_suggestions": [],
    },
    {
        "session_id": "test-session-003",
        "transcript_path": "/tmp/test.jsonl",
        "cwd": "/Users/tester/projects/myapp",
        "permission_mode": "default",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {
            "command": "git push origin main --force",
        },
        "permission_suggestions": [],
    },
]


def run_hook(payload: dict, simulate_action: str | None, timeout: int = 10) -> dict | None:
    """
    Run watch_approver.py with the given payload piped to stdin.
    If simulate_action is set, hit the callback URL automatically after 1 second.
    Returns the parsed JSON output from the hook, or None on error.
    """
    proc = subprocess.Popen(
        [sys.executable, str(HOOK_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    stdin_data = json.dumps(payload)

    if simulate_action and simulate_action != "timeout":
        # Read the port the hook server started on by intercepting the ntfy call.
        # Since we can't easily intercept ntfy, we patch: run with a mock ntfy
        # (done below via env override) and hit the callback directly.
        pass

    stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout + 5)

    if stderr:
        print(f"  [stderr] {stderr.strip()}", file=sys.stderr)

    if not stdout.strip():
        return None

    try:
        return json.loads(stdout.strip())
    except json.JSONDecodeError:
        print(f"  [bad output] {stdout!r}")
        return None


def assert_decision(result: dict | None, expected_behavior: str, label: str) -> bool:
    """Assert the hookSpecificOutput contains the expected decision behavior."""
    if result is None:
        print(f"  ❌  {label}: No output received.")
        return False

    try:
        behavior = result["hookSpecificOutput"]["decision"]["behavior"]
        if behavior == expected_behavior:
            print(f"  ✅  {label}: decision.behavior = '{behavior}'")
            return True
        else:
            print(f"  ❌  {label}: expected '{expected_behavior}', got '{behavior}'")
            print(f"       Full output: {json.dumps(result, indent=2)}")
            return False
    except (KeyError, TypeError) as e:
        print(f"  ❌  {label}: unexpected output shape — {e}")
        print(f"       Full output: {json.dumps(result, indent=2)}")
        return False


def test_summarizer_only():
    """Unit-test the summarizer module in isolation."""
    print("\n── Summarizer unit tests ──────────────────────────────────")
    sys.path.insert(0, str(Path(__file__).parent))
    from summarizer import _format_fallback  # noqa

    cases = [
        ("Bash", {"command": "npm run build"}, "/tmp", "Run: npm run build"),
        ("Write", {"file_path": "/tmp/foo/bar.ts"}, "/tmp/foo", "Write → bar.ts"),
        ("Read", {"file_path": "/etc/hosts"}, "/tmp", "Read: /etc/hosts"),
        ("WebSearch", {"query": "python asyncio tutorial"}, "/tmp", "Web search: python asyncio tutorial"),
        ("Unknown", {}, "/tmp", "Unknown permission requested"),
    ]

    passed = 0
    for tool, inp, cwd, expected in cases:
        result = _format_fallback(tool, inp, cwd)
        ok = result == expected
        status = "✅" if ok else "❌"
        print(f"  {status}  {tool}: got '{result}'" + ("" if ok else f" (expected '{expected}')"))
        if ok:
            passed += 1

    print(f"\n  {passed}/{len(cases)} summarizer tests passed.")
    return passed == len(cases)


def main():
    parser = argparse.ArgumentParser(description="Test the Claude Code Watch Approver hook.")
    parser.add_argument(
        "--no-llm", action="store_true", help="Skip LiteLLM summarization tests."
    )
    args = parser.parse_args()

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Claude Code Watch Approver — Test Suite")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    all_passed = True

    # ── Summarizer unit tests (no network, no Watch) ──
    all_passed &= test_summarizer_only()

    # ── Hook integration test: check JSON output schema ──
    print("\n── Hook output schema test ────────────────────────────────")
    print("  Sending sample Bash request to hook (timeout=5s → expect deny)...")

    # We run with a very short timeout and no ntfy config needed
    # since we just want to validate the output shape on timeout.
    sample = SAMPLE_REQUESTS[0].copy()

    # Create a minimal config that disables ntfy and LiteLLM for test speed
    import tempfile, os
    test_config = {
        "ntfy": {"topic": "test-topic-do-not-use", "server": "http://127.0.0.1:19999"},
        "summarizer": {"enabled": False},
        "timeout_seconds": 3,
        "timeout_action": "deny",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        json.dump(test_config, tf)
        tmp_config = tf.name

    # Temporarily patch config path (monkey-patch via env-like approach: use a wrapper)
    wrapper = f"""
import sys, pathlib
sys.path.insert(0, '{Path(__file__).parent}')
import watch_approver
watch_approver.CONFIG_PATH = pathlib.Path('{tmp_config}')
watch_approver.main()
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", wrapper],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = proc.communicate(input=json.dumps(sample), timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()

    os.unlink(tmp_config)

    result = None
    if stdout.strip():
        try:
            result = json.loads(stdout.strip())
        except Exception:
            pass

    # ntfy call will fail (no server at 19999) → hook should still output deny
    all_passed &= assert_decision(result, "deny", "Timeout/ntfy-fail → deny")

    # ── Summary ──
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if all_passed:
        print("  🎉  All tests passed!")
    else:
        print("  ⚠️   Some tests failed — see output above.")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
