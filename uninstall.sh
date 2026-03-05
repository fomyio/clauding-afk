#!/usr/bin/env bash
# uninstall.sh — Cleans up the legacy manual installation of claude-afk

set -e

HOOKS_DIR="$HOME/.claude/hooks"
CLAUDE_SETTINGS="$HOME/.claude/settings.json"

echo "🤖 claude-afk — Legacy Uninstaller"
echo "=================================="

echo "1. Removing hook from $CLAUDE_SETTINGS..."
if [ -f "$CLAUDE_SETTINGS" ]; then
  python3 - <<PYEOF
import json, pathlib
settings_path = pathlib.Path("$CLAUDE_SETTINGS")
try:
    settings = json.loads(settings_path.read_text())
    hooks = settings.get("hooks", {})
    if "PermissionRequest" in hooks:
        # Keep hooks that aren't watch_approver.py
        filtered_pr = []
        for h in hooks["PermissionRequest"]:
            if not any("watch_approver.py" in cmd.get("command", "") for cmd in h.get("hooks", [])):
                filtered_pr.append(h)
        
        if len(filtered_pr) == 0:
            del hooks["PermissionRequest"]
        else:
            hooks["PermissionRequest"] = filtered_pr
            
        settings["hooks"] = hooks
        settings_path.write_text(json.dumps(settings, indent=2))
        print("✅  Cleaned settings.json")
    else:
        print("⏭️  No PermissionRequest hooks found in settings.")
except Exception as e:
    print(f"⚠️  Could not clean settings.json: {e}")
PYEOF
fi

echo "2. Removing legacy scripts from $HOOKS_DIR..."
rm -f "$HOOKS_DIR/watch_approver.py"
rm -f "$HOOKS_DIR/summarizer.py"
echo "✅  Scripts removed."

if [ -f "$HOOKS_DIR/config.json" ]; then
    echo "⚠️  Found legacy config at $HOOKS_DIR/config.json."
    read -p "Do you want to move it to the new plugin config location (~/.config/claude-afk/config.json)? [Y/n] " MOVE_CONFIG
    MOVE_CONFIG=${MOVE_CONFIG:-Y}
    if [[ "$MOVE_CONFIG" =~ ^[Yy]$ ]]; then
        mkdir -p ~/.config/claude-afk
        mv "$HOOKS_DIR/config.json" ~/.config/claude-afk/config.json
        echo "✅  Config moved to ~/.config/claude-afk/config.json"
    else
        echo "⏭️  Leaving config in $HOOKS_DIR."
    fi
fi

echo ""
echo "Uninstall complete! You can now safely install the plugin version:"
echo "claude /plugin install https://github.com/fomyio/claude-afk"
