"""
summarizer.py
Summarizes a Claude Code PermissionRequest into a short, Watch-friendly sentence.
Uses LiteLLM so the user can pick any LLM provider.
Falls back to a plain formatted string if LiteLLM is unavailable / disabled.
"""

import json
import os


def _format_fallback(tool_name: str, tool_input: dict, cwd: str) -> str:
    """Produce a readable summary without any LLM."""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "").strip()
        # Truncate long commands
        if len(cmd) > 80:
            cmd = cmd[:77] + "…"
        return f"Run: {cmd}"

    if tool_name in ("Write", "Edit", "MultiEdit"):
        path = tool_input.get("file_path", tool_input.get("path", "a file"))
        # Show path relative to cwd when possible
        try:
            rel = os.path.relpath(path, cwd)
            path = rel if not rel.startswith("..") else path
        except ValueError:
            pass
        return f"{tool_name} → {path}"

    if tool_name == "Read":
        path = tool_input.get("file_path", tool_input.get("path", "a file"))
        return f"Read: {path}"

    if tool_name == "WebSearch":
        query = tool_input.get("query", "")
        return f"Web search: {query[:60]}"

    # Generic fallback
    first_val = next(iter(tool_input.values()), "") if tool_input else ""
    if isinstance(first_val, str) and first_val:
        return f"{tool_name}: {first_val[:80]}"
    return f"{tool_name} permission requested"


def summarize(hook_data: dict, config: dict) -> str:
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
    api_key_env = summarizer_cfg.get("api_key_env", "ANTHROPIC_API_KEY")

    if not enabled:
        return _format_fallback(tool_name, tool_input, cwd)

    # Check API key is set
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        return _format_fallback(tool_name, tool_input, cwd)

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
            "Reply with ONE sentence, max 12 words, starting with a verb. Be specific. No punctuation at end.\n\n"
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
        return _format_fallback(tool_name, tool_input, cwd)
