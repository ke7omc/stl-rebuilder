#!/usr/bin/env python3
"""
loop.py — driver for the self-annealing STL→solid coding loop.

Each iteration: restore frozen files → usage gate → pick mode/model → run a fresh headless
`claude -p` (streamed, with heartbeats) → restore frozen files again → auto-commit → score with
the frozen harness (memory-guarded) → update state → repeat. The driver, never the agent,
decides when a milestone passes.

Standard library only. State lives in state/loop_state.json; logs in logs/.
Usage:
  python3 loop.py                 # run until done / guardrail
  python3 loop.py --once          # exactly one iteration (supervised runs)
  python3 loop.py --dry-run       # show config, compose the prompt, call nothing
  python3 loop.py --status        # human-readable status (add --json for the raw state)
  python3 loop.py --stop          # graceful: finish + evaluate the in-flight iteration, then exit
  python3 loop.py --kill          # now: kill the agent, checkpoint-commit its work, save state, exit
  python3 loop.py --reset-stall
  python3 loop.py --recheck-models

Ctrl-C in the driver's terminal behaves like --kill (save-then-stop). Everything is resumable:
rerun ./loop.sh and it continues exactly where it left off (a pending evaluation is run first).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_DIR, LOGS_DIR, OUT_DIR = ROOT / "state", ROOT / "logs", ROOT / "out"
STATE_FILE = STATE_DIR / "loop_state.json"
LOCK_FILE = STATE_DIR / "loop.lock"
STOP_FILE = STATE_DIR / "STOP"
CURRENT_FILE = STATE_DIR / "current.json"
DASHBOARD_FILE = STATE_DIR / "STATUS.md"
VENV_PY = ROOT / ".venv" / "bin" / "python"

# ----------------------------------------------------------------------------- config
# Every value can be overridden with an env var of the same name prefixed LOOP_, e.g.
#   LOOP_WINDOW_BUDGET_USD=60 LOOP_MODEL_DEFAULT=claude-opus-5 ./loop.sh
CONFIG = {
    # models / effort
    "MODEL_DEFAULT": "claude-sonnet-5",   # full IDs: the CLI's alias table can lag (2.1.152: 'sonnet' → 4.6)
    "EFFORT_DEFAULT": "medium",
    "MODEL_ESCALATE": "claude-opus-5",
    "EFFORT_ESCALATE": "high",
    "EFFORT_TOURNAMENT": "xhigh",
    "MODEL_REVIEW": "claude-opus-5",
    "EFFORT_REVIEW": "high",
    "FALLBACK_MODEL": "",          # e.g. "sonnet" — applied only to escalated/tournament runs
    # stall policy (counts are consecutive iterations without progress on one milestone)
    "STALL_ESCALATE": 3,           # switch to MODEL_ESCALATE after this many
    "STALL_TOURNAMENT": 4,         # run a tournament this many stalls after escalation
    "STALL_MAX": 12,               # stop with a status report
    "PROGRESS_MIN_DELTA": 0.005,   # smaller gains do not reset the stall counter (creeping is stalling)
    # caps (wall clock per iteration, by mode)
    "MAX_ITERATIONS": 300,
    "ITER_TIMEOUT_S": 5400,        # 90 min: normal / handoff iterations
    "ESCALATED_TIMEOUT_S": 7200,   # 120 min: escalated (Opus) iterations
    "REVIEW_TIMEOUT_S": 7200,      # 120 min: the M0 review-harness pass
    "TOURNAMENT_TIMEOUT_S": 10800, # 180 min
    "SCORE_TIMEOUT_S": 2400,       # 40 min for a selftest / scorer run (MEM_LIMIT_GB is the real backstop)
    "COMMIT_BY_PCT": 80.0,         # header tells the agent to have working state committed by this % of its budget
    "BUDGET_USD_DEFAULT": 10.0,    # --max-budget-usd per iteration (Sonnet M1/M2 iterations ran $5–6 in 10–14 min)
    "BUDGET_USD_ESCALATE": 15.0,
    "BUDGET_USD_TOURNAMENT": 20.0,
    # machine protection / feedback
    "MEM_LIMIT_GB": 24.0,          # kill a selftest/scorer whose process tree exceeds this RSS
    "HEARTBEAT_S": 300,            # progress line while an agent or scorer runs
    # usage pacing (API-equivalent dollars reported by the CLI; tune after observing limits)
    "WINDOW_HOURS": 5.0,
    "WINDOW_BUDGET_USD": 120.0,
    "WINDOW_GATE_PCT": 75.0,
    "WEEKLY_BUDGET_USD": 0.0,      # 0 = disabled
    "RATE_LIMIT_SLEEP_S": 1800,    # when the reset time cannot be parsed
    "RATE_LIMIT_SLEEP_MAX_S": 18000,
    "SLEEP_BETWEEN_ITER_S": 20,
    # permissions
    "SKIP_PERMISSIONS": 0,         # 1 = --dangerously-skip-permissions (Brady's call)
    "USE_API_KEY": 0,              # 1 = keep ANTHROPIC_API_KEY in the env (bills the API)
}
for _k in list(CONFIG):
    _v = os.environ.get("LOOP_" + _k)
    if _v is not None:
        CONFIG[_k] = type(CONFIG[_k])(_v) if not isinstance(CONFIG[_k], bool) else _v == "1"

INFRA_PATHS = ["loop.py", "loop.sh", "driver", "PROMPT.md", "MISSION.md", "CLAUDE.md",
               ".claude", "README.md"]
INFRA_TAG, HARNESS_TAG = "infra-frozen", "harness-frozen"
MILESTONES = ["M0", "M1", "M2", "M3", "M4", "M5", "HANDOFF", "DONE"]
REQUIRED_FLAGS = ["--effort", "--max-budget-usd", "--permission-mode", "--output-format",
                  "--strict-mcp-config", "--mcp-config", "--setting-sources", "--verbose",
                  "--disable-slash-commands", "--disallowedTools"]
RATE_LIMIT_RE = re.compile(
    r"rate.?limit|usage limit|limit (has been |was )?reached|out of (usage|credits)|"
    r"too many requests|\b429\b|overloaded|\b529\b|resets? (at|in)|try again (at|in)|"
    r"exceeded your|quota", re.I)
# the driver's own agent invocations are recognisable by this pair of flags
AGENT_SIGNATURE = ("claude -p --model", "--disallowedTools WebFetch,WebSearch")

CURRENT_AGENT_PID: int | None = None   # set while an agent runs (for save-then-stop)
CURRENT_CHILD_PID: int | None = None   # set while a selftest/scorer runs


# ----------------------------------------------------------------------------- utils
def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def ts() -> str:
    return now().strftime("%Y-%m-%dT%H:%M:%S")


def hhmm(t: dt.datetime) -> str:
    return t.strftime("%H:%M")


def fmt_min(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)} s"
    m = int(round(seconds / 60.0))
    return f"{m} min" if m < 60 else f"{m // 60}h{m % 60:02d}m"


def log(msg: str) -> None:
    line = f"[{ts()}] {msg}"
    print(line, flush=True)
    LOGS_DIR.mkdir(exist_ok=True)
    with (LOGS_DIR / "loop.log").open("a") as fh:
        fh.write(line + "\n")


def sh(args: list[str], check: bool = False, timeout: int | None = None, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=check,
                          timeout=timeout, **kw)


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return sh(["git", *args], check=check)


def git_head() -> str:
    return git("rev-parse", "HEAD").stdout.strip()


def tag_exists(tag: str) -> bool:
    return git("rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode == 0


# ----------------------------------------------------------------------------- processes
def pid_alive(pid: int) -> bool:
    """True for a live process; False for a missing one or a zombie (dead, awaiting reap)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], text=True, capture_output=True).stdout.strip()
    except OSError:
        return True
    return bool(stat) and not stat.startswith("Z")


