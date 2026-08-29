#!/usr/bin/env python3
"""
loop.py — driver for the self-annealing STL→solid coding loop.

Each iteration: restore frozen files → usage gate → pick mode/model → run a fresh headless
`claude -p` → restore frozen files again → auto-commit → score with the frozen harness →
update state → repeat. The driver, never the agent, decides when a milestone passes.

Standard library only. State lives in state/loop_state.json; logs in logs/.
Usage:
  python3 loop.py            # run until done / guardrail
  python3 loop.py --once     # exactly one iteration (supervised runs)
  python3 loop.py --dry-run  # show config, compose the prompt, call nothing
  python3 loop.py --status   # print state summary
  python3 loop.py --recheck-models
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_DIR, LOGS_DIR, OUT_DIR = ROOT / "state", ROOT / "logs", ROOT / "out"
STATE_FILE = STATE_DIR / "loop_state.json"
LOCK_FILE = STATE_DIR / "loop.lock"
VENV_PY = ROOT / ".venv" / "bin" / "python"

# ----------------------------------------------------------------------------- config
# Every value can be overridden with an env var of the same name prefixed LOOP_, e.g.
#   LOOP_WINDOW_BUDGET_USD=60 LOOP_MODEL_DEFAULT=claude-opus-5 ./loop.sh
CONFIG = {
    # models / effort
    "MODEL_DEFAULT": "sonnet",
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
    # caps
    "MAX_ITERATIONS": 300,
    "ITER_TIMEOUT_S": 2700,        # 45 min per normal iteration
    "TOURNAMENT_TIMEOUT_S": 5400,
    "SCORE_TIMEOUT_S": 1500,
    "BUDGET_USD_DEFAULT": 4.0,     # --max-budget-usd per iteration
    "BUDGET_USD_ESCALATE": 10.0,
    "BUDGET_USD_TOURNAMENT": 20.0,
    # usage pacing (API-equivalent dollars reported by the CLI; tune after observing limits)
    "WINDOW_HOURS": 5.0,
    "WINDOW_BUDGET_USD": 40.0,
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
                  "--strict-mcp-config", "--mcp-config", "--setting-sources",
                  "--disable-slash-commands", "--disallowedTools"]
RATE_LIMIT_RE = re.compile(
    r"rate.?limit|usage limit|limit (has been |was )?reached|out of (usage|credits)|"
    r"too many requests|\b429\b|overloaded|\b529\b|resets? (at|in)|try again (at|in)|"
    r"exceeded your|quota", re.I)


# ----------------------------------------------------------------------------- utils
def now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def ts() -> str:
    return now().strftime("%Y-%m-%dT%H:%M:%S")


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


# ----------------------------------------------------------------------------- state
def default_state() -> dict:
    return {
        "iteration": 0, "milestone": "M0", "m0_phase": "build",
        "best_progress": 0.0, "stall": 0, "escalated": False, "tournaments": 0,
        "usage": [], "history": [], "verified_models": {}, "stopped_reason": None,
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


def acquire_lock() -> None:
    STATE_DIR.mkdir(exist_ok=True)
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            os.kill(pid, 0)
            sys.exit(f"another loop.py (pid {pid}) is running; remove {LOCK_FILE} if stale")
        except (ValueError, ProcessLookupError, PermissionError):
            pass
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


def auto_commit(iteration: int) -> bool:
    if not git("status", "--porcelain").stdout.strip():
        return False
    git("add", "-A")
    r = git("commit", "-qm", f"iter {iteration}: driver auto-commit (agent left uncommitted changes)")
    if r.returncode == 0:
        log("driver auto-committed uncommitted agent changes")
        return True
    return False


# ----------------------------------------------------------------------------- usage pacing
def window_cost(st: dict, hours: float) -> float:
    cutoff = now() - dt.timedelta(hours=hours)
    return sum(u["cost_usd"] for u in st["usage"]
               if dt.datetime.fromisoformat(u["ts"]) > cutoff)


def usage_gate(st: dict) -> None:
    """Sleep (between iterations only) until the rolling-window spend is under the gate."""
    gate = CONFIG["WINDOW_BUDGET_USD"] * CONFIG["WINDOW_GATE_PCT"] / 100.0
    checks = [(CONFIG["WINDOW_HOURS"], gate, "5h-window")]
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
            f"pausing {secs // 60} min until {wake.strftime('%H:%M')} (work is committed; safe to Ctrl-C)")
        st["history"].append({"ts": ts(), "event": "usage_pause", "seconds": secs, "window": name})
        save_state(st)
        time.sleep(secs)


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
    args = ["claude", "-p", "--model", model, "--effort", effort,
            "--output-format", "json", "--max-budget-usd", f"{budget:.2f}",
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


def run_claude(prompt: str, model: str, effort: str, budget: float, timeout: int,
               fallback: str = "", tag: str = "") -> dict:
    args = claude_args(model, effort, budget, fallback)
    log(f"claude start: model={model} effort={effort} budget=${budget:.2f} timeout={timeout}s {tag}")
    t0 = time.time()
    proc = subprocess.Popen(args, cwd=ROOT, env=claude_env(), text=True,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    try:
        out, err = proc.communicate(prompt, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            out, err = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
        timed_out = True
    data = parse_claude_json(out) or {}
    text_blob = "\n".join([out[-4000:], err[-4000:], str(data.get("result", ""))[-2000:]])
    rate_limited = bool(RATE_LIMIT_RE.search(text_blob)) and (proc.returncode != 0 or data.get("is_error"))
    cost = float(data.get("total_cost_usd") or data.get("cost_usd") or 0.0)
    usage = data.get("modelUsage") or data.get("model_usage") or {}
    served = list(usage.keys()) if isinstance(usage, dict) else []
    return {
        "ok": proc.returncode == 0 and not data.get("is_error") and not timed_out,
        "exit": proc.returncode, "timed_out": timed_out, "rate_limited": rate_limited,
        "reset_delay": parse_reset_delay(text_blob) if rate_limited else None,
        "cost_usd": cost, "served_models": served, "session_id": data.get("session_id"),
        "num_turns": data.get("num_turns"), "duration_s": round(time.time() - t0, 1),
        "result_tail": str(data.get("result", ""))[-1500:], "stderr_tail": err[-1500:],
        "raw": data,
    }


# ----------------------------------------------------------------------------- scoring
def run_selftest() -> tuple[bool, str]:
    if not (ROOT / "harness" / "selftest.py").exists():
        return False, "harness/selftest.py does not exist yet"
    try:
        r = sh([str(VENV_PY), "harness/selftest.py"], timeout=CONFIG["SCORE_TIMEOUT_S"])
    except subprocess.TimeoutExpired:
        return False, "selftest timed out"
    tail = (r.stdout + "\n" + r.stderr)[-3000:]
    return r.returncode == 0, tail


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


def run_scorer(milestone: str, iteration: int) -> tuple[int, dict | None, str]:
    """Run the frozen scorer. Returns (exit, score dict or None, log tail)."""
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / "score.json"
    if out.exists():
        out.unlink()
    if not (ROOT / "harness" / "score.py").exists():
        return 2, None, "harness/score.py does not exist"
    try:
        r = sh([str(VENV_PY), "harness/score.py", "--milestone", milestone, "--out", str(out)],
               timeout=CONFIG["SCORE_TIMEOUT_S"])
        code, tail = r.returncode, (r.stdout + "\n" + r.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        code, tail = 2, f"scorer timed out after {CONFIG['SCORE_TIMEOUT_S']}s"
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
    (LOGS_DIR / f"iter-{iteration:04d}.score.log").write_text(tail)
    shutil.copy(out, LOGS_DIR / f"iter-{iteration:04d}.score.json")
    return code, score, tail


# ----------------------------------------------------------------------------- modes
def choose_mode(st: dict) -> dict:
    ms, stall = st["milestone"], st["stall"]
    if ms == "M0" and st["m0_phase"] == "review":
        return dict(mode="review-harness", model=CONFIG["MODEL_REVIEW"], effort=CONFIG["EFFORT_REVIEW"],
                    budget=CONFIG["BUDGET_USD_ESCALATE"], timeout=CONFIG["ITER_TIMEOUT_S"])
    if ms == "HANDOFF":
        return dict(mode="handoff", model=CONFIG["MODEL_ESCALATE"], effort=CONFIG["EFFORT_DEFAULT"],
                    budget=CONFIG["BUDGET_USD_DEFAULT"], timeout=CONFIG["ITER_TIMEOUT_S"])
    if stall >= CONFIG["STALL_ESCALATE"]:
        st["escalated"] = True
    if st["escalated"]:
        t_at = CONFIG["STALL_ESCALATE"] + CONFIG["STALL_TOURNAMENT"]
        if stall >= t_at and (stall - t_at) % 3 == 0:
            return dict(mode="tournament", model=CONFIG["MODEL_ESCALATE"], effort=CONFIG["EFFORT_TOURNAMENT"],
                        budget=CONFIG["BUDGET_USD_TOURNAMENT"], timeout=CONFIG["TOURNAMENT_TIMEOUT_S"])
        return dict(mode="escalated", model=CONFIG["MODEL_ESCALATE"], effort=CONFIG["EFFORT_ESCALATE"],
                    budget=CONFIG["BUDGET_USD_ESCALATE"], timeout=CONFIG["ITER_TIMEOUT_S"])
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
    header = [
        "# LOOP HEADER (generated by the driver)",
        f"- iteration: {iteration}    - time: {ts()}",
        f"- milestone: {st['milestone']}" + (f" (phase: {st['m0_phase']})" if st['milestone'] == 'M0' else ""),
        f"- mode: {mode['mode']}    - model: {mode['model']}/{mode['effort']}",
        f"- consecutive stalls on this milestone: {st['stall']} (best progress {st['best_progress']:.3f})",
        f"- latest driver verdict: {latest_verdict_line()}",
        f"- frozen paths (do not edit): {', '.join(frozen)}",
        f"- venv python: {VENV_PY}",
        "",
    ]
    parts = ["\n".join(header), (ROOT / "PROMPT.md").read_text()]
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
    if progress > st["best_progress"] + 1e-9:
        log(f"progress improved {st['best_progress']:.3f} → {progress:.3f}")
        st["best_progress"], st["stall"] = progress, 0
    else:
        st["stall"] += 1
        log(f"no improvement (progress {progress:.3f}, best {st['best_progress']:.3f}); stall={st['stall']}")


def evaluate(st: dict, iteration: int, mode: str) -> None:
    ms = st["milestone"]
    if ms == "M0":
        ok, tail = run_selftest()
        (LOGS_DIR / f"iter-{iteration:04d}.selftest.log").write_text(tail)
        if not ok:
            log("M0: selftest failing")
            if st["m0_phase"] == "review":
                st["m0_phase"] = "build"
            update_stall(st, 0.0)
            return
        code, score, _ = run_scorer("M1", iteration)
        valid, why = contract_valid(score)
        if code == 2 or not valid:
            log(f"M0: selftest ok but scorer contract invalid (exit {code}: {why})")
            update_stall(st, 0.5)
            return
        if st["m0_phase"] == "build":
            st["m0_phase"] = "review"
            update_stall(st, 0.9)
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
        if ok:
            advance(st, "HANDOFF.md written")
        else:
            update_stall(st, 0.0)
        return
    code, score, _ = run_scorer(ms, iteration)
    progress = float(score.get("progress", 0.0)) if score else 0.0
    if score and score.get("pass"):
        # keep the winning artifacts
        for key, path in (score.get("artifacts") or {}).items():
            p = ROOT / path
            if p.exists():
                shutil.copy(p, LOGS_DIR / f"{ms}-final{p.suffix}")
        advance(st, f"scorer pass at iteration {iteration}")
        return
    update_stall(st, progress)


def write_status(st: dict, reason: str) -> None:
    lines = [f"# Loop status — {ts()}", "", f"**Stopped:** {reason}", "",
             f"- milestone: {st['milestone']}  (M0 phase: {st['m0_phase']})",
             f"- iterations run: {st['iteration']}",
             f"- stall count: {st['stall']}  best progress: {st['best_progress']:.3f}  escalated: {st['escalated']}",
             f"- spend (API-equivalent): last 5h ${window_cost(st, 5):.2f}, total ${sum(u['cost_usd'] for u in st['usage']):.2f}",
             f"- latest verdict: {latest_verdict_line()}", "",
             "Resume with `./loop.sh` (state is on disk). To reset the stall counter after a manual fix: "
             "`python3 loop.py --reset-stall`."]
    (STATE_DIR / "STATUS.md").write_text("\n".join(lines) + "\n")
    st["stopped_reason"] = reason
    save_state(st)
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
        want = "opus" if "opus" in m else ("sonnet" if "sonnet" in m else m)
        if served and not any(want in s for s in served):
            log(f"WARNING: requested {m} but served by {served}; alias may be stale — consider `claude update`")
    save_state(st)


# ----------------------------------------------------------------------------- main loop
def iterate(st: dict, once: bool) -> int:
    while True:
        if st["milestone"] == "DONE":
            log("all milestones complete and HANDOFF.md written — nothing to do")
            return 0
        if st["iteration"] >= CONFIG["MAX_ITERATIONS"]:
            write_status(st, f"MAX_ITERATIONS={CONFIG['MAX_ITERATIONS']} reached")
            return 2
        if st["stall"] >= CONFIG["STALL_MAX"]:
            write_status(st, f"no progress for STALL_MAX={CONFIG['STALL_MAX']} iterations on {st['milestone']}")
            return 2
        restore_frozen()
        usage_gate(st)
        mode = choose_mode(st)
        n = st["iteration"] + 1
        prompt = compose_prompt(st, mode, n)
        (LOGS_DIR / f"iter-{n:04d}.prompt.md").write_text(prompt)
        head_before = git_head()
        fallback = CONFIG["FALLBACK_MODEL"] if mode["mode"] in ("escalated", "tournament") else ""
        res = run_claude(prompt, mode["model"], mode["effort"], mode["budget"], mode["timeout"],
                         fallback=fallback, tag=f"iter={n} mode={mode['mode']} milestone={st['milestone']}")
        (LOGS_DIR / f"iter-{n:04d}.claude.json").write_text(json.dumps(res["raw"], indent=2) if res["raw"] else
                                                            json.dumps({k: v for k, v in res.items() if k != "raw"}, indent=2))
        if res["cost_usd"]:
            st["usage"].append({"ts": now().isoformat(), "cost_usd": res["cost_usd"], "model": mode["model"],
                                "served": res["served_models"], "iteration": n})
        if res["rate_limited"]:
            delay = res["reset_delay"] or CONFIG["RATE_LIMIT_SLEEP_S"]
            delay = min(delay, CONFIG["RATE_LIMIT_SLEEP_MAX_S"])
            log(f"RATE/USAGE LIMIT hit mid-iteration; sleeping {delay // 60} min then retrying "
                f"(partial work stays in the working tree; not counted as an iteration)")
            st["history"].append({"ts": ts(), "event": "rate_limit_pause", "seconds": delay})
            save_state(st)
            time.sleep(delay)
            continue
        st["iteration"] = n
        log(f"claude done: ok={res['ok']} exit={res['exit']} timed_out={res['timed_out']} "
            f"turns={res['num_turns']} cost=${res['cost_usd']:.2f} {res['duration_s']}s served={res['served_models']}")
        if res["result_tail"]:
            log("agent summary: " + res["result_tail"].replace("\n", " | ")[-600:])
        restore_frozen()
        auto_commit(n)
        head_after = git_head()
        evaluate(st, n, mode["mode"])
        st["history"].append({"ts": ts(), "iteration": n, "milestone_before": None, "mode": mode["mode"],
                              "model": mode["model"], "cost_usd": res["cost_usd"], "ok": res["ok"],
                              "head_before": head_before[:10], "head_after": head_after[:10],
                              "milestone_after": st["milestone"], "stall": st["stall"],
                              "best_progress": st["best_progress"]})
        st["history"] = st["history"][-500:]
        save_state(st)
        if once:
            log("--once: stopping after one iteration")
            return 0
        time.sleep(CONFIG["SLEEP_BETWEEN_ITER_S"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--recheck-models", action="store_true")
    ap.add_argument("--reset-stall", action="store_true")
    ap.add_argument("--max-iterations", type=int)
    a = ap.parse_args()
    if a.max_iterations:
        CONFIG["MAX_ITERATIONS"] = a.max_iterations
    st = load_state()
    if a.status:
        print(json.dumps({k: v for k, v in st.items() if k not in ("usage", "history")}, indent=2))
        print(f"spend last 5h: ${window_cost(st, 5):.2f}   total: ${sum(u['cost_usd'] for u in st['usage']):.2f}")
        print("latest verdict:", latest_verdict_line())
        return 0
    if a.reset_stall:
        st.update(stall=0, escalated=False, stopped_reason=None)
        save_state(st)
        print("stall counter reset")
        return 0
    if a.dry_run:
        print("CONFIG:", json.dumps(CONFIG, indent=2))
        mode = choose_mode(st)
        print("MODE:", mode)
        print("ARGS:", " ".join(claude_args(mode["model"], mode["effort"], mode["budget"])))
        print("---- PROMPT ----")
        print(compose_prompt(st, mode, st["iteration"] + 1))
        return 0
    acquire_lock()
    try:
        preflight(st, a.recheck_models)
        return iterate(st, a.once)
    except KeyboardInterrupt:
        log("interrupted by user; state saved — rerun ./loop.sh to resume")
        save_state(st)
        return 130
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
