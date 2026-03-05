"""
summarizer.py
Summarizes a Claude Code PermissionRequest into a short, Watch-friendly sentence.
Uses LiteLLM so the user can pick any LLM provider.

API key: Claude Code passes ANTHROPIC_API_KEY to all hooks automatically.
No manual key setup needed — it just works out of the box.
If you use a different provider, set the relevant env var and update config.json.

Falls back to a plain formatted string if LiteLLM is unavailable or disabled.
"""

import json
import os


def _format_fallback(tool_name: str, tool_input: dict, cwd: str, project_name: str) -> str:
    """Produce a readable summary without any LLM."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "").strip()
        # Truncate long commands
        if len(cmd) > 80:
            cmd = cmd[:77] + "…"
        return f"[{project_name}] Run: {cmd}"

    if tool_name in ("Write", "Edit", "MultiEdit"):
        path = tool_input.get("file_path", tool_input.get("path", "a file"))
        # Show path relative to cwd when possible
        try:
            rel = os.path.relpath(path, cwd)
            path = rel if not rel.startswith("..") else path
        except ValueError:
            pass
        return f"[{project_name}] {tool_name} → {path}"

    if tool_name == "Read":
        path = tool_input.get("file_path", tool_input.get("path", "a file"))
        return f"[{project_name}] Read: {path}"

    if tool_name == "WebSearch":
        query = tool_input.get("query", "")
        return f"[{project_name}] Web search: {query[:60]}"

    # Generic fallback
    first_val = next(iter(tool_input.values()), "") if tool_input else ""
    if isinstance(first_val, str) and first_val:
        return f"[{project_name}] {tool_name}: {first_val[:80]}"
    return f"[{project_name}] {tool_name} permission requested"


def summarize(hook_data: dict, config: dict, project_name: str = "Unknown") -> str:
    """
    Return a single short sentence describing what Claude wants to do.

    Args:
        hook_data: The full PermissionRequest JSON from Claude Code.
        config: The user's config dict (from config.json).

    Returns:
        A short string (≤ ~80 chars) suitable for a Watch notification.
    """
    tool_name = hook_data.get("tool_name", "Unknown")
    tool_input = hook_data.get("tool_input", {})
    cwd = hook_data.get("cwd", "")

    summarizer_cfg = config.get("summarizer", {})
    enabled = summarizer_cfg.get("enabled", True)
    model = summarizer_cfg.get("model", "claude-haiku-3-5")

    if not enabled:
        return _format_fallback(tool_name, tool_input, cwd, project_name)

    # Claude Code passes ANTHROPIC_API_KEY to all hooks automatically.
    # For other providers, set the relevant env var and update 'model' in config.
    # Key resolution order: ANTHROPIC_API_KEY → any key from api_key_env in config.
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get(
        summarizer_cfg.get("api_key_env", ""), ""
    )
    if not api_key:
        # No key available — use local fallback (free, no API call)
        return _format_fallback(tool_name, tool_input, cwd, project_name)

    try:
        from litellm import completion  # type: ignore

        # Keep the input small — only send relevant fields
        relevant = {
            "tool": tool_name,
            "input": tool_input,
            "cwd": cwd,
        }
        prompt = (
            "You are summarizing an AI coding assistant's action request for an Apple Watch notification. "
            "Reply with ONE sentence, max 12 words, starting with a verb. Be specific. No punctuation at end.\n"
            f"ALWAYS start your response with exactly: [{project_name}] \n\n"
            f"Action: {json.dumps(relevant, ensure_ascii=False)}"
        )

        response = completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=60,
            temperature=0,
            api_key=api_key,
        )
        summary = response.choices[0].message.content.strip()
        # Safety: cap length
        if len(summary) > 100:
            summary = summary[:97] + "…"
        return summary

    except Exception:
        # Any LiteLLM error → silent fallback
        return _format_fallback(tool_name, tool_input, cwd, project_name)
