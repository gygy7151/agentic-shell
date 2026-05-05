#!/usr/bin/env python3
"""Adaptive CLI agent — Groq backend (OpenAI-compatible API)."""
from __future__ import annotations

import json
import os
import sys
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


def call_llm(messages: list, system: str, tools: list) -> dict:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        sys.exit("ERROR: GROQ_API_KEY not set. export it and retry.")

    # OpenAI style: system is the first message in the messages array.
    full_messages = [{"role": "system", "content": system}] + messages

    body = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "messages": full_messages,
        "tools": _to_openai_tools(tools),
        "tool_choice": "auto",
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        sys.exit(f"\nAPI HTTP {e.code}:\n{body_text}\n")
    except urllib.error.URLError as e:
        sys.exit(f"\nNetwork error: {e}\n")


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
    print(f"Adaptive Agent (Groq / {MODEL}). Type a task. Ctrl-C or 'exit' to quit.\n")
    skills_index = load_skills_index(SKILLS_DIR) or "(none yet)"
    system = SYSTEM_PROMPT_TEMPLATE.format(skills_index=skills_index)

    messages: list = []
    try:
        while True:
            user_in = input("\nyou> ").strip()
            if not user_in:
                continue
            if user_in.lower() in ("exit", "quit"):
                break
            messages.append({"role": "user", "content": user_in})
            run_turn(messages, system)
    except (KeyboardInterrupt, EOFError):
        pass
    print("\nbye.")


if __name__ == "__main__":
    main()
