#!/usr/bin/env python3
"""Send one Claude Code hook event to the tracker. Stdlib only.

report.sh runs this in the background with the hook's JSON on stdin, so
nothing here can slow down or fail a session. It:

- works out the repo and branch from the event's own folder (cwd), on every
  event, so an agent working in another repo is reported against that repo;
- finds the issue the work belongs to: a branch named <issue>-<slug>, a
  "Closes #N" in a PR the session opens, or "#N" in the first prompt, keyed
  as owner/repo#N;
- keeps only what decision 7 allows: your prompts up to 4,000 characters
  (8,000 for the first), machine-made pastes over 15 lines folded, Claude's
  replies up to 2,000 characters, no tool output (not even a failed tool's
  error, which is usually its stderr), and secrets scrubbed;
- when a turn or the session ends, adds up the tokens each model used, from
  the transcript and its subagents' transcripts, so the tracker can show cost;
- posts it to CLAUDE_EVENTS_URL with CLAUDE_EVENTS_TOKEN, giving up after 3 s.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

MAX_PROMPT = 4000
MAX_FIRST_PROMPT = 8000
MAX_REPLY = 2000
MAX_SUMMARY = 160
FOLD_OVER_LINES = 15
FOLD_KEEP_LINES = 3
POST_TIMEOUT = 3
MAX_MODELS = 10

# --- Secrets ------------------------------------------------------------------

REDACTED = "[redacted]"
SECRET_PATTERNS = [
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY[A-Z ]*-----.*?"
            r"(?:-----END [A-Z ]*PRIVATE KEY[A-Z ]*-----|\Z)",
            re.S,
        ),
        "[private key redacted]",
    ),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    (re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{20,}"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), REDACTED),
    (re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"), REDACTED),  # Stripe
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), REDACTED),  # GitLab
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), REDACTED),  # Google
    # curl -u user:pass, and mysql -phunter2 (the password glued to -p)
    (re.compile(r"(?<!\S)(-u|--user)(\s+|=)([^\s:]+):(\S+)"), r"\1\2\3:" + REDACTED),
    (re.compile(r"(?i)\b(mysql\w*|mariadb\w*)\b([^\n]*?\s)-p(?=\S)\S+"), r"\1\2-p" + REDACTED),
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{16,}"), r"\1 " + REDACTED),
    # Credentials in any URL: https://, postgres://, redis://, amqp://, …
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1" + REDACTED + "@"),
    # A flag and its value: --password hunter2, --token=abc
    (
        re.compile(
            r"(?i)(--?[\w-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key)[\w-]*)"
            r"(\s+|=)(?!-)(\S+)"
        ),
        r"\1\2" + REDACTED,
    ),
    (
        re.compile(
            r"(?i)\b([\w-]*(?:pass(?:wd|word)?|pwd|pw|secret|token|api[_-]?key|access[_-]?key)"
            r"[\w-]*)"
            # the key may be quoted, as in JSON or YAML: "api_key": "…"
            r"([\"']?\s*[:=]\s*)(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;]+)"
        ),
        r"\1\2" + REDACTED,
    ),
    (
        re.compile(
            r"(?i)\b([\w-]*(?:secret|api[_-]?key|access[_-]?key|password)[\w-]*)"
            r"(\s+)([A-Za-z0-9/+=_.-]{16,})"
        ),
        r"\1\2" + REDACTED,
    ),
]


def scrub(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# --- Folding machine-made pastes -----------------------------------------------

MACHINE_LINE = re.compile(
    r"\d{1,2}:\d{2}:\d{2}"  # a timestamp
    r"|\d{4}-\d{2}-\d{2}"  # a date
    r"|^\s+at\s"  # a JS / Java stack frame
    r'|^\s*File ".*", line \d+'  # a Python stack frame
    r"|^Traceback \(most recent call last\)"
    r"|^\s*[\w.]*(?:Error|Exception)\b"
    r"|\b(?:DEBUG|INFO|WARN|WARNING|ERROR|FATAL|TRACE)\b"
    r"|[;{}]\s*$"  # a line of code
    r"|^(?: {4}|\t)"  # indented
    r"|=>|::|</|/>|^\s*[#/]{2}|^\s*\$ "
    r"|^\s*[\"'][^\"']+[\"']\s*:"  # a JSON or YAML key
    r"|^\s*[\[\]{}],?\s*$"  # a lone bracket
    r"|^[^,\s]+(?:,[^,\s]*){2,}$"  # a CSV row
)


def _machine_share(lines: list[str]) -> float:
    filled = [ln for ln in lines if ln.strip()]
    return sum(1 for ln in filled if MACHINE_LINE.search(ln)) / len(filled) if filled else 0.0


def _fold(lines: list[str]) -> list[str]:
    hidden = len(lines) - FOLD_KEEP_LINES
    return [*lines[:FOLD_KEEP_LINES], f"[… {hidden} more lines folded]"]


def fold_pastes(text: str) -> str:
    """Fold pasted blocks over 15 lines that look machine-made to 3 lines and a count.

    A fenced code block is one block and always counts as machine-made.
    Outside fences, neighbouring paragraphs that are each mostly machine-made
    lines join into one block, so a log with blank lines in it still folds.
    """
    out: list[str] = []
    para: list[str] = []
    run: list[str] = []  # machine-made paragraphs, joined
    fence: list[str] | None = None

    def flush_run() -> None:
        nonlocal run
        blanks = len(run) - len("\n".join(run).rstrip("\n").split("\n")) if run else 0
        body = run[: len(run) - blanks]
        out.extend(_fold(body) if len(body) > FOLD_OVER_LINES else body)
        out.extend(run[len(run) - blanks :])
        run = []

    def flush_para() -> None:
        nonlocal para
        if not para:
            return
        content = [ln for ln in para if ln.strip()]
        if content and _machine_share(content) >= 0.5:
            run.extend(para)
        else:
            flush_run()
            out.extend(para)
        para = []

    for line in text.split("\n"):
        if fence is not None:
            fence.append(line)
            if line.strip().startswith("```"):
                body = fence[1:-1]
                if len(body) > FOLD_OVER_LINES:
                    out.extend([fence[0], *_fold(body), fence[-1]])
                else:
                    out.extend(fence)
                fence = None
            continue
        if line.strip().startswith("```"):
            flush_para()
            flush_run()
            fence = [line]
        elif line.strip():
            para.append(line)
        else:
            para.append(line)
            flush_para()
    if fence is not None:  # unclosed fence: treat what's there as one block
        body = fence[1:]
        out.extend([fence[0], *_fold(body)] if len(body) > FOLD_OVER_LINES else fence)
    flush_para()
    flush_run()
    return "\n".join(out)


def clip(text: object, limit: int) -> str:
    s = text if isinstance(text, str) else ("" if text is None else str(text))
    s = s.strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def clean(text: object, limit: int) -> str:
    return clip(scrub(text if isinstance(text, str) else ""), limit)


# --- Repo, branch and issue -----------------------------------------------------


def _git(cwd: str, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, *args], capture_output=True, text=True, timeout=2, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def repo_from_remote(url: str) -> str | None:
    """owner/name from a remote URL: github.com, SSH, or the cloud git proxy."""
    m = re.search(r"[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url.strip())
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


def issue_from_branch(branch: str) -> int | None:
    """N from a branch named <N>-<slug>, also under a prefix such as claude/."""
    m = re.match(r"^([1-9][0-9]*)-[\w.-]+$", branch.rsplit("/", 1)[-1])
    return int(m.group(1)) if m else None


ISSUE_URL = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/(?:issues|pull)/([1-9][0-9]*)")
# GitHub owners are letters, digits and hyphens; repo names may also hold dots.
ISSUE_REF = re.compile(r"(?<![\w/#&.])(?:([\w-]+)/)?([\w.-]+)?#([1-9][0-9]*)\b")
CLOSES_REF = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+"
    r"(?:https://github\.com/([\w.-]+)/([\w.-]+)/issues/([1-9][0-9]*)"
    r"|(?:([\w-]+)/)?([\w.-]+)?#([1-9][0-9]*))"
)


def _key(owner: str | None, name: str | None, number: str, repo: str | None) -> str | None:
    if not name:
        return f"{repo}#{number}" if repo else None
    if not owner:
        if not repo:
            return None
        owner = repo.split("/")[0]
    return f"{owner}/{name}#{number}".lower()


def issue_refs(text: str, repo: str | None) -> list[str]:
    """Issue keys mentioned as #N, name#N, owner/name#N or a GitHub issue URL."""
    keys: list[str] = []
    for m in ISSUE_URL.finditer(text):
        keys.append(f"{m.group(1)}/{m.group(2)}#{m.group(3)}".lower())
    for m in ISSUE_REF.finditer(ISSUE_URL.sub(" ", text)):
        key = _key(m.group(1), m.group(2), m.group(3), repo)
        if key:
            keys.append(key)
    return list(dict.fromkeys(keys))