def descendants(pid: int) -> list[int]:
    """All transitive children of pid (collected BEFORE killing anything — orphans get
    re-parented to launchd the moment their parent dies)."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid="], text=True, capture_output=True).stdout
    except OSError:
        return []
    kids: dict[int, list[int]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], [pid]
    while stack:
        cur = stack.pop()
        for c in kids.get(cur, []):
            if c not in found:
                found.append(c)
                stack.append(c)
    return found


def kill_tree(pid: int | None, grace_s: float = 8.0) -> int:
    """SIGTERM a process and every descendant, SIGKILL whatever survives grace_s. Returns the
    number of processes signalled. Safe to call on a pid that is already gone."""
    if not pid:
        return 0
    pids = [pid] + descendants(pid)
    for p in pids:
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + grace_s
    alive = [p for p in pids if pid_alive(p)]
    while alive and time.time() < deadline:
        time.sleep(0.25)
        alive = [p for p in alive if pid_alive(p)]
    for p in alive:
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return len(pids)


def rss_gb_tree(pid: int) -> float:
    pids = [pid] + descendants(pid)
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", ",".join(map(str, pids))],
                             text=True, capture_output=True).stdout
    except OSError:
        return 0.0
    kb = sum(int(x) for x in out.split() if x.isdigit())
    return kb / 1048576.0


def loop_agent_pids() -> list[int]:
    """Processes that look like this driver's own `claude -p` invocations (from any earlier run)."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], text=True, capture_output=True).stdout
    except OSError:
        return []
    pids = []
    for line in out.splitlines():
        if all(sig in line for sig in AGENT_SIGNATURE):
            head = line.split(None, 1)[0]
            if head.isdigit():
                pids.append(int(head))
    return pids


