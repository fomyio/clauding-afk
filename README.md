# 🤖⌚ Claude Code Apple Watch Approver

Approve or reject Claude Code permission requests directly from your **Apple Watch** (or iPhone). No cloud server. No custom iOS app. Just tap.

![Notification preview: "🤖 Claude wants permission — Run: rm -rf node_modules" with Approve, Always, Reject buttons]

## How it works

1. Claude Code asks for permission to run a command
2. Your wrist buzzes with a notification summarizing what Claude wants to do
3. Tap **✅ Approve**, **🔁 Always**, or **❌ Reject** — Claude responds instantly

Permission requests are summarized by an LLM (Claude Haiku by default) into a single plain-English sentence, so you instantly know what's happening even on a small Watch screen.

## Requirements

- macOS (where Claude Code runs)
- Python 3.8+
- iPhone with the free [ntfy](https://apps.apple.com/app/ntfy/id1625396336) app
- Apple Watch (paired to the same iPhone)
- An API key for summarization (optional — Anthropic, OpenAI, etc.)

## Installation

```bash
git clone https://github.com/your-org/ClaudeCodeAppleWatch
cd ClaudeCodeAppleWatch
bash install.sh
```

The installer will:
- Install Python dependencies (`requests`, `litellm`)
- Generate a private random ntfy topic for you
- Copy scripts to `~/.claude/hooks/`
- Register the `PermissionRequest` hook in `~/.claude/settings.json`

Then:
1. Open the **ntfy** app on your iPhone
2. Tap **+** → Server: `https://ntfy.sh` → Topic: *(shown at end of install)*
3. Enable notification permissions when prompted

That's it — run `claude` as normal.

## Watch actions

| Button | What it does |
|--------|-------------|
| ✅ Approve | Allow this command once |
| 🔁 Always | Allow and never ask again for this tool type |
| ❌ Reject | Block this command; Claude will try another approach |

If you don't respond within 60 seconds, the request is **denied** by default (safe). Configurable in `config.json`.

## Configuration

After install, edit `~/.claude/hooks/config.json`:

```json
{
  "ntfy": {
    "topic": "your-private-topic",
    "server": "https://ntfy.sh"
  },
  "summarizer": {
    "enabled": true,
    "model": "claude-haiku-3-5",
    "api_key_env": "ANTHROPIC_API_KEY"
  },
  "timeout_seconds": 60,
  "timeout_action": "deny"
}
```

### Disabling the summarizer

Set `"enabled": false` — a local fallback formatter will be used instead (no API call, free).

### Using a different LLM

Change `model` to any [LiteLLM-supported model](https://docs.litellm.ai/docs/providers), e.g.:

```json
"model": "gpt-4o-mini",
"api_key_env": "OPENAI_API_KEY"
```

### Self-hosting ntfy

Change `"server"` to your own ntfy instance URL.

## Testing (no Watch required)

```bash
python3 test_hook.py
```

Runs summarizer unit tests and validates the hook output schema using a local simulated request.

## Security

- **Keep your topic secret** — it's your auth token. Anyone with it can send you fake approval requests.
- The topic is auto-generated as a 32-character random string on install.
- `config.json` is git-ignored.
- The hook runs a temporary local HTTP server (random port, loopback only) per request — no port stays open.

## Project structure

```
ClaudeCodeAppleWatch/
├── watch_approver.py       ← Claude Code hook entry point
├── summarizer.py           ← LiteLLM summarization (with fallback)
├── config.example.json     ← Config template
├── install.sh              ← One-command setup
├── test_hook.py            ← Local test suite
└── README.md
```

## License

MIT