def closes_refs(text: str, repo: str | None) -> list[str]:
    keys = []
    for m in CLOSES_REF.finditer(text):
        if m.group(3):
            keys.append(f"{m.group(1)}/{m.group(2)}#{m.group(3)}".lower())
        else:
            key = _key(m.group(4), m.group(5), m.group(6), repo)
            if key:
                keys.append(key)
    return list(dict.fromkeys(keys))


# --- The event ---------------------------------------------------------------------

SUMMARY_KEYS = ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt")


def tool_summary(name: str, tool_input: object) -> str:
    """One line for a tool call: the command, path or pattern it acted on."""
    if isinstance(tool_input, dict):
        for key in SUMMARY_KEYS:
            if isinstance(tool_input.get(key), str) and tool_input[key].strip():
                first = tool_input[key].strip().splitlines()[0]
                return clean(f"{name}: {first}", MAX_SUMMARY)
    return clip(name, MAX_SUMMARY)


def plan_input(name: str, tool_input: object) -> dict | None:
    """The few fields of a planning or agent tool call the tracker needs."""
    if not isinstance(tool_input, dict):
        return None
    if name == "TodoWrite" and isinstance(tool_input.get("todos"), list):
        return {
            "todos": [
                {"content": clean(t.get("content"), 200), "status": clip(t.get("status"), 20)}
                for t in tool_input["todos"][:100]
                if isinstance(t, dict)
            ]
        }
    if name == "TaskCreate":
        return {"subject": clean(tool_input.get("subject"), 200)}
    if name == "TaskUpdate":
        out = {"taskId": clip(tool_input.get("taskId"), 20)}
        if tool_input.get("status"):
            out["status"] = clip(tool_input["status"], 20)
        if tool_input.get("subject"):
            out["subject"] = clean(tool_input["subject"], 200)
        return out
    if name in ("Agent", "Task"):
        return {
            "description": clean(tool_input.get("description"), 200),
            "subagent_type": clip(tool_input.get("subagent_type"), 100),
            "run_in_background": bool(tool_input.get("run_in_background")),
        }
    return None


