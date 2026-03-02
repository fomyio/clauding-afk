#!/usr/bin/env bash
# install.sh — One-command setup for Claude Code Apple Watch Approver
# Usage: bash install.sh

set -e

HOOKS_DIR="$HOME/.claude/hooks"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "🤖 Claude Code → Apple Watch Approver — Installer"
echo "=================================================="
echo ""

# ── 1. Check Python 3 ──────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "❌  Python 3 is required but not found. Install it and try again."
  exit 1
fi
PYTHON=$(command -v python3)
echo "✅  Python 3 found: $PYTHON"

# ── 2. Install Python dependencies ─────────────────────────────────────────
echo ""
echo "📦  Installing Python dependencies (requests, litellm)..."
# Capture output; only surface real failures (not resolver warnings from unrelated packages)
PIP_OUTPUT=$("$PYTHON" -m pip install --quiet requests litellm 2>&1) || {
  echo "❌  pip install failed:"
  echo "$PIP_OUTPUT"
  exit 1
}
echo "✅  Dependencies installed."

# ── 3. Create hooks directory ──────────────────────────────────────────────
mkdir -p "$HOOKS_DIR"

# ── 4. Set up ntfy topic ──────────────────────────────────────────────────
CONFIG_DEST="$HOOKS_DIR/config.json"

if [ -f "$CONFIG_DEST" ]; then
  echo ""
  echo "ℹ️   Existing config found at $CONFIG_DEST — skipping topic setup."
  TOPIC=$(python3 -c "import json; d=json.load(open('$CONFIG_DEST')); print(d['ntfy']['topic'])")
else
  # Generate a memorable 5-word topic from a curated common-words list
  AUTO_TOPIC=$(python3 - <<'PYEOF'
import secrets
# 200 common, clearly pronounceable words — easy to read on a Watch screen
words = [
    'apple','arrow','atlas','beach','birch','blade','blaze','bloom','bolt','brave',
    'brook','brush','cabin','cargo','cedar','chalk','chase','chess','chill','civic',
    'clam','claw','clean','clear','cliff','clock','cloud','clover','coast','comet',
    'coral','crane','creek','crescent','crisp','cross','crown','crystal','curve','cycle',
    'dawn','delta','depot','depth','dew','dice','dingo','disco','diver','dock',
    'dome','door','draft','drape','drift','drum','dune','dust','dusk','eagle',
    'echo','edge','ember','epoch','fable','falcon','fawn','ferry','field','flare',
    'flash','fleet','flint','float','flood','floor','flute','foam','focus','fold',
    'forest','forge','fork','fossil','frame','frost','gale','gate','gem','glade',
    'glacier','glow','globe','gold','grape','gravel','grove','gust','harbor','haze',
    'hill','honey','horizon','horn','hound','husk','hive','ice','iris','island',
    'jade','jolt','kite','knoll','lake','lantern','lark','laser','lava','leaf',
    'ledge','lemon','lens','lever','light','lime','linen','lion','log','loom',
    'lunar','maple','marble','marsh','mast','meadow','mesa','mint','mist','moon',
    'moose','moss','moth','mountain','nova','oak','oar','ocean','olive','orbit',
    'otter','owl','palm','patch','peak','pebble','pine','pixel','plank','plant',
    'plum','polar','pond','poplar','porch','port','prism','pulse','quartz','quill',
    'raven','reef','ridge','river','rock','rose','rowan','rust','sage','salt',
    'sand','shell','shore','silver','sky','slate','smoke','snow','solar','spark',
    'sphere','spike','splash','sprint','spruce','star','steel','stem','stone','storm',
    'stream','stripe','summit','surf','swift','sword','thorn','tide','tiger','timber',
    'torch','tower','trail','trout','trunk','tulip','tundra','turbo','vale','vapor',
    'vault','vine','violet','vision','volt','wave','wheat','willow','wind','wolf',
    'wood','wool','wren','yard','zinc','zone'
]
print('-'.join(secrets.choice(words) for _ in range(5)))
PYEOF
)

  echo ""
  echo "🔑  Auto-generated topic: $AUTO_TOPIC"
  echo ""
  read -p "   Press Enter to use it, or type your own topic name: " USER_TOPIC
  if [ -n "$USER_TOPIC" ]; then
    TOPIC="$USER_TOPIC"
    echo "   Using custom topic: $TOPIC"
  else
    TOPIC="$AUTO_TOPIC"
    echo "   Using auto-generated topic."
  fi

  # Write config.json from the template
  python3 - <<PYEOF
import json, pathlib

template_path = pathlib.Path("$SCRIPT_DIR/config.example.json")
template = json.loads(template_path.read_text())
template.pop("_comment", None)
template["ntfy"]["topic"] = "$TOPIC"

dest = pathlib.Path("$CONFIG_DEST")
dest.write_text(json.dumps(template, indent=2))
print(f"✅  Config written to {dest}")
PYEOF
fi

# ── 5. Copy scripts to hooks directory ─────────────────────────────────────
echo ""
echo "📁  Copying scripts to $HOOKS_DIR..."
cp "$SCRIPT_DIR/watch_approver.py" "$HOOKS_DIR/watch_approver.py"
cp "$SCRIPT_DIR/summarizer.py"     "$HOOKS_DIR/summarizer.py"
chmod +x "$HOOKS_DIR/watch_approver.py"
echo "✅  Scripts copied."

# ── 6. Register the hook in ~/.claude/settings.json ───────────────────────
echo ""
echo "⚙️   Registering PermissionRequest hook in $CLAUDE_SETTINGS..."

python3 - <<PYEOF
import json, pathlib, sys

settings_path = pathlib.Path("$CLAUDE_SETTINGS")

if settings_path.exists():
    try:
        settings = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        print("⚠️   Could not parse existing settings.json — creating a fresh one.")
        settings = {}
else:
    settings = {}

hook_command = "python3 ~/.claude/hooks/watch_approver.py"
hook_entry = {"type": "command", "command": hook_command}

hooks = settings.setdefault("hooks", {})
existing = hooks.get("PermissionRequest", [])

# Avoid duplicates
already = any(h.get("command") == hook_command for h in existing if isinstance(h, dict))
if already:
    print("ℹ️   Hook already registered — no changes to settings.json.")
else:
    existing.append(hook_entry)
    hooks["PermissionRequest"] = existing
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2))
    print("✅  Hook registered.")
PYEOF

# ── 7. Done — print next steps ─────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎉  Installation complete!"
echo ""
echo "   Next steps:"
echo ""
echo "   1. Install the ntfy app on your iPhone (free):"
echo "      App Store → search 'ntfy'"
echo ""
echo "   2. Subscribe to your private topic:"
echo "      Open ntfy → '+' → Server: https://ntfy.sh"
echo "      Topic: $TOPIC"
echo ""
echo "   3. (Optional) Set your LLM API key for smart summaries:"
echo "      export ANTHROPIC_API_KEY=sk-ant-..."
echo "      (add to ~/.zshrc or ~/.bashrc to persist)"
echo ""
echo "   4. Run 'claude' as normal and watch for a tap on your wrist!"
echo ""
echo "   ⚠️  Keep your topic name private — it's your auth token."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
