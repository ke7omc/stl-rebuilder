# Research: claude -p flags, hooks, permission modes, Ralph-loop pattern, guardrails

_Verified research dump, 2026-08-28. Treat as reference, not gospel: re-verify any API against the installed package version before relying on it._

## HEADLESS MODE (`-p`) EXACT MECHANICS

### CLI Flags
```bash
claude -p "prompt text"                      # non-interactive, read from arg
claude -p < prompt.txt                       # read from stdin (capped at 10MB)
claude -p "prompt" --model claude-opus-5    # model by FULL ID (e.g. claude-opus-5, not alias)
claude -p "prompt" --model opus              # model by ALIAS (opus, sonnet, haiku, fable, best)
claude -p "prompt" --max-turns 5             # stop after N turns
claude -p "prompt" --output-format json      # structured JSON with fields: result, session_id, total_cost_usd, model_usage
claude -p "prompt" --output-format stream-json # newline-delimited JSON events in real-time
claude -p "prompt" --output-format stream-json --include-partial-messages # stream tokens as they emit
claude -p "prompt" --allowedTools "Bash,Read,Edit,WebFetch" # auto-approve named tools (comma-separated)
claude -p "prompt" --disallowedTools "mcp__*" # deny MCP tools by regex
claude -p "prompt" --permission-mode auto    # auto-approve via classifier (available on some plans)
claude -p "prompt" --permission-mode dontAsk # deny everything not in allow rules
claude -p "prompt" --permission-mode acceptEdits # auto-approve file edits + safe filesystem commands
claude -p "prompt" --permission-mode bypassPermissions # skip all permission prompts (dangerous—use sandboxed env only)
claude -p "prompt" --continue                # continue most recent session
claude -p "prompt" --resume session-id       # continue specific session by ID
claude -p "prompt" --bare                    # skip hooks, skills, .claude/settings, CLAUDE.md, MCP servers—faster, safer for CI
claude -p "prompt" --add-dir /path1 /path2   # grant file access to additional directories
claude -p "prompt" --append-system-prompt "instructions" # add to system prompt
claude -p "prompt" --settings '{"model":"sonnet"}' # override settings via JSON
claude -p "prompt" --mcp-config path/to/mcp.json # load MCP servers
claude -p "prompt" --json [file.json]        # write structured output to file (or stdout if no arg)
claude -p "prompt" --json-schema '{"type":"object","properties":{...}}' # enforce JSON schema on output
claude -p "prompt" --verbose                 # debug output to stderr
claude -p "prompt" --forward-subagent-text   # emit subagent text blocks in stream-json (v2.1.211+)
```

**Exit codes:**
- `0` = success
- `1` = failed (error, cost limit, no cases, invalid args, auth failure, context overflow)
- `2` = partial (cost ceiling hit, auth failed at first run)
- `130` = SIGINT (Ctrl+C)
- `143` = SIGTERM (kill signal)

**JSON output fields** (`--output-format json`):
```json
{
  "result": "text output",
  "session_id": "session-uuid",
  "total_cost_usd": 0.123,
  "model_usage": {
    "claude-opus-5": {
      "input_tokens": 1000,
      "output_tokens": 500,
      "cache_creation_tokens": 0,
      "cache_read_tokens": 0
    }
  },
  "structured_output": { /* if --json-schema */ },
  "duration_seconds": 45
}
```

**stream-json line format:**
```json
{"type":"system", "subtype":"init", "modelUsage":{...}, "plugins":[...], "mcp_servers":[...]}
{"type":"stream_event", "event":{"type":"message_start",...}}
{"type":"stream_event", "event":{"delta":{"type":"text_delta", "text":"token..."}}}
{"type":"result", "text":"final output", "session_id":"...", "total_cost_usd":0.123}
```

---

## SESSION CONTINUITY: `--continue` VS `--resume` VS FRESH

| Pattern | Command | State Persistence | Context Reuse | Best For |
|---------|---------|-------------------|---------------|----------|
| **Continue** | `claude -p "follow-up" --continue` | Session ID kept | Full context preserved | Sequential fix iterations (same context grows) |
| **Resume by ID** | `claude -p "follow-up" --resume abc123` | Explicit session ID | Full context + auto-summary if large | Long-running tasks resumed later |
| **Fresh per iteration** | bash loop with separate `claude -p` calls | None; fresh session each time | Only git/files preserved, new context each call | "Ralph Wiggum" pattern (fresh perspective per turn) |

**Key tradeoff:**
- `--continue` / `--resume`: **context grows unbounded** → tokens accumulate → eventually context-window hits auto-compaction or overflow
- **Fresh per iteration**: **context resets** → no token accumulation across iterations, but **agent loses conversation history** (must re-read files, CLAUDE.md, progress state from disk/git)