# ----------------------------------------------------------------------------- state
def default_state() -> dict:
    return {
        "iteration": 0, "milestone": "M0", "m0_phase": "build",
        "best_progress": 0.0, "stall": 0, "escalated": False, "tournaments": 0,
        "usage": [], "history": [], "verified_models": {}, "stopped_reason": None,
        "pending_eval": None,        # set between "agent done" and "evaluation done"
        "last_eval": None,           # what the driver last concluded, for the next prompt header
        "last_selftest_s": None,     # duration of the last *completed* selftest run
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        st = json.loads(STATE_FILE.read_text())
        base = default_state()
        base.update(st)
        return base
    return default_state()


def save_state(st: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(STATE_FILE)


def set_current(info: dict | None) -> None:
    """state/current.json: what the driver is doing right now (read by --status)."""
    STATE_DIR.mkdir(exist_ok=True)
    if info is None:
        try:
            CURRENT_FILE.unlink()
        except FileNotFoundError:
            pass
        return
    CURRENT_FILE.write_text(json.dumps(info, indent=2))


def read_current() -> dict | None:
    if not CURRENT_FILE.exists():
        return None
    try:
        return json.loads(CURRENT_FILE.read_text())
    except json.JSONDecodeError:
        return None


def running_driver_pid() -> int | None:
    if not LOCK_FILE.exists():
        return None
    try:
        pid = int(LOCK_FILE.read_text().strip())
    except ValueError:
        return None
    return pid if pid_alive(pid) else None


def acquire_lock() -> None:
    STATE_DIR.mkdir(exist_ok=True)
    pid = running_driver_pid()
    if pid:
        sys.exit(f"another loop.py (pid {pid}) is running; use --stop/--kill, or remove {LOCK_FILE} if stale")
    LOCK_FILE.write_text(str(os.getpid()))


def release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


# ----------------------------------------------------------------------------- frozen files
def restore_frozen(report: bool = True) -> list[str]:
    """Restore infra (and harness once frozen) from their tags. Returns paths that changed."""
    changed: list[str] = []
    plans = [(INFRA_TAG, INFRA_PATHS)]
    if tag_exists(HARNESS_TAG):
        plans.append((HARNESS_TAG, ["harness"]))
    for tag, paths in plans:
        if not tag_exists(tag):
            continue
        before = git("status", "--porcelain", "--", *paths).stdout
        git("checkout", tag, "--", *paths)
        after = git("status", "--porcelain", "--", *paths).stdout
        if before.strip() or after.strip():
            diff = git("diff", "--cached", "--stat", "--", *paths).stdout + git("diff", "--stat", "--", *paths).stdout
            changed += [ln.strip() for ln in diff.splitlines() if "|" in ln]
    if changed and report:
        log(f"VIOLATION: agent modified frozen files; restored from tags: {changed}")
        git("add", "-A")
        git("commit", "-qm", "driver: restore frozen files modified by agent")
    return changed


def auto_commit(iteration: int, why: str = "agent left uncommitted changes") -> bool:
    if not git("status", "--porcelain").stdout.strip():
        return False
    git("add", "-A")
    r = git("commit", "-qm", f"iter {iteration}: driver auto-commit ({why})")
    if r.returncode == 0:
        log(f"driver auto-committed uncommitted agent changes ({why})")
        return True
    return False


# ----------------------------------------------------------------------------- usage pacing
def window_cost(st: dict, hours: float) -> float:
    cutoff = now() - dt.timedelta(hours=hours)
    return sum(u["cost_usd"] for u in st["usage"]
               if dt.datetime.fromisoformat(u["ts"]) > cutoff)


def window_gate_usd() -> float:
    return CONFIG["WINDOW_BUDGET_USD"] * CONFIG["WINDOW_GATE_PCT"] / 100.0


def stop_requested() -> bool:
    return STOP_FILE.exists()


def sleep_interruptible(secs: float, why: str = "") -> bool:
    """Sleep in 60 s slices; returns False early if state/STOP appears."""
    end = time.time() + secs
    while time.time() < end:
        if stop_requested():
            log(f"STOP requested while {why or 'sleeping'} — waking up to stop")
            return False
        time.sleep(min(60.0, max(0.0, end - time.time())))
    return True


def usage_gate(st: dict) -> None:
    """Sleep (between iterations only) until the rolling-window spend is under the gate."""
    checks = [(CONFIG["WINDOW_HOURS"], window_gate_usd(), "5h-window")]
    if CONFIG["WEEKLY_BUDGET_USD"] > 0:
        checks.append((24 * 7.0, CONFIG["WEEKLY_BUDGET_USD"] * CONFIG["WINDOW_GATE_PCT"] / 100.0, "weekly"))
    for hours, limit, name in checks:
        spent = window_cost(st, hours)
        if spent < limit:
            continue
        # find the earliest moment the window drops below the limit
        entries = sorted(st["usage"], key=lambda u: u["ts"])
        wake = None
        for u in entries:
            expiry = dt.datetime.fromisoformat(u["ts"]) + dt.timedelta(hours=hours)
            remaining = sum(v["cost_usd"] for v in entries
                            if dt.datetime.fromisoformat(v["ts"]) > expiry - dt.timedelta(hours=hours))
            if remaining < limit:
                wake = expiry
                break
        wake = wake or (now() + dt.timedelta(hours=hours))
        secs = max(60, int((wake - now()).total_seconds()) + 60)
        log(f"USAGE GATE ({name}): spent ${spent:.2f} of ${limit:.2f} gate; "
            f"pausing {secs // 60} min until {hhmm(wake)} (work is committed; safe to Ctrl-C or --stop; "
            f"raise LOOP_WINDOW_BUDGET_USD if your plan has headroom)")
        st["history"].append({"ts": ts(), "event": "usage_pause", "seconds": secs, "window": name})
        save_state(st)
        write_dashboard(st, note=f"paused by usage gate until {hhmm(wake)}")
        if not sleep_interruptible(secs, "paused by the usage gate"):
            return


def parse_reset_delay(text: str) -> int | None:
    """Best-effort: seconds until the limit resets, parsed from an error message."""
    m = re.search(r"resets? in\s+(?:(\d+)\s*h(?:ours?)?)?\s*(?:(\d+)\s*m(?:in(?:utes?)?)?)?", text, re.I)
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + 120
    m = re.search(r"(?:resets?|try again)\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text, re.I)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3):
            hour = hour % 12 + (12 if m.group(3).lower() == "pm" else 0)
        target = now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now():
            target += dt.timedelta(days=1)
        return int((target - now()).total_seconds()) + 120
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?)", text)
    if m:
        try:
            t = dt.datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.astimezone()
            return max(60, int((t - now()).total_seconds()) + 120)
        except ValueError:
            pass
    m = re.search(r"retry.after[^0-9]*(\d+)", text, re.I)
    if m:
        return int(m.group(1)) + 30
    return None


# ----------------------------------------------------------------------------- claude
def claude_args(model: str, effort: str, budget: float, fallback: str = "") -> list[str]:
    # stream-json (+ --verbose, which print mode requires for it) so the driver can watch the
    # agent work: turn count, last tool call, and the final result event with cost/turns.
    args = ["claude", "-p", "--model", model, "--effort", effort,
            "--output-format", "stream-json", "--verbose", "--max-budget-usd", f"{budget:.2f}",
            "--disallowedTools", "WebFetch,WebSearch",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--disable-slash-commands", "--setting-sources", "project,local"]
    if CONFIG["SKIP_PERMISSIONS"]:
        args.append("--dangerously-skip-permissions")
    else:
        args += ["--permission-mode", "acceptEdits"]
    if fallback:
        args += ["--fallback-model", fallback]
    return args


def claude_env() -> dict:
    env = dict(os.environ)
    if not CONFIG["USE_API_KEY"] and env.pop("ANTHROPIC_API_KEY", None):
        log("note: ANTHROPIC_API_KEY removed from the agent env so runs bill the subscription "
            "(set LOOP_USE_API_KEY=1 to keep it)")
    env.setdefault("CLAUDE_PROJECT_DIR", str(ROOT))
    return env


def parse_claude_json(stdout: str) -> dict | None:
    """For --output-format json (preflight smoke tests) and as a fallback for stream output:
    the whole thing, or the last JSON object line."""
    stdout = stdout.strip()
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


class StreamTracker:
    """Consumes stream-json lines from `claude -p` and keeps what the driver cares about."""

    def __init__(self) -> None:
        self.turns = 0
        self.tool_calls = 0
        self.last_tool = ""
        self.last_tool_at: float | None = None
        self.last_text = ""
        self.result: dict | None = None
        self.session_id: str | None = None
        self.raw_tail: collections.deque[str] = collections.deque(maxlen=60)
        self.bad_lines = 0

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        self.raw_tail.append(line[:3000])
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            self.bad_lines += 1
            return
        if not isinstance(ev, dict):
            return
        t = ev.get("type")
        if t == "system":
            self.session_id = ev.get("session_id") or self.session_id
        elif t == "assistant":
            self.turns += 1
            for b in (ev.get("message") or {}).get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    arg = (inp.get("command") or inp.get("file_path") or inp.get("pattern")
                           or inp.get("description") or inp.get("prompt") or "")
                    arg = " ".join(str(arg).split())[:80]
                    self.last_tool = f"{b.get('name')}({arg})" if arg else str(b.get("name"))
                    self.last_tool_at = time.time()
                    self.tool_calls += 1
                elif b.get("type") == "text" and b.get("text"):
                    self.last_text = str(b["text"])[-600:]
        elif t == "result":
            self.result = ev
            self.session_id = ev.get("session_id") or self.session_id


def run_claude(prompt: str, model: str, effort: str, budget: float, timeout: int,
               fallback: str = "", tag: str = "", hb_extra=None) -> dict:
    """Run one agent iteration. Streams its output, logs a heartbeat every HEARTBEAT_S, enforces
    the wall-clock timeout by killing the whole process tree, and survives Ctrl-C cleanly."""
    global CURRENT_AGENT_PID
    args = claude_args(model, effort, budget, fallback)
    t0 = time.time()
    deadline = now() + dt.timedelta(seconds=timeout)
    log(f"claude start: model={model} effort={effort} budget=${budget:.2f} "
        f"timeout={fmt_min(timeout)} (deadline {hhmm(deadline)}) {tag}")
    proc = subprocess.Popen(args, cwd=ROOT, env=claude_env(), text=True, bufsize=1,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    CURRENT_AGENT_PID = proc.pid
    trk = StreamTracker()
    err_lines: collections.deque[str] = collections.deque(maxlen=400)

    def read_out() -> None:
        try:
            for line in proc.stdout:
                trk.feed(line)
        except (ValueError, OSError):
            pass

    def read_err() -> None:
        try:
            for line in proc.stderr:
                err_lines.append(line)
        except (ValueError, OSError):
            pass

    threads = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
    for th in threads:
        th.start()
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass

    timed_out = False
    next_hb = t0 + CONFIG["HEARTBEAT_S"]
    try:
        while proc.poll() is None:
            el = time.time() - t0
            if el > timeout:
                timed_out = True
                log(f"TIMEOUT: agent exceeded {fmt_min(timeout)} — killing its process tree "
                    f"({trk.turns} turns seen, last tool: {trk.last_tool or '—'})")
                kill_tree(proc.pid)
                break
            if time.time() >= next_hb:
                ago = f" {fmt_min(time.time() - trk.last_tool_at)} ago" if trk.last_tool_at else ""
                extra = f" — {hb_extra()}" if hb_extra else ""
                log(f"[hb] {tag}: {fmt_min(el)} of {fmt_min(timeout)} ({el / timeout:.0%}), deadline {hhmm(deadline)}"
                    f" — turns {trk.turns}, tools {trk.tool_calls}, last: {trk.last_tool or '—'}{ago}{extra}")
                next_hb += CONFIG["HEARTBEAT_S"]
            time.sleep(1)
    except KeyboardInterrupt:
        log(f"interrupt: killing the agent process tree ({trk.turns} turns seen)")
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        CURRENT_AGENT_PID = None
        raise
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid, grace_s=2)
        proc.wait()
    for th in threads:
        th.join(timeout=5)
    CURRENT_AGENT_PID = None

    data = trk.result or {}
    err = "".join(err_lines)
    text_blob = "\n".join(list(trk.raw_tail)[-15:] + [err[-4000:], str(data.get("result", ""))[-2000:]])
    rate_limited = bool(RATE_LIMIT_RE.search(text_blob)) and (proc.returncode != 0 or bool(data.get("is_error")))
    cost = float(data.get("total_cost_usd") or data.get("cost_usd") or 0.0)
    estimated = False
    if not cost and trk.turns > 0 and (timed_out or proc.returncode != 0):
        # the CLI died before reporting; assume the cap was spent so the usage gate errs safe
        cost, estimated = float(budget), True
    usage = data.get("modelUsage") or data.get("model_usage") or {}
    served = list(usage.keys()) if isinstance(usage, dict) else []
    return {
        "ok": proc.returncode == 0 and not data.get("is_error") and not timed_out,
        "exit": proc.returncode, "timed_out": timed_out, "rate_limited": rate_limited,
        "reset_delay": parse_reset_delay(text_blob) if rate_limited else None,
        "cost_usd": cost, "estimated_cost": estimated, "served_models": served,
        "session_id": data.get("session_id") or trk.session_id,
        "num_turns": data.get("num_turns") or (trk.turns or None), "turns_seen": trk.turns,
        "tool_calls": trk.tool_calls, "last_tool": trk.last_tool,
        "duration_s": round(time.time() - t0, 1),
        "result_tail": (str(data.get("result", "")) or trk.last_text)[-1500:], "stderr_tail": err[-1500:],
        "raw": data,
    }


# ----------------------------------------------------------------------------- guarded subprocesses (scoring)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
NOISE_RE = re.compile(r"^\s*\*|Step File Name|Sending all data|Transfer(ring)? (Mode|Shape)|^\s*$")


def clean_tail(text: str, n: int) -> str:
    """Strip ANSI colour codes and OCCT's STEP-writer banner noise, keep the last n chars."""
    lines = [ln for ln in ANSI_RE.sub("", text).splitlines() if not NOISE_RE.search(ln)]
    return "\n".join(lines)[-n:]


def run_guarded(args: list[str], timeout: int, label: str) -> dict:
    """Run a harness subprocess with: a wall-clock cap, a memory cap on its whole process tree,
    heartbeat lines, and clean Ctrl-C handling. Returns code/stdout/stderr/note/duration/peak."""
    global CURRENT_CHILD_PID
    proc = subprocess.Popen(args, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    CURRENT_CHILD_PID = proc.pid
    out_lines: list[str] = []
    err_lines: list[str] = []

    def reader(stream, sink):
        try:
            for line in stream:
                sink.append(line)
        except (ValueError, OSError):
            pass

    threads = [threading.Thread(target=reader, args=(proc.stdout, out_lines), daemon=True),
               threading.Thread(target=reader, args=(proc.stderr, err_lines), daemon=True)]
    for th in threads:
        th.start()
    t0, peak, note = time.time(), 0.0, None
    next_hb = t0 + CONFIG["HEARTBEAT_S"]
    limit = float(CONFIG["MEM_LIMIT_GB"])
    try:
        while proc.poll() is None:
            el = time.time() - t0
            if el > timeout:
                note = f"{label} timed out after {fmt_min(timeout)} (killed by the driver)"
                log("SCORING: " + note)
                kill_tree(proc.pid)
                break
            rss = rss_gb_tree(proc.pid)
            peak = max(peak, rss)
            if rss > limit:
                note = (f"killed by the driver: {label} process tree reached {rss:.1f} GB RSS, over "
                        f"MEM_LIMIT_GB={limit:g} — a memory blowup in the harness, not a timeout")
                log("SCORING: " + note)
                kill_tree(proc.pid)
                break
            if time.time() >= next_hb:
                passed = sum(1 for ln in out_lines if ln.startswith("[PASS]"))
                failed = sum(1 for ln in out_lines if ln.startswith("[FAIL]"))
                log(f"[scoring] {label}: {fmt_min(el)} elapsed, RSS {rss:.1f} GB (peak {peak:.1f}, limit {limit:g}), "
                    f"checks so far {passed} pass / {failed} fail")
                next_hb += CONFIG["HEARTBEAT_S"]
            time.sleep(5)
    except KeyboardInterrupt:
        log(f"interrupt: killing {label} process tree")
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        CURRENT_CHILD_PID = None
        raise
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid, grace_s=2)
        proc.wait()
    for th in threads:
        th.join(timeout=5)
    CURRENT_CHILD_PID = None
    code = proc.returncode
    if note is None and code == -signal.SIGKILL:
        note = (f"{label} was SIGKILLed by the OS (most likely out of memory; peak RSS seen "
                f"{peak:.1f} GB) — not a timeout")
    return {"code": code, "stdout": "".join(out_lines), "stderr": "".join(err_lines), "note": note,
            "duration_s": round(time.time() - t0, 1), "peak_gb": round(peak, 2)}


# ----------------------------------------------------------------------------- scoring
def run_selftest() -> dict:
    if not (ROOT / "harness" / "selftest.py").exists():
        return {"ok": False, "tail": "harness/selftest.py does not exist yet", "passed": 0, "total": 0,
                "duration_s": 0.0, "note": None, "peak_gb": 0.0}
    r = run_guarded([str(VENV_PY), "-u", "harness/selftest.py"], CONFIG["SCORE_TIMEOUT_S"], "selftest")
    passed = sum(1 for ln in r["stdout"].splitlines() if ln.startswith("[PASS]"))
    failed = sum(1 for ln in r["stdout"].splitlines() if ln.startswith("[FAIL]"))
    tail = clean_tail(r["stdout"] + "\n" + r["stderr"], 3000)
    if r["note"]:
        tail += f"\n\nDRIVER: {r['note']}"
    return {"ok": r["code"] == 0, "tail": tail, "passed": passed, "total": passed + failed,
            "duration_s": r["duration_s"], "note": r["note"], "peak_gb": r["peak_gb"]}


def contract_valid(score: dict | None) -> tuple[bool, str]:
    if not isinstance(score, dict):
        return False, "score.json missing or not an object"
    for key in ("milestone", "pass", "progress", "checks"):
        if key not in score:
            return False, f"score.json missing key '{key}'"
    p = score["progress"]
    if not isinstance(p, (int, float)) or not (0.0 <= float(p) <= 1.0):
        return False, "progress must be a number in [0,1]"
    if not isinstance(score["checks"], list):
        return False, "checks must be a list"
    if not score["pass"] and not isinstance(score.get("first_failure"), dict):
        return False, "failing score must carry first_failure"
    return True, "ok"


def run_scorer(milestone: str, iteration: int, out_name: str = "score.json") -> tuple[int, dict | None, str]:
    """Run the frozen scorer. Returns (exit, score dict or None, log tail)."""
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / out_name
    suffix = "" if out_name == "score.json" else f"-{milestone}"
    if out.exists():
        out.unlink()
    if not (ROOT / "harness" / "score.py").exists():
        return 2, None, "harness/score.py does not exist"
    r = run_guarded([str(VENV_PY), "-u", "harness/score.py", "--milestone", milestone, "--out", str(out), "--keep"],
                    CONFIG["SCORE_TIMEOUT_S"], f"scorer {milestone}")
    code = r["code"]
    tail = clean_tail(r["stdout"] + "\n" + r["stderr"], 4000)
    if r["note"]:
        code, tail = 2, tail + f"\n\nDRIVER: {r['note']}"
    score = None
    if out.exists():
        try:
            score = json.loads(out.read_text())
        except json.JSONDecodeError as e:
            tail += f"\nscore.json is not valid JSON: {e}"
    if score is None:
        score = {"milestone": milestone, "pass": False, "progress": 0.0, "stage_reached": "scorer",
                 "first_failure": {"check": "scorer_error", "value": code,
                                   "hint": "the frozen scorer failed to produce score.json — "
                                           "most likely rebuild.py or its imports are broken; see log tail"},
                 "checks": [], "scorer_log_tail": tail}
        out.write_text(json.dumps(score, indent=2))
    (LOGS_DIR / f"iter-{iteration:04d}.score{suffix}.log").write_text(tail)
    shutil.copy(out, LOGS_DIR / f"iter-{iteration:04d}.score{suffix}.json")
    return code, score, tail


def check_regressions(upto: str, iteration: int) -> list[tuple[str, str]]:
    """Re-score every milestone before `upto` on the current HEAD. Returns [(milestone, why)] for
    the ones that no longer pass — a later milestone may not be bought by breaking an earlier one."""
    failing: list[tuple[str, str]] = []
    for m in MILESTONES[1:MILESTONES.index(upto)]:
        if m in ("HANDOFF", "DONE"):
            continue
        code, score, _ = run_scorer(m, iteration, out_name=f"score.{m}.json")
        if score and score.get("pass"):
            log(f"regression check: {m} still passes")
            continue
        ff = (score or {}).get("first_failure") or {}
        why = f"{ff.get('check')}={ff.get('value')} — {str(ff.get('hint', ''))[:160]}"
        log(f"REGRESSION: {m} no longer passes on HEAD: {why}")
        failing.append((m, why))
    return failing


def demote(st: dict, failing: list[tuple[str, str]], iteration: int, context: str) -> None:
    """Send the loop back to the lowest milestone that fails on HEAD."""
    target, why = failing[0]
    st["milestone"] = target
    st.update(best_progress=0.0, stall=0, escalated=False)
    set_last_eval(st, iteration, "regression", False,
                  f"{context}: {target} no longer passes ({why}); milestone set back to {target}",
                  "\n".join(f"{m}: {w}" for m, w in failing))
    log(f"MILESTONE DEMOTED → {target}  ({context}; earlier milestones must keep passing)")


# ----------------------------------------------------------------------------- modes
def choose_mode(st: dict) -> dict:
    ms, stall = st["milestone"], st["stall"]
    if ms == "M0" and st["m0_phase"] == "review":
        return dict(mode="review-harness", model=CONFIG["MODEL_REVIEW"], effort=CONFIG["EFFORT_REVIEW"],
                    budget=CONFIG["BUDGET_USD_ESCALATE"], timeout=CONFIG["REVIEW_TIMEOUT_S"])
    if ms == "HANDOFF":
        return dict(mode="handoff", model=CONFIG["MODEL_ESCALATE"], effort=CONFIG["EFFORT_DEFAULT"],
                    budget=CONFIG["BUDGET_USD_DEFAULT"], timeout=CONFIG["ITER_TIMEOUT_S"])
    if stall >= CONFIG["STALL_ESCALATE"]:
        st["escalated"] = True
    if st["escalated"]:
        t_at = CONFIG["STALL_ESCALATE"] + CONFIG["STALL_TOURNAMENT"]
        if ms != "M0" and stall >= t_at and (stall - t_at) % 3 == 0:
            return dict(mode="tournament", model=CONFIG["MODEL_ESCALATE"], effort=CONFIG["EFFORT_TOURNAMENT"],
                        budget=CONFIG["BUDGET_USD_TOURNAMENT"], timeout=CONFIG["TOURNAMENT_TIMEOUT_S"])
        return dict(mode="escalated", model=CONFIG["MODEL_ESCALATE"], effort=CONFIG["EFFORT_ESCALATE"],
                    budget=CONFIG["BUDGET_USD_ESCALATE"], timeout=CONFIG["ESCALATED_TIMEOUT_S"])
    return dict(mode="normal", model=CONFIG["MODEL_DEFAULT"], effort=CONFIG["EFFORT_DEFAULT"],
                budget=CONFIG["BUDGET_USD_DEFAULT"], timeout=CONFIG["ITER_TIMEOUT_S"])


def latest_verdict_line() -> str:
    f = OUT_DIR / "score.json"
    if not f.exists():
        return "no driver verdict yet (out/score.json absent)"
    try:
        s = json.loads(f.read_text())
    except json.JSONDecodeError:
        return "out/score.json is unreadable"
    if s.get("pass"):
        return f"{s.get('milestone')} PASSED (progress 1.0)"
    ff = s.get("first_failure") or {}
    loc = ff.get("location") or {}
    where = f" at z={loc.get('z_mm')} {loc.get('region', '')}".rstrip() if loc else ""
    return (f"{s.get('milestone')} progress={s.get('progress')}; stage={s.get('stage_reached')}; "
            f"first failure: {ff.get('check')}={ff.get('value')} (threshold {ff.get('threshold')}){where}"
            f" — hint: {ff.get('hint')}")


def compose_prompt(st: dict, mode: dict, iteration: int) -> str:
    frozen = INFRA_PATHS + (["harness/"] if tag_exists(HARNESS_TAG) else [])
    start = now()
    deadline = start + dt.timedelta(seconds=mode["timeout"])
    commit_by = start + dt.timedelta(seconds=mode["timeout"] * CONFIG["COMMIT_BY_PCT"] / 100.0)
    selftest_note = (f"; a full harness/selftest.py run last took {fmt_min(st['last_selftest_s'])}"
                     if st.get("last_selftest_s") else "")
    header = [
        "# LOOP HEADER (generated by the driver)",
        f"- iteration: {iteration}    - time now: {hhmm(start)} ({ts()})",
        f"- milestone: {st['milestone']}" + (f" (phase: {st['m0_phase']})" if st['milestone'] == 'M0' else ""),
        f"- mode: {mode['mode']}    - model: {mode['model']}/{mode['effort']}",
        f"- TIME BUDGET: {fmt_min(mode['timeout'])} — the driver kills this session at {hhmm(deadline)} "
        f"and auto-commits whatever is on disk. Have working state committed and PROGRESS.md updated "
        f"by {hhmm(commit_by)}{selftest_note}.",
        f"- consecutive stalls on this milestone: {st['stall']} (best progress {st['best_progress']:.3f})",
        f"- latest driver verdict: {latest_verdict_line()}",
        f"- frozen paths (do not edit): {', '.join(frozen)}",
        f"- venv python: {VENV_PY}",
        f"- the driver runs selftest/scorer with a {CONFIG['MEM_LIMIT_GB']:g} GB memory cap and a "
        f"{fmt_min(CONFIG['SCORE_TIMEOUT_S'])} wall-clock cap; exceeding either fails the evaluation.",
    ]
    le = st.get("last_eval")
    if le:
        header.append(f"- last driver evaluation (iteration {le.get('iteration')}, {le.get('kind')}): "
                      f"{'PASSED' if le.get('ok') else 'FAILED'} — {le.get('summary', '')}")
    header.append("")
    parts = ["\n".join(header)]
    if le and le.get("tail"):
        parts.append("## Last driver evaluation — output tail (read this before re-running anything)\n"
                     "```\n" + le["tail"].strip() + "\n```")
    parts.append((ROOT / "PROMPT.md").read_text())
    frag = {
        "review-harness": "driver/prompt_review_harness.md",
        "escalated": "driver/prompt_escalated.md",
        "tournament": "driver/prompt_tournament.md",
        "handoff": "driver/prompt_handoff.md",
    }.get(mode["mode"])
    if st["milestone"] == "M0" and mode["mode"] != "review-harness":
        frag = "driver/prompt_m0.md"
    if frag:
        parts.append((ROOT / frag).read_text())
    return "\n\n".join(parts)


# ----------------------------------------------------------------------------- evaluation
def advance(st: dict, note: str) -> None:
    i = MILESTONES.index(st["milestone"])
    st["milestone"] = MILESTONES[i + 1]
    st.update(best_progress=0.0, stall=0, escalated=False)
    log(f"MILESTONE ADVANCE → {st['milestone']}  ({note})")


def update_stall(st: dict, progress: float) -> None:
    if progress >= st["best_progress"] + CONFIG["PROGRESS_MIN_DELTA"]:
        log(f"progress improved {st['best_progress']:.4f} → {progress:.4f}")
        st["best_progress"], st["stall"] = progress, 0
    else:
        st["stall"] += 1
        if progress > st["best_progress"]:
            st["best_progress"] = progress   # keep the high-water mark, but creeping counts as a stall
        log(f"no meaningful improvement (progress {progress:.4f}, best {st['best_progress']:.4f}); stall={st['stall']}")


HARNESS_FILES = ["milestones.py", "generators.py", "metrics.py", "meshcheck.py", "score.py", "selftest.py"]


def m0_modules_present() -> int:
    return sum(1 for f in HARNESS_FILES
               if (ROOT / "harness" / f).exists() and (ROOT / "harness" / f).stat().st_size > 200)


def m0_progress(passed: int, total: int) -> float:
    """Monotone proxy for harness completeness while selftest is not yet green: 0.6 for the six
    modules existing, plus 0.35 × the fraction of selftest checks that pass. A selftest that dies
    mid-run therefore reads as a regression (fewer checks seen), which is correct."""
    p = 0.6 * m0_modules_present() / len(HARNESS_FILES)
    if total > 0:
        p += 0.35 * passed / total
    return round(p, 3)


def set_last_eval(st: dict, iteration: int, kind: str, ok: bool, summary: str, tail: str) -> None:
    st["last_eval"] = {"iteration": iteration, "kind": kind, "ok": ok, "summary": summary,
                       "tail": tail[-900:], "ts": ts()}


def evaluate(st: dict, iteration: int, mode: str) -> None:
    ms = st["milestone"]
    if ms == "M0":
        r = run_selftest()
        (LOGS_DIR / f"iter-{iteration:04d}.selftest.log").write_text(r["tail"])
        summary = f"{r['passed']}/{r['total']} checks passed in {fmt_min(r['duration_s'])}, peak RSS {r['peak_gb']} GB"
        if r["note"]:
            summary += f"; {r['note']}"
        set_last_eval(st, iteration, "selftest", r["ok"], summary, r["tail"])
        if not r["ok"]:
            built = m0_progress(r["passed"], r["total"])
            log(f"M0: selftest not passing — {summary} (modules {m0_modules_present()}/{len(HARNESS_FILES)})")
            if st["m0_phase"] == "review":
                # the review pass legitimately raised the bar; that is not a stall of the builder
                st["m0_phase"] = "build"
                st["best_progress"], st["stall"] = built, 0
                log(f"M0: review pass moved the bar → back to build at progress {built:.3f}; stall counter reset")
            else:
                update_stall(st, built)
            return
        st["last_selftest_s"] = r["duration_s"]
        code, score, tail = run_scorer("M1", iteration)
        valid, why = contract_valid(score)
        if code == 2 or not valid:
            log(f"M0: selftest ok but scorer contract invalid (exit {code}: {why})")
            set_last_eval(st, iteration, "scorer-contract", False, f"exit {code}: {why}", tail)
            update_stall(st, 0.8)
            return
        if st["m0_phase"] == "build":
            st["m0_phase"] = "review"
            update_stall(st, 0.95)
            log("M0: harness complete → next iteration is the review-harness pass")
            return
        # review pass done and everything still green → freeze
        git("tag", "-f", HARNESS_TAG)
        log(f"M0: harness FROZEN at {git_head()[:10]} (tag {HARNESS_TAG})")
        advance(st, "harness frozen")
        return
    if ms == "HANDOFF":
        h = ROOT / "HANDOFF.md"
        ok = h.exists() and len(h.read_text()) > 1500 and all(f"M{i}" in h.read_text() for i in range(1, 6))
        failing = check_regressions("HANDOFF", iteration)
        if failing:
            demote(st, failing, iteration, "at HANDOFF")
            return
        set_last_eval(st, iteration, "handoff", ok, "HANDOFF.md present and complete" if ok else
                      "HANDOFF.md missing, short, or not covering M1–M5", "")
        if ok:
            advance(st, "HANDOFF.md written")
        else:
            update_stall(st, 0.0)
        return
    code, score, tail = run_scorer(ms, iteration)
    progress = float(score.get("progress", 0.0)) if score else 0.0
    set_last_eval(st, iteration, f"scorer {ms}", bool(score and score.get("pass")), latest_verdict_line(), tail)
    if score and score.get("pass"):
        failing = check_regressions(ms, iteration)
        if failing:
            demote(st, failing, iteration, f"{ms} passes")
            return
        keep_artifacts(score, ms)
        advance(st, f"scorer pass at iteration {iteration}")
        return
    update_stall(st, progress)


def keep_artifacts(score: dict, ms: str) -> list[str]:
    """Copy a passing milestone's artifacts to logs/<ms>-final.*; tolerate missing/null paths."""
    kept: list[str] = []
    arts = score.get("artifacts")
    if not isinstance(arts, dict):
        return kept
    for key, path in arts.items():
        if not isinstance(path, str) or not path:
            continue
        src = Path(path)
        if not src.is_absolute():
            src = ROOT / src
        if not src.exists():
            continue
        dest = LOGS_DIR / f"{ms}-final-{key}{src.suffix}"
        try:
            shutil.copy(src, dest)
            kept.append(os.path.relpath(dest, ROOT))
        except OSError as e:
            log(f"note: could not keep artifact {key} ({src}): {e}")
    if kept:
        log(f"kept {ms} artifacts: {', '.join(kept)}")
    return kept


# ----------------------------------------------------------------------------- status / dashboard
def iteration_rows(st: dict) -> list[dict]:
    return [h for h in st["history"] if "iteration" in h and "mode" in h]


def spend_summary(st: dict) -> str:
    est = sum(1 for u in st["usage"] if u.get("estimated"))
    total = sum(u["cost_usd"] for u in st["usage"])
    s = f"last 5h ${window_cost(st, 5):.2f} / gate ${window_gate_usd():.2f} · total ${total:.2f}"
    if est:
        s += f" ({est} iteration{'s' if est != 1 else ''} estimated at the budget cap)"
    return s


def gate_eta(st: dict) -> str:
    rows = iteration_rows(st)
    if not rows:
        return ""
    avg_cost = sum(r.get("cost_usd", 0.0) for r in rows[-8:]) / len(rows[-8:])
    room = window_gate_usd() - window_cost(st, 5)
    if avg_cost <= 0:
        return ""
    n = int(room / avg_cost)
    return f"≈{n} more iteration{'s' if n != 1 else ''} before the usage gate pauses" if n < 12 else "no gate pause expected soon"


def status_lines(st: dict) -> list[str]:
    rows = iteration_rows(st)
    lines = [f"stl-rebuilder loop — {ts()}",
             f"  milestone {st['milestone']}" + (f" (phase {st['m0_phase']})" if st['milestone'] == 'M0' else "")
             + f" · iteration {st['iteration']} · stall {st['stall']} · best progress {st['best_progress']:.3f}"
             + (" · escalated" if st["escalated"] else "")]
    pid = running_driver_pid()
    cur = read_current()
    if pid and cur:
        started = dt.datetime.fromisoformat(cur["started"])
        el = (now() - started).total_seconds()
        if cur.get("phase") == "agent":
            lines.append(f"  driver RUNNING (pid {pid}) — iteration {cur['iteration']} {cur['mode']}/{cur['model']}: "
                         f"{fmt_min(el)} of {fmt_min(cur['timeout_s'])} ({el / cur['timeout_s']:.0%}), "
                         f"deadline {hhmm(started + dt.timedelta(seconds=cur['timeout_s']))}")
        else:
            lines.append(f"  driver RUNNING (pid {pid}) — iteration {cur['iteration']}: agent done, "
                         f"evaluating ({fmt_min(el)} since the iteration started)")
    elif pid:
        lines.append(f"  driver RUNNING (pid {pid}) — between iterations (preflight, gate pause, or sleeping)")
    else:
        last = next((h for h in reversed(st["history"]) if h.get("event") == "interrupted"), None)
        why = st.get("stopped_reason") or (f"interrupted {last['ts'][11:16]}" if last else "not started / exited")
        lines.append(f"  driver NOT running — {why}")
    if st.get("pending_eval"):
        lines.append(f"  pending: iteration {st['pending_eval']['iteration']} finished but was not evaluated — "
                     f"the next ./loop.sh evaluates it first")
    if rows:
        by_mode: dict[str, list[dict]] = {}
        for r in rows:
            by_mode.setdefault(r["mode"], []).append(r)
        timed = [r["duration_s"] for r in rows if r.get("duration_s")]
        avg_min = f"avg {sum(timed) / len(timed) / 60:.0f} min · " if timed else ""
        avg_cost = sum(r.get("cost_usd", 0.0) for r in rows) / len(rows)
        per_mode = " · ".join(f"{m} {len(v)}× ${sum(r.get('cost_usd', 0) for r in v) / len(v):.2f}"
                              for m, v in by_mode.items())
        lines.append(f"  iterations: {len(rows)} recorded · {avg_min}avg ${avg_cost:.2f} ({per_mode})")
    lines.append(f"  spend: {spend_summary(st)}" + (f" · {gate_eta(st)}" if gate_eta(st) else ""))
    le = st.get("last_eval")
    if le:
        lines.append(f"  last evaluation: iteration {le['iteration']} {le['kind']} "
                     f"{'PASSED' if le['ok'] else 'FAILED'} — {le['summary']}")
    lines.append(f"  latest verdict: {latest_verdict_line()}")
    if st.get("last_selftest_s"):
        lines.append(f"  a full selftest last took {fmt_min(st['last_selftest_s'])}")
    if not pid:
        mode = choose_mode(dict(st))
        lines.append(f"  next iteration would be: {mode['mode']} on {mode['model']}/{mode['effort']} "
                     f"(budget ${mode['budget']:.2f}, {fmt_min(mode['timeout'])})")
    return lines


def write_dashboard(st: dict, note: str | None = None) -> None:
    """state/STATUS.md — refreshed after every iteration and on every stop."""
    rows = iteration_rows(st)[-12:]
    lines = [f"# Loop status — {ts()}", ""]
    if note:
        lines += [f"**{note}**", ""]
    lines += ["```", *status_lines(st), "```", "",
              "## Last iterations", "",
              "| iter | mode | model | min | cost | ok | after |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        after = f"{r.get('milestone_after')} stall={r.get('stall')} best={r.get('best_progress', 0):.3f}"
        cost = f"${r.get('cost_usd', 0):.2f}" + ("~" if r.get("estimated") else "")
        lines.append(f"| {r['iteration']} | {r['mode']} | {r.get('model', '')} | {r.get('duration_s', 0) / 60:.0f} "
                     f"| {cost} | {'yes' if r.get('ok') else 'no'} | {after} |")
    lines += ["", "Commands: `./loop.sh` resume · `python3 loop.py --status` · `--stop` (finish the iteration, "
              "then exit) · `--kill` (now, with a checkpoint commit) · `--reset-stall`.", ""]
    STATE_DIR.mkdir(exist_ok=True)
    DASHBOARD_FILE.write_text("\n".join(lines))


def write_status(st: dict, reason: str) -> None:
    st["stopped_reason"] = reason
    save_state(st)
    write_dashboard(st, note=f"Stopped: {reason}")
    log(f"STOP: {reason} — see state/STATUS.md")


# ----------------------------------------------------------------------------- preflight
def preflight(st: dict, recheck: bool) -> None:
    for d in (STATE_DIR, LOGS_DIR, OUT_DIR):
        d.mkdir(exist_ok=True)
    if not shutil.which("claude"):
        sys.exit("preflight: `claude` CLI not on PATH")
    helptext = sh(["claude", "--help"]).stdout
    missing = [f for f in REQUIRED_FLAGS if f not in helptext]
    if missing:
        sys.exit(f"preflight: installed claude CLI lacks flags {missing}; run `claude update`")
    log(f"preflight: claude {sh(['claude', '--version']).stdout.strip()}")
    # leftovers from an earlier driver that died without cleaning up
    for pid in loop_agent_pids():
        n = kill_tree(pid)
        log(f"preflight: killed an orphaned loop agent (pid {pid}, {n} processes) left by an earlier run")
    if not VENV_PY.exists():
        sys.exit("preflight: .venv missing — see README (Environment)")
    r = sh([str(VENV_PY), "-c", "import build123d, trimesh, gmsh, OCP, shapely, scipy; print('venv ok')"])
    if r.returncode != 0:
        sys.exit(f"preflight: venv imports failed:\n{r.stderr[-2000:]}")
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        sys.exit("preflight: not a git repository")
    top = git("rev-parse", "--show-toplevel").stdout.strip()
    if Path(top).resolve() != ROOT:
        sys.exit(f"preflight: git toplevel is {top}, expected {ROOT}")
    if not tag_exists(INFRA_TAG):
        git("tag", INFRA_TAG)
        log(f"preflight: created tag {INFRA_TAG} at {git_head()[:10]}")
    # model smoke test: confirm which model actually serves each alias
    models = {CONFIG["MODEL_DEFAULT"], CONFIG["MODEL_ESCALATE"], CONFIG["MODEL_REVIEW"]}
    for m in sorted(models):
        if not recheck and m in st["verified_models"]:
            continue
        args = ["claude", "-p", "Reply with exactly: OK", "--model", m, "--output-format", "json",
                "--max-budget-usd", "0.30", "--tools", "", "--disable-slash-commands",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "project"]
        try:
            r = subprocess.run(args, cwd=ROOT, env=claude_env(), text=True, capture_output=True, timeout=180)
        except subprocess.TimeoutExpired:
            sys.exit(f"preflight: smoke test for --model {m} timed out")
        data = parse_claude_json(r.stdout) or {}
        served = list((data.get("modelUsage") or data.get("model_usage") or {}).keys())
        blob = r.stdout[-2000:] + r.stderr[-2000:]
        if r.returncode != 0 or data.get("is_error"):
            if RATE_LIMIT_RE.search(blob):
                sys.exit(f"preflight: usage/rate limit hit during smoke test for {m}:\n{blob}")
            sys.exit(f"preflight: `claude -p --model {m}` failed (exit {r.returncode}):\n{blob}\n"
                     "If this is an auth error, run `claude` interactively once to log in, or set LOOP_USE_API_KEY=1.")
        st["verified_models"][m] = {"served": served, "ts": ts(), "cost_usd": data.get("total_cost_usd")}
        log(f"preflight: --model {m} → served by {served or 'unknown'}")
        want = m if m.startswith("claude-") else ("opus" if "opus" in m else ("sonnet" if "sonnet" in m else m))
        if served and not any(want in s for s in served):
            log(f"WARNING: requested {m} but served by {served}; alias may be stale — use a full model ID "
                f"or run `claude update`")
    save_state(st)


# ----------------------------------------------------------------------------- main loop
def finish_pending(st: dict) -> None:
    """Evaluate an iteration whose agent has finished (normally right away; after a restart if
    the driver was stopped mid-evaluation)."""
    p = st["pending_eval"]
    n = p["iteration"]
    set_current({"iteration": n, "mode": p["mode"], "model": p.get("model", ""), "started": p.get("started", now().isoformat()),
                 "timeout_s": p.get("timeout_s", 0), "phase": "evaluating", "pid": os.getpid()})
    restore_frozen()
    auto_commit(n)
    head_after = git_head()
    evaluate(st, n, p["mode"])
    st["history"].append({"ts": ts(), "iteration": n, "milestone_before": p.get("milestone_before"), "mode": p["mode"],
                          "model": p.get("model"), "cost_usd": p.get("cost_usd", 0.0), "estimated": p.get("estimated", False),
                          "ok": p.get("ok"), "duration_s": p.get("duration_s", 0.0), "turns": p.get("turns"),
                          "head_before": p.get("head_before"), "head_after": head_after[:10],
                          "milestone_after": st["milestone"], "stall": st["stall"],
                          "best_progress": st["best_progress"]})
    st["history"] = st["history"][-500:]
    st["pending_eval"] = None
    save_state(st)
    set_current(None)
    write_dashboard(st)


def iterate(st: dict, once: bool) -> int:
    while True:
        if stop_requested():
            STOP_FILE.unlink()
            write_status(st, "stopped on request (state/STOP) between iterations — everything is committed")
            return 0
        if st.get("pending_eval"):
            log(f"resuming: iteration {st['pending_eval']['iteration']}'s agent had finished but its evaluation "
                f"did not complete — evaluating it now")
            finish_pending(st)
            continue
        if st["milestone"] == "DONE":
            write_status(st, "all milestones complete and HANDOFF.md written — nothing to do")
            return 0
        if st["iteration"] >= CONFIG["MAX_ITERATIONS"]:
            write_status(st, f"MAX_ITERATIONS={CONFIG['MAX_ITERATIONS']} reached")
            return 2
        if st["stall"] >= CONFIG["STALL_MAX"]:
            write_status(st, f"no progress for STALL_MAX={CONFIG['STALL_MAX']} iterations on {st['milestone']}")
            return 2
        restore_frozen()
        usage_gate(st)
        if stop_requested():
            continue
        mode = choose_mode(st)
        n = st["iteration"] + 1
        prompt = compose_prompt(st, mode, n)
        (LOGS_DIR / f"iter-{n:04d}.prompt.md").write_text(prompt)
        head_before = git_head()
        started = now().isoformat()
        set_current({"iteration": n, "mode": mode["mode"], "model": mode["model"], "started": started,
                     "timeout_s": mode["timeout"], "phase": "agent", "pid": os.getpid()})
        fallback = CONFIG["FALLBACK_MODEL"] if mode["mode"] in ("escalated", "tournament") else ""
        res = run_claude(prompt, mode["model"], mode["effort"], mode["budget"], mode["timeout"], fallback=fallback,
                         tag=f"iter {n} {mode['mode']}/{mode['model'].replace('claude-', '')} {st['milestone']}",
                         hb_extra=lambda: f"window ${window_cost(st, 5):.2f}/${window_gate_usd():.2f}")
        (LOGS_DIR / f"iter-{n:04d}.claude.json").write_text(
            json.dumps(res["raw"], indent=2) if res["raw"] else
            json.dumps({k: v for k, v in res.items() if k != "raw"}, indent=2))
        if res["cost_usd"]:
            st["usage"].append({"ts": now().isoformat(), "cost_usd": res["cost_usd"], "model": mode["model"],
                                "served": res["served_models"], "iteration": n, "estimated": res["estimated_cost"]})
        if res["rate_limited"]:
            delay = res["reset_delay"] or CONFIG["RATE_LIMIT_SLEEP_S"]
            delay = min(delay, CONFIG["RATE_LIMIT_SLEEP_MAX_S"])
            log(f"RATE/USAGE LIMIT hit mid-iteration; sleeping {delay // 60} min then retrying "
                f"(partial work stays in the working tree; not counted as an iteration)")
            st["history"].append({"ts": ts(), "event": "rate_limit_pause", "seconds": delay})
            save_state(st)
            set_current(None)
            if not sleep_interruptible(delay, "waiting for the usage limit to reset"):
                continue
            continue
        st["iteration"] = n
        cost_s = f"${res['cost_usd']:.2f}" + (" (estimated: CLI died before reporting)" if res["estimated_cost"] else "")
        log(f"claude done: ok={res['ok']} exit={res['exit']} timed_out={res['timed_out']} "
            f"turns={res['num_turns']} tools={res['tool_calls']} cost={cost_s} {fmt_min(res['duration_s'])} "
            f"served={res['served_models']}" + (f" last tool: {res['last_tool']}" if not res["ok"] and res["last_tool"] else ""))
        if res["result_tail"]:
            log("agent summary: " + res["result_tail"].replace("\n", " | ")[-600:])
        st["pending_eval"] = {"iteration": n, "mode": mode["mode"], "model": mode["model"], "started": started,
                              "timeout_s": mode["timeout"], "milestone_before": st["milestone"],
                              "cost_usd": res["cost_usd"], "estimated": res["estimated_cost"], "ok": res["ok"],
                              "duration_s": res["duration_s"], "turns": res["num_turns"], "head_before": head_before[:10]}
        save_state(st)
        finish_pending(st)
        if once:
            log("--once: stopping after one iteration")
            return 0
        sleep_interruptible(CONFIG["SLEEP_BETWEEN_ITER_S"], "between iterations")


def save_then_stop(st: dict, why: str) -> None:
    """Kill anything the driver spawned, checkpoint-commit the agent's work, save state."""
    log(f"{why}: stopping — killing children, checkpointing work, saving state")
    for pid in (CURRENT_AGENT_PID, CURRENT_CHILD_PID):
        if pid:
            kill_tree(pid)
    cur = read_current()
    n = cur["iteration"] if cur else st["iteration"]
    auto_commit(n, "interrupted — driver checkpoint of the agent's uncommitted work")
    st["history"].append({"ts": ts(), "event": "interrupted", "iteration": n, "why": why})
    save_state(st)
    set_current(None)
    write_dashboard(st, note=f"Stopped: {why} at {hhmm(now())}. Resume with ./loop.sh")
    log("stopped; state saved — rerun ./loop.sh to resume exactly where it left off")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--json", action="store_true", help="with --status: dump the raw state")
    ap.add_argument("--stop", action="store_true", help="finish + evaluate the in-flight iteration, then exit")
    ap.add_argument("--kill", action="store_true", help="stop now: kill the agent, checkpoint-commit, save state")
    ap.add_argument("--recheck-models", action="store_true")
    ap.add_argument("--reset-stall", action="store_true")
    ap.add_argument("--check-regressions", action="store_true",
                    help="re-score every milestone below the current one on HEAD; demote the state if one fails")
    ap.add_argument("--max-iterations", type=int)
    a = ap.parse_args()
    if a.max_iterations:
        CONFIG["MAX_ITERATIONS"] = a.max_iterations
    st = load_state()
    if a.status:
        if a.json:
            print(json.dumps({k: v for k, v in st.items() if k not in ("usage", "history")}, indent=2))
        print("\n".join(status_lines(st)))
        return 0
    if a.stop:
        pid = running_driver_pid()
        if not pid:
            print("no driver is running (nothing to stop)")
            return 1
        STATE_DIR.mkdir(exist_ok=True)
        STOP_FILE.touch()
        cur = read_current()
        where = (f"iteration {cur['iteration']} ({cur['phase']})" if cur else "between iterations")
        print(f"stop requested: driver pid {pid} is in {where}; it will finish and evaluate that iteration, "
              f"then exit. Watch logs/loop.log. (Use --kill to stop immediately.)")
        return 0
    if a.kill:
        pid = running_driver_pid()
        if not pid:
            print("no driver is running (nothing to kill)")
            return 1
        os.kill(pid, signal.SIGTERM)
        print(f"sent SIGTERM to driver pid {pid}: it kills the agent, checkpoint-commits its work, saves state "
              f"and exits. Watch logs/loop.log for 'stopped; state saved'.")
        return 0
    if a.check_regressions:
        if running_driver_pid():
            print("stop the driver first (--stop/--kill); the sweep must not race an evaluation")
            return 1
        failing = check_regressions(st["milestone"], st["iteration"])
        if failing:
            demote(st, failing, st["iteration"], "manual regression sweep")
        else:
            print(f"all milestones below {st['milestone']} pass on HEAD")
        save_state(st)
        write_dashboard(st)
        return 0
    if a.reset_stall:
        st.update(stall=0, escalated=False, stopped_reason=None)
        save_state(st)
        print("stall counter reset (and escalation cleared)")
        return 0
    if a.dry_run:
        print("CONFIG:", json.dumps(CONFIG, indent=2))
        mode = choose_mode(st)
        print("MODE:", mode)
        print("ARGS:", " ".join(claude_args(mode["model"], mode["effort"], mode["budget"])))
        print("---- PROMPT ----")
        print(compose_prompt(st, mode, st["iteration"] + 1))
        return 0

    def _on_sigterm(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, _on_sigterm)
    acquire_lock()
    st["stopped_reason"] = None
    try:
        preflight(st, a.recheck_models)
        if STOP_FILE.exists():
            STOP_FILE.unlink()  # a stale request from a previous run must not stop this one
        return iterate(st, a.once)
    except KeyboardInterrupt as e:
        save_then_stop(st, "interrupted (--kill)" if "signal" in str(e) else "interrupted (Ctrl-C)")
        return 130
    except Exception as e:  # a driver bug must never stop the loop silently
        import traceback
        tb = traceback.format_exc()
        LOGS_DIR.mkdir(exist_ok=True)
        with (LOGS_DIR / "driver-crash.log").open("a") as fh:
            fh.write(f"\n[{ts()}] driver crashed\n{tb}\n")
        log(f"DRIVER CRASH: {type(e).__name__}: {e} — traceback in logs/driver-crash.log; "
            f"state saved, loop.sh restarts the driver (state is resumable)")
        for pid in (CURRENT_AGENT_PID, CURRENT_CHILD_PID):
            if pid:
                kill_tree(pid)
        auto_commit(st["iteration"] + 1, "driver crash — checkpoint of any uncommitted agent work")
        st["history"].append({"ts": ts(), "event": "driver_crash", "error": f"{type(e).__name__}: {e}"[:300]})
        save_state(st)
        set_current(None)
        write_dashboard(st, note=f"Driver crashed at {hhmm(now())}: {type(e).__name__}: {e} — see logs/driver-crash.log")
        return 3
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
