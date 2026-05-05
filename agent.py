#!/usr/bin/env python3
"""Adaptive CLI agent — Groq backend (OpenAI-compatible API)."""
from __future__ import annotations

import json
import os
import re
import select
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from prompts import SYSTEM_PROMPT_TEMPLATE
from tools import BUILTIN_TOOLS, execute_tool, load_skills_index

ROOT = Path(__file__).parent
SKILLS_DIR = ROOT / "skills"
WORKSPACE = ROOT / "workspace"
WORKSPACE.mkdir(exist_ok=True)
SKILLS_DIR.mkdir(exist_ok=True)

API_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_TOKENS = int(os.environ.get("GROQ_MAX_TOKENS", "4096"))
MAX_ITERS_PER_TURN = 12
MAX_HISTORY_MESSAGES = int(os.environ.get("GROQ_HISTORY_CAP", "20"))


def _to_openai_tools(anthropic_tools: list) -> list:
    """Translate Anthropic-style schemas (in tools.py) into OpenAI tool format."""
    out = []
    for t in anthropic_tools:
        params = t.get("input_schema") or {"type": "object", "properties": {}}
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": params,
                },
            }
        )
    return out


def _parse_retry_after(body_text: str) -> float | None:
    """Pull the wait time out of a Groq 429 message body. Handles both
    `Xs` and `Xms` units — Groq emits the latter for very short waits."""
    m = re.search(r"try again in ([\d.]+)(ms|s)\b", body_text)
    if not m:
        return None
    try:
        val = float(m.group(1))
    except ValueError:
        return None
    return val / 1000.0 if m.group(2) == "ms" else val


def call_llm(messages: list, system: str, tools: list) -> dict:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("ERROR: GROQ_API_KEY not set. export it and retry.")

    full_messages = [{"role": "system", "content": system}] + messages
    payload = json.dumps(
        {
            "model": MODEL,
            "max_tokens": MAX_TOKENS,
            "messages": full_messages,
            "tools": _to_openai_tools(tools),
            "tool_choice": "auto",
        }
    ).encode()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
        "User-Agent": "adaptive-agent/1.0",
    }

    for attempt in range(3):
        req = urllib.request.Request(API_URL, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")
            if e.code == 429 and attempt < 2:
                wait = _parse_retry_after(body_text)
                if wait is None:
                    wait = 2.0  # body format changed — retry with a sane default
                if wait <= 30:
                    print(f"\n(rate limited; waiting {wait:.2f}s and retrying)")
                    time.sleep(wait + 0.5)
                    continue
            sys.exit(f"\nAPI HTTP {e.code}:\n{body_text}\n")
        except urllib.error.URLError as e:
            sys.exit(f"\nNetwork error: {e}\n")
    sys.exit("\nAPI: gave up after retries.\n")


def _trim_history(messages: list, cap: int = MAX_HISTORY_MESSAGES) -> list:
    """Cap message list at the earliest user-message boundary that keeps len <= cap.
    Cutting at user-message boundaries avoids orphaning tool-result messages from
    their preceding tool_calls assistant message (the API rejects that)."""
    if len(messages) <= cap:
        return messages
    for i, m in enumerate(messages):
        if m.get("role") == "user" and len(messages) - i <= cap:
            return messages[i:]
    return messages


def _read_user_input() -> str:
    """Read one logical message. Drains pasted multi-line input from stdin
    so a JSON/code block paste arrives as a single message instead of being
    split across several input() calls."""
    try:
        first = input("\nyou> ")
    except EOFError:
        return ""
    lines = [first]
    while select.select([sys.stdin], [], [], 0.05)[0]:
        line = sys.stdin.readline()
        if not line:
            break
        lines.append(line.rstrip("\n"))
    return "\n".join(lines).strip()


def confirm(prompt: str) -> bool:
    while True:
        ans = input(f"{prompt} [y/n]: ").strip().lower()
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no", ""):
            return False


def ask(question: str) -> str:
    print(f"\nagent asks> {question}")
    return input("you> ").strip()


def run_turn(messages: list, system: str) -> None:
    """Drive the model loop until it stops emitting tool calls."""
    for _ in range(MAX_ITERS_PER_TURN):
        resp = call_llm(messages, system, BUILTIN_TOOLS)
        try:
            msg = resp["choices"][0]["message"]
        except (KeyError, IndexError):
            print(f"\n(unexpected response: {json.dumps(resp)[:500]})")
            return

        text = msg.get("content")
        if text and text.strip():
            print(f"\nagent> {text.strip()}")

        tool_calls = msg.get("tool_calls") or []

        # Echo the assistant turn back into history (required for follow-up calls)
        assistant_entry = {"role": "assistant", "content": text or ""}
        if tool_calls:
            assistant_entry["tool_calls"] = tool_calls
        messages.append(assistant_entry)

        if not tool_calls:
            return

        for tc in tool_calls:
            fn = tc.get("function", {}) or {}
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            print(f"\n[tool: {name}]")
            out = execute_tool(
                name=name,
                args=args,
                confirm_cb=confirm,
                ask_cb=ask,
                skills_dir=SKILLS_DIR,
                workspace=WORKSPACE,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": out if isinstance(out, str) else str(out),
                }
            )

    print("\n(stopped: max iterations reached for this turn)")


def main() -> None:
    print(f"Adaptive Agent (Groq / {MODEL}). Type a task. Ctrl-C or 'exit' to quit.")
    print("(multi-line paste supported; type 'exit' alone to quit)\n")
    skills_index = load_skills_index(SKILLS_DIR) or "(none yet)"
    system = SYSTEM_PROMPT_TEMPLATE.format(skills_index=skills_index)

    messages: list = []
    try:
        while True:
            user_in = _read_user_input()
            if not user_in:
                continue
            if user_in.lower() in ("exit", "quit"):
                break
            messages.append({"role": "user", "content": user_in})
            run_turn(messages, system)
            messages[:] = _trim_history(messages)
    except (KeyboardInterrupt, EOFError):
        pass
    print("\nbye.")


if __name__ == "__main__":
    main()