**Recommendation for unattended fix-test-fix loop:**
Use **fresh context per iteration** + file-based state (`PROGRESS.md`, `fix_plan.md`, staged git diff) so:
1. Agent memory doesn't bloat
2. Each iteration starts crisp
3. Git history is clean (one commit per fix)

---

## MODEL FLAGS: ALIAS VS FULL ID

**Accepted by `--model`:**

```bash
--model opus              # Alias → resolves to latest Opus (v2.1.219+ = Opus 5)
--model sonnet            # Alias → resolves to latest Sonnet (v2.1.197+ = Sonnet 5)
--model haiku
--model fable             # Claude Fable 5 (requires v2.1.170+)
--model best              # Uses Fable 5 if available, else Opus
--model opusplan          # Opus for planning, Sonnet for execution (auto-switch at plan boundary)
--model claude-opus-5     # Full model ID (exact)
--model claude-sonnet-5
--model claude-haiku-4-5-20250514
```

**Environment variable precedence (lowest to highest):**
1. User settings `~/.claude/settings.json` key `model`
2. `ANTHROPIC_MODEL` env var
3. `--model` CLI flag
4. Managed/policy settings (highest)

**For Opus 5 specifically:**
- Use alias `--model opus` if available on your plan
- Use full ID `--model claude-opus-5` to pin exact version
- Requires Claude Code v2.1.219+; run `claude update` to upgrade

---

## HOOKS JSON SHAPES (`.claude/settings.json`)

### Stop Hook (prompt-based, for `/goal` or custom completion checks)
```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "prompt",
            "prompt": "Check if all tests pass and the build is clean. Return {\"ok\": true} or {\"ok\": false, \"reason\": \"...remaining work...\"}"
          }
        ]
      }
    ]
  }
}
```
- Fires after **every turn** (not just task completion)
- Model evaluates condition → returns `{"ok": true}` (stop) or `{"ok": false, "reason": "..."}` (continue with reason as next directive)
- Optional: `"impossible": true` on `ok: false` → marks condition impossible, stops loop with "impossible" status
- Can also use `"type": "agent"` for tool-use verification (subagent spins up to run tests, check files, etc.)

### PostToolUse Hook (command-based, enforce tests after tool execution)
```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'npm test 2>&1 | tail -20; [[ $? -eq 0 ]] && echo {\\\"hookSpecificOutput\\\":{\\\"hookEventName\\\":\\\"PostToolUse\\\",\\\"decision\\\":\\\"allow\\\"}} || echo {\\\"hookSpecificOutput\\\":{\\\"hookEventName\\\":\\\"PostToolUse\\\",\\\"decision\\\":\\\"block\\\",\\\"reason\\\":\\\"Tests failed\\\"}}'",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```
- Exit code `0` → allow (no blocking decision)
- Exit code `2` → block the tool call
- JSON stdout `{"hookSpecificOutput": {"decision": "block", "reason": "..."}}` → block with feedback to Claude

### PreToolUse Hook (block risky commands in unattended mode)
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.command' | grep -E '(git push|rm -rf|sudo)' && exit 2 || exit 0"
          }
        ]
      }
    ]
  }
}
```

### SessionStart Hook (inject context after each fresh session)
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",  // or "compact", "resume", "clear", "fork"
        "hooks": [
          {
            "type": "command",
            "command": "echo 'Iteration state:' && cat PROGRESS.md && echo '---' && git status --short | head -5"
          }
        ]
      }
    ]
  }
}
```
- Stdout text is injected into Claude's context as a system reminder

---

## PERMISSION MODES FOR UNATTENDED LOOPS

| Mode | Prompts? | Best For |
|------|----------|----------|
| **`auto`** | No; classifier reviews each action | Multi-step automation with safety checks (requires feature flag) |
| **`dontAsk`** | No; denies unless pre-approved | Locked-down CI/script (strict allowlist required) |
| **`acceptEdits`** | Partial; auto-approves file edits + safe filesystem commands (`mkdir`, `touch`, `mv`, `cp`). Blocks network, subprocesses | Local unattended fix-test loops with file access |
| **`bypassPermissions`** | No; skips all checks | Sandboxed container only (dangerous) |
| **`default` (Manual)** | Yes; prompts on every tool | Not suitable for unattended |

**Recommended for unattended bash-driver loop:**
```json
{
  "permissions": {
    "defaultMode": "acceptEdits",
    "allow": [
      "Bash(npm test *)",
      "Bash(npm run build *)",
      "Bash(git commit *)",
      "Bash(git status *)",
      "Read",
      "Edit"
    ],
    "deny": [
      "Bash(git push *)",
      "Bash(git reset --hard *)",
      "WebFetch"
    ]
  }
}
```

---

## `/goal` VS `/loop` VS BASH WHILE-LOOP

