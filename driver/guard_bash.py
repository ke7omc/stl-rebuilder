#!/usr/bin/env python3
"""PreToolUse hook: block Bash commands the unattended agent must never run.

Reads the hook payload on stdin, exits 2 (block, with the reason fed back to the agent) on a
match, 0 otherwise. Defense in depth next to the settings.json deny list — patterns here also
catch commands wrapped in `bash -c`, subshells, or `&&` chains.
"""
import json
import re
import sys

BLOCK = [
    (r"\bgit\s+push\b", "git push is not allowed in the loop"),
    (r"\bgit\s+(tag|remote)\b", "git tag/remote are driver-owned"),
    (r"\bgit\s+checkout\s+(infra-frozen|harness-frozen)\b", "frozen tags are driver-owned"),
    (r"\bsudo\b", "no sudo"),
    (r"\b(curl|wget|ssh|scp|rsync|nc|telnet)\b", "no network tools in the loop"),
    (r"\bop\s+(read|item|vault|signin)\b", "1Password is off-limits to the loop"),
    (r"\b(brew|npm|npx|yarn|pnpm|cargo|gem)\b", "no system package managers"),
    (r"(?<![\w./])pip3?\s+install\b", "use .venv/bin/pip, not the system pip"),
    (r"(?<![\w./])python3?\s+-m\s+pip\s+install\b", "use .venv/bin/pip, not the system pip"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(/|~|\$HOME|\.\.)(\s|$|/)", "refusing recursive delete outside the repo"),
    (r"\bcd\s+(~|/|\$HOME)(\s|$)", "stay inside the repo"),
    (r"\bkill(all)?\s+(-9\s+)?(-1|1)\b", "no process nuking"),
    (r"\b(launchctl|crontab|defaults\s+write|osascript)\b", "no system config changes"),
    (r"~/\.(claude|ssh|aws|config)\b|/Users/[^/\s]+/\.(claude|ssh|aws|config)\b", "no touching dotfiles"),
]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    cmd = str((payload.get("tool_input") or {}).get("command", ""))
    for pat, why in BLOCK:
        if re.search(pat, cmd):
            print(f"BLOCKED by driver/guard_bash.py: {why}. Command: {cmd[:200]}", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