def task_id(response: object) -> str | None:
    """The id TaskCreate gave the new task. Only the id is kept, never the output."""
    text = response if isinstance(response, str) else json.dumps(response)
    m = re.search(r'"id"\s*:\s*"?(\d+)', text) or re.search(r"#(\d+)", text)
    return m.group(1) if m else None


def last_reply_from_transcript(path: str) -> str:
    """Claude's last text from the end of a transcript, when the hook didn't carry it."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 256 * 1024))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        msg = obj.get("message") if isinstance(obj, dict) else None
        if obj.get("type") != "assistant" or not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content
        texts = [
            b.get("text", "")
            for b in content or []
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip()
        ]
        if texts:
            return "\n\n".join(texts)
    return ""


TOKEN_KINDS = ("input", "output", "cache_read", "cache_write_5m", "cache_write_1h")


def _count(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def usage_from_transcripts(path: str) -> dict:
    """Tokens per model, from a transcript and its subagents' transcripts.

    A reply written over several lines repeats its usage under one message id,
    so each id counts once.
    """
    main = Path(path)
    files = [main, *sorted(main.with_suffix("").glob("subagents/*.jsonl"))]
    replies: dict[str, tuple[str, dict]] = {}
    for file in files:
        try:
            lines = file.read_text("utf-8", "replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines):
            if '"usage"' not in line:
                continue
            try:
                msg = json.loads(line).get("message")
            except (ValueError, AttributeError):
                continue
            if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                continue
            model = msg.get("model")
            if not isinstance(model, str) or not model or model.startswith("<"):
                continue  # "<synthetic>" replies cost nothing
            replies[str(msg.get("id") or f"{file}:{n}")] = (model, msg["usage"])
    totals: dict[str, dict[str, int]] = {}
    for model, usage in replies.values():
        if model not in totals and len(totals) >= MAX_MODELS:
            continue
        t = totals.setdefault(clip(model, 100), dict.fromkeys(TOKEN_KINDS, 0))
        t["input"] += _count(usage.get("input_tokens"))
        t["output"] += _count(usage.get("output_tokens"))
        t["cache_read"] += _count(usage.get("cache_read_input_tokens"))
        split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
        five, hour = (
            _count(split.get("ephemeral_5m_input_tokens")),
            _count(split.get("ephemeral_1h_input_tokens")),
        )
        if not five and not hour:
            five = _count(usage.get("cache_creation_input_tokens"))
        t["cache_write_5m"] += five
        t["cache_write_1h"] += hour
    return totals


def pr_closes(tool: str, tool_input: dict, repo: str | None) -> list[str]:
    """Issues a pull request closes, when this tool call opens one."""
    if "create_pull_request" in tool:
        text = f"{tool_input.get('title', '')}\n{tool_input.get('body', '')}"
        if tool_input.get("owner") and tool_input.get("repo"):
            repo = f"{tool_input['owner']}/{tool_input['repo']}".lower()
        return closes_refs(text, repo)
    command = str(tool_input.get("command", ""))
    if tool == "Bash" and "gh pr create" in command:
        return closes_refs(command, repo)
    return []


def is_first_prompt(session_id: str, state_dir: str) -> bool:
    """True the first time a session submits a prompt (decided by a marker file)."""
    safe = re.sub(r"[^\w-]", "_", session_id)[:100]
    marker = Path(state_dir) / f"claude-tracker-{safe}.first"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        with open(marker, "x"):
            return True
    except FileExistsError:
        return False
    except OSError:
        return True


def event_time(env: dict) -> float:
    """When the hook fired (set by report.sh before it backgrounds), else now."""
    try:
        return float(env.get("CLAUDE_EVENT_TS") or "")
    except ValueError:  # e.g. BSD date printing a literal "%N"
        return time.time()


def build(hook: dict, env: dict, state_dir: str) -> dict | None:
    """The payload for the tracker, from the hook's input and the environment."""
    kind, sid = hook.get("hook_event_name"), hook.get("session_id")
    if not isinstance(kind, str) or not isinstance(sid, str) or not sid:
        return None
    cwd = hook.get("cwd") if isinstance(hook.get("cwd"), str) else ""
    repo = repo_from_remote(_git(cwd, "remote", "get-url", "origin")) if cwd else None
    branch = _git(cwd, "symbolic-ref", "--short", "HEAD") if cwd else ""
    refs: dict = {}
    number = issue_from_branch(branch) if branch else None
    if number and repo:
        refs["branch"] = f"{repo}#{number}"

    out: dict = {
        "v": 1,
        "event": kind,
        "session_id": sid,
        "remote_id": env.get("CLAUDE_CODE_REMOTE_SESSION_ID") or None,
        "repo": repo,
        "branch": branch or None,
        "ts": event_time(env),
    }
    if hook.get("agent_id"):
        out["agent_id"] = clip(hook["agent_id"], 100)
        out["agent_type"] = clip(hook.get("agent_type"), 100)

    if kind == "UserPromptSubmit":
        first = is_first_prompt(sid, state_dir)
        raw = hook.get("prompt")
        prompt = fold_pastes(scrub(raw if isinstance(raw, str) else ""))
        out["prompt"] = clip(prompt, MAX_FIRST_PROMPT if first else MAX_PROMPT)
        if first:
            found = issue_refs(prompt, repo)
            if found:
                refs["prompt"] = found
    elif kind in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        name = clip(hook.get("tool_name"), 100) or "tool"
        tool_input = hook.get("tool_input")
        out["tool"] = name
        out["tool_summary"] = tool_summary(name, tool_input)
        planned = plan_input(name, tool_input)
        if planned is not None:
            out["tool_input"] = planned
        if name == "TaskCreate" and kind == "PostToolUse":
            out["task_id"] = task_id(hook.get("tool_response"))
        if isinstance(tool_input, dict) and kind == "PreToolUse":
            found = pr_closes(name, tool_input, repo)
            if found:
                refs["pr"] = found
    elif kind == "Notification":
        out["message"] = clean(hook.get("message"), MAX_REPLY)
        out["notification_type"] = clip(hook.get("notification_type"), 40)
    elif kind in ("Stop", "SubagentStop", "StopFailure"):
        reply = hook.get("last_assistant_message")
        if not isinstance(reply, str) and kind != "SubagentStop" and hook.get("transcript_path"):
            reply = last_reply_from_transcript(str(hook["transcript_path"]))
        out["reply"] = clean(reply, MAX_REPLY)
        if kind == "StopFailure":
            out["error"] = clean(hook.get("error"), 500)
    elif kind == "SessionEnd":
        out["reason"] = clip(hook.get("reason"), 40)
    if kind in ("Stop", "StopFailure", "SessionEnd") and hook.get("transcript_path"):
        usage = usage_from_transcripts(str(hook["transcript_path"]))
        if usage:
            out["usage"] = usage
    if refs:
        out["refs"] = refs
    return out


def post(url: str, token: str, payload: dict) -> None:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=POST_TIMEOUT).close()
    except Exception:  # noqa: BLE001 - the session must never notice a failed post
        pass


def main() -> int:
    env = dict(os.environ)
    url, token = env.get("CLAUDE_EVENTS_URL"), env.get("CLAUDE_EVENTS_TOKEN", "")
    if not url or not token:
        return 0
    try:
        hook = json.loads(sys.stdin.read() or "{}")
        payload = build(hook, env, tempfile.gettempdir()) if isinstance(hook, dict) else None
    except Exception:  # noqa: BLE001
        return 0
    if payload:
        post(url, token, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