| Mechanism | Syntax | Behavior | Stops When | Best For |
|-----------|--------|----------|-----------|----------|
| **`/goal`** | `/goal "all tests pass and lint is clean"` | Session stays open; model evaluates condition after **each turn**; continues automatically | Model confirms condition met, or impossible, or error, or `/goal clear` | Unattended self-correcting loops in **interactive sessions** |
| **`/loop`** | `/loop 5m /check-deploy` or `/loop /check-deploy` (self-paced) | Runs prompt on interval; dynamic pacing chooses 1m–1h based on progress | 7-day expiry, manually canceled, or Claude decides work done | Session polling (PR status, build checks) |
| **Bash while-loop** | `while true; do claude -p ...; done` | Fresh session per iteration; exits on exit code | Script logic (e.g., `exit 0` on success, retry count limit) | **Unattended fix-test-fix** (Ralph Wiggum pattern) |

**For unattended loop on remote machine (no interactive session):**
- `claude -p "/goal ..."` in `-p` mode works but blocks until goal met; must handle timeout at bash level
- **Bash while-loop is simpler** for CI/cron: each iteration is self-contained, easy to cap iterations, git history clean

---

## RALPH WIGGUM LOOP PATTERN (File-Based State)

**Core idea:** Fresh agent context per iteration; state tracked in **files** (not conversation memory).

```bash
#!/bin/bash
ITERATIONS=0
MAX_ITERATIONS=20
FAILED_ITERATIONS=0

while [[ $ITERATIONS -lt $MAX_ITERATIONS ]]; do
  ITERATIONS=$((ITERATIONS + 1))
  
  # Fresh session, read state from disk
  claude -p "
    Read PROGRESS.md to see what's done and what's next.
    Read PROMPT.md for the goal.
    Read any failing test output in FAILURES.txt.
    Select the highest-priority incomplete task, implement it, run tests.
    Update PROGRESS.md: mark tasks done, log blockers.
    Commit your changes with 'git commit -am \"<task>: implementation\"' if all tests pass.
    If tests fail, append the error to FAILURES.txt and explain the fix in fix_plan.md.
  " \
    --model claude-opus-5 \
    --permission-mode acceptEdits \
    --allowedTools "Bash(npm test *),Bash(npm run build *),Bash(git commit *),Bash(git status *),Read,Edit" \
    --max-turns 10 \
    --output-format json | jq -r '.result' > iteration-${ITERATIONS}.log
  
  STATUS=$?
  
  if [[ $STATUS -eq 0 ]]; then
    # Check if done: PROGRESS.md shows all tasks complete
    if grep -q "^## Status: COMPLETE" PROGRESS.md; then
      echo "✓ Goal achieved after $ITERATIONS iterations"
      exit 0
    fi
    FAILED_ITERATIONS=0
  else
    FAILED_ITERATIONS=$((FAILED_ITERATIONS + 1))
    if [[ $FAILED_ITERATIONS -ge 3 ]]; then
      echo "✗ 3 consecutive failures; stopping"
      exit 1
    fi
  fi
  
  # Protect frozen harness (driver never overwrites tests)
  git checkout HEAD -- tests/ harness/
  
  sleep 2  # brief pause between iterations
done

echo "✗ Reached max iterations ($MAX_ITERATIONS) without completion"
exit 1
```

**State files:**
- `PROGRESS.md`: checklist of tasks, current status, what's next
- `PROMPT.md` or `SPEC.md`: goal and requirements (never changes)
- `FAILURES.txt`: last test failure output (append-only log)
- `fix_plan.md`: agent's notes on next approach (can be overwritten each iteration)

**Key features:**
- **One iteration = one task**: each iteration picks one job, does it, commits
- **Clean git history**: one commit per task, rollback is trivial
- **Idempotent agent**: same prompt, same state → same decision (tests validate)
- **Automatic recovery**: if iteration fails, next iteration reads failure log and pivots

