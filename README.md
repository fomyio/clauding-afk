# 🤖⌚ claude-afk

<img width="3168" height="1344" alt="image" src="https://github.com/user-attachments/assets/0cec1e04-30c5-457c-94f3-bbfd29c7a9eb" />
---

**Claude codes. You approve from the couch, the coffee shop, or mid-cat-cuddle.**

Kick off a task in your terminal and walk away. When Claude needs a decision, your Apple Watch, iPhone, or Android buzzes — tap **Approve** and it keeps going. One-command install. No cloud server. No custom app.


---

## Why this exists

![claude-afk-demo-ezgif com-video-to-gif-converter](https://github.com/user-attachments/assets/912a5655-b186-4db8-b9d5-6e7b5585cfef)

Claude Code is powerful — and careful. Before it runs anything potentially destructive, it asks for your permission. That's great. But if you're away from your desk, getting a coffee, or just don't want to tab back to the terminal, that prompt blocks your entire workflow until you respond.

This hook intercepts that prompt and sends it straight to your wrist.

---

## How it works

```
Claude wants to run a command
         │
         ▼
watch_approver.py (Claude Code hook)
  1. Summarizes the request into plain English using Claude Haiku (no config needed)
  2. Sends a notification to your iPhone/Watch via ntfy.sh
  3. Waits for your tap
         │
    ┌────┴────────────┐
    │  Approve once   │  → Claude continues
    │  Always Allow   │  → Claude continues + adds to allow list
    │  Reject         │  → Claude tries another approach
    └─────────────────┘
```

Permission requests are summarized into a single sentence so you know exactly what's happening on a 1.7-inch screen:

> *"Delete all files in node_modules directory"*

instead of a raw command string.

---

## Demo

| On iPhone | On Apple Watch |
|-----------|----------------|
| Full notification with Approve / Always Allow / Reject buttons | Alert buzz — grab phone to respond |

> **Note:** Apple Watch shows the notification as a heads-up buzz. Action buttons are on the iPhone. The Watch is your alert; the phone is your control.

---

## Requirements

- macOS, Linux, or Windows *(running Claude Code CLI)*
- Python 3.8+
- iPhone with the free **[ntfy](https://apps.apple.com/app/ntfy/id1625396336)** app
- Apple Watch *(for the wrist buzz — iPhone works great on its own too)*

---

## Install

```bash
git clone https://github.com/your-org/ClaudeCodeAppleWatch
cd ClaudeCodeAppleWatch
bash install.sh
```

The installer handles everything:

1. ✅ Checks Python 3
2. 📦 Installs `requests` and `litellm`
3. 🔑 Generates a private 5-word ntfy topic (or you enter your own)
4. 📁 Copies hook scripts to `~/.claude/hooks/`
5. 🔒 Opens port 45678 in the macOS firewall *(so your iPhone can reach it)*
6. ⚙️ Registers the `PermissionRequest` hook in `~/.claude/settings.json`

Then subscribe in the ntfy app:
```
ntfy app → + → Server: https://ntfy.sh → Topic: <shown at install>
```

Run `claude` as normal. Your first permission prompt will arrive on your wrist.

---

## Configuration

`~/.claude/hooks/config.json` — edit after install:

```json
{
  "ntfy": {
    "topic": "your-private-topic",
    "server": "https://ntfy.sh"
  },
  "summarizer": {
    "enabled": true,
    "model": "claude-haiku-3-5"
  },
  "callback_port": 45678,
  "escalation_delay_seconds": 10,
  "macos_dialog": false,
  "timeout_seconds": 60,
  "timeout_action": "deny"
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `ntfy.topic` | *(generated)* | Your private ntfy topic — keep it secret |
| `summarizer.enabled` | `true` | AI summaries using Claude Haiku — no API setup needed |
| `summarizer.model` | `claude-haiku-3-5` | Override to use a different model (optional) |
| `callback_port` | `45678` | Fixed port the hook listens on |
| `escalation_delay_seconds` | `10` | Seconds before escalating *(if `macos_dialog` is on)* |
| `macos_dialog` | `false` | Show a native macOS dialog before sending to Watch |
| `timeout_seconds` | `60` | How long to wait before auto-deny |
| `timeout_action` | `"deny"` | `"deny"` or `"allow"` on timeout |

> **API key**: Claude Code passes `ANTHROPIC_API_KEY` to all hooks automatically — no setup needed. The summarizer works out of the box.

### Use a different model

By default Claude Haiku is used — powered by your existing Claude Code API key, zero config.
To switch providers (OpenAI, Gemini, Mistral, Ollama, etc.):

```json
"model": "gpt-4o-mini",
"api_key_env": "OPENAI_API_KEY"
```

### Self-host ntfy

Point `"server"` at your own ntfy instance for full data sovereignty.

### Optional: macOS dialog before Watch

Enable a native Mac dialog that appears for 10 seconds before escalating to your Watch — handy if you're at your desk:

```json
"macos_dialog": true
```

---

## Security

### Per-request token
Every notification includes a random **16-byte secret token** embedded in the action button URLs:
```
http://192.168.x.x:45678/approve?token=X7kP2mNqRs4vW9tL
```
The local server validates it using `secrets.compare_digest()` (timing-safe). Any request without the correct token gets **403 Forbidden** — even from devices on your LAN.

### Private ntfy topic
Your topic name is the first line of defense — only devices subscribed to your topic receive the notification. The auto-generated topic is a random 5-word phrase. Keep it private.

### Port scope
Port `45678` is only reachable on your local network. It's not exposed to the internet unless you've set up explicit port forwarding. The server only runs for the duration of each permission request (~60s max).

---

## Compatibility

| Surface | Hooks supported? |
|---------|-----------------|
| `claude` in terminal | ✅ Full support |
| Claude Code (any terminal) | ✅ Full support |
| VS Code / Desktop app | ❌ These use built-in native UIs |

> This project uses Claude Code's `PermissionRequest` hook — a feature **unique to Claude Code** among major AI CLI agents. Gemini CLI, Codex CLI, and Aider don't have an equivalent hook system as of early 2025.

---

## Testing

```bash
python3 test_hook.py
```

Validates the summarizer, hook JSON output, and simulates a full permission request locally — no Watch needed.

---

## Project structure

```
ClaudeCodeAppleWatch/
├── watch_approver.py    ← Hook entry point: server, ntfy, decision logic
├── summarizer.py        ← LiteLLM summarization with local fallback
├── config.example.json  ← Config template
├── install.sh           ← One-command setup
├── test_hook.py         ← Local test suite
└── README.md
```

---

## License

MIT — use it, fork it, build on it.