**Sources:** [Ralph Loop Repository](https://github.com/agenticloops-ai/ralph-loop), [Ralph Wiggum Methodology](https://github.com/ghuntley/how-to-ralph-wiggum)

---

## GUARDRAILS FOR UNATTENDED LOOPS

| Guardrail | Implementation |
|-----------|----------------|
| **Iteration cap** | `MAX_ITERATIONS=20` in bash loop; `--max-turns 10` per session |
| **Per-iteration turn limit** | `--max-turns 10` forces agent to commit/stop after 10 API calls |
| **Protect test/harness** | `git checkout HEAD -- tests/ harness/` before scoring; driver only reads, never edits |
| **Cost ceiling** | `--max-budget-usd 50` to stop if spend exceeds threshold (v2.1.245+) |
| **Timeout per iteration** | Bash `timeout 300 claude -p ...` (5 min), exit 124 on timeout |
| **No-progress detector** | Compare git diff size iteration-to-iteration; exit if zero changes 3x in a row |
| **Commit each iteration** | Agent commits after success; driver checks `git log -1 --oneline` to verify |
| **Log per iteration** | Capture stdout/stderr to `iteration-${N}.log` for post-mortem |
| **Scoring sandbox** | Separate bash subprocess runs tests (not agent); agent can't corrupt test harness |

**Example no-progress check:**
```bash
PREV_HASH=$(git rev-parse HEAD)
# ... iteration runs ...
CURR_HASH=$(git rev-parse HEAD)
if [[ "$PREV_HASH" == "$CURR_HASH" ]]; then
  NO_PROGRESS=$((NO_PROGRESS + 1))
  [[ $NO_PROGRESS -ge 3 ]] && exit 1
else
  NO_PROGRESS=0
fi
```

---

## BILLING & RATE LIMITS

**`-p` mode uses:**
- **API key** (if `ANTHROPIC_API_KEY` set) → per-token billing
- **Claude subscription** (if logged in via `/login`) → draws from Pro/Max/Team allowance
- **NOT** Managed Agents (Managed Agents is separate, Anthropic-hosted sandbox)

**Cost tracking:**
```bash
claude -p "..." --output-format json | jq '.total_cost_usd'  # client-side estimate
```

**Rate limit behavior (Opus 5 back-to-back iterations):**
- **Anthropic API:** standard rate limits apply (TPM/RPM per org). Multiple rapid iterations may hit 429 rate-limit errors → Claude Code auto-retries with exponential backoff.
- **Subscription plans (Pro/Max):** draw from shared 5-hour rolling window; no per-model rate limit visible.
- **Fallback:** `--fallback-model sonnet,haiku` chains; if Opus unavailable, automatically retry on Sonnet.

**Opus 5 cost:** ~$15 per million input tokens, ~$60 per million output tokens (2026 estimate). Rough per-iteration cost: 5k input + 2k output ≈ $0.18.

**Environment variable to cap spend:**
```bash
claude -p "..." --max-budget-usd 5.00  # fail if total spend exceeds $5 (v2.1.245+)
```

---

## FINAL MECHANICS SUMMARY

| Requirement | Exact Flag / Config | Notes |
|-------------|-------------------|-------|
| **Headless agent** | `claude -p "prompt"` | Exits with code 0 on success, nonzero on failure |
| **Opus 5 model** | `--model claude-opus-5` or `--model opus` | Alias okay; full ID safer for pinning |
| **Fresh context per iteration** | Bash loop with separate `claude -p` calls | No `--continue` / `--resume`; one invocation per iteration |
| **Auto-approve tools** | `--permission-mode acceptEdits --allowedTools "Bash(...),Read,Edit"` | No prompts; strict allowlist required |
| **JSON output** | `--output-format json \| jq '.result'` | Extracts text; `.total_cost_usd` for billing |
| **Tests must pass before stop** | **Stop hook** (prompt-based) or **PostToolUse hook** (command-based) | Stop hook fires after every turn; can block if tests fail |
| **Fresh tests each iteration** | `git checkout HEAD -- tests/` before scoring | Driver (not agent) owns test harness |
| **State across iterations** | Files: `PROGRESS.md`, `FAILURES.txt`, `PROMPT.md` | Agent reads disk; no conversation carryover |
| **Exit on success** | Check `PROGRESS.md` for "COMPLETE" marker | Agent updates on each iteration |
| **Max iterations** | Bash `for i in {1..20}` or while-loop with counter | Prevent runaway loops |
| **Cost cap** | `--max-budget-usd 50` (v2.1.245+) | Fail if total spend exceeds threshold |
| **Rate limit fallback** | `--fallback-model sonnet,haiku` | Auto-retry on Sonnet if Opus rate-limited |
| **Billing source** | `ANTHROPIC_API_KEY` (API) or `/login` (subscription) | Charges per token or draws from plan allowance |

---

**Documentation references (current as of August 2026):**

- [Claude Code Headless Reference](https://code.claude.com/docs/en/headless.md)
- [CLI Reference](https://code.claude.com/docs/en/cli-reference.md)
- [Goal Command (`/goal`)](https://code.claude.com/docs/en/goal.md)
- [Hooks Guide](https://code.claude.com/docs/en/hooks-guide.md)
- [Permissions](https://code.claude.com/docs/en/permissions.md)
- [Model Configuration](https://code.claude.com/docs/en/model-config.md)
- [Costs & Billing](https://code.claude.com/docs/en/costs.md)
- [Scheduled Tasks (`/loop`)](https://code.claude.com/docs/en/scheduled-tasks.md)
- [Ralph Wiggum Loop Pattern](https://github.com/agenticloops-ai/ralph-loop)