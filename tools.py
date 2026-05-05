"""Built-in tools, schemas, and dispatch."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

# ---- tool schemas exposed to the model ---------------------------------------

BUILTIN_TOOLS: list = [
    {
        "name": "run_python",
        "description": (
            "Execute Python source in a fresh subprocess with a timeout. "
            "This is your primary mechanism for building and running tools on the fly. "
            "Always print() the value you want to observe — only stdout/stderr are captured. "
            "On error, the full traceback is returned so you can debug and re-run."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Complete Python script to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Hard timeout in seconds (default 20, max 120).",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file and return its contents.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write text to a file in the workspace. "
            "Relative paths are resolved against ./workspace/. Overwrites if exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the user a clarifying question and wait for their typed reply. "
            "Use this WHENEVER the task is ambiguous, missing required inputs, "
            "or you must choose between materially different interpretations. "
            "Prefer asking once over guessing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "save_skill",
        "description": (
            "Persist a tool you wrote so it can be reused across sessions. "
            "ALWAYS triggers an interactive y/n confirmation. "
            "Provide snake_case name, a short 'when_to_use' description for the index, "
            "and the full Python source (a self-contained module is best)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "when_to_use": {"type": "string"},
                "code": {"type": "string"},
            },
            "required": ["name", "when_to_use", "code"],
        },
    },
    {
        "name": "load_skill",
        "description": (
            "Return the source code of a previously saved skill so you can paste it "
            "into a run_python call or import it from skills/<name>/tool.py."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_skills",
        "description": "List all currently saved skills with their 'when to use' descriptions.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

# ---- dispatch ---------------------------------------------------------------

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def execute_tool(name, args, confirm_cb, ask_cb, skills_dir, workspace):
    try:
        if name == "run_python":
            timeout = min(int(args.get("timeout", 20) or 20), 120)
            return _run_python(args["code"], timeout, workspace)
        if name == "read_file":
            return _read_file(args["path"])
        if name == "write_file":
            return _write_file(args["path"], args["content"], workspace)
        if name == "ask_user":
            answer = ask_cb(args["question"])
            return answer or "(user gave no answer)"
        if name == "save_skill":
            return _save_skill(
                args["name"], args["when_to_use"], args["code"], skills_dir, confirm_cb
            )
        if name == "load_skill":
            return _load_skill(args["name"], skills_dir)
        if name == "list_skills":
            idx = load_skills_index(skills_dir)
            return idx or "(no skills saved yet)"
        return f"ERROR: unknown tool '{name}'"
    except KeyError as e:
        return f"ERROR: missing argument {e}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ---- implementations --------------------------------------------------------


def _run_python(code: str, timeout: int, workspace: Path) -> str:
    workspace = workspace.resolve()
    script = workspace / "_run.py"
    script.write_text(code, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["python3", "_run.py"],  # relative — resolved against cwd below
            capture_output=True,
            timeout=timeout,
            cwd=str(workspace),
            text=True,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: execution timed out after {timeout}s"
    parts = []
    if proc.stdout:
        parts.append(f"STDOUT:\n{proc.stdout.rstrip()}")
    if proc.stderr:
        parts.append(f"STDERR:\n{proc.stderr.rstrip()}")
    if proc.returncode != 0:
        parts.append(f"EXIT CODE: {proc.returncode}")
    return "\n\n".join(parts) or "(no output)"


def _read_file(path: str) -> str:
    p = Path(path)
    if not p.exists():
        return f"ERROR: file not found: {path}"
    return p.read_text(encoding="utf-8", errors="replace")


def _write_file(path: str, content: str, workspace: Path) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = workspace / p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {p}"


def _save_skill(name, when_to_use, code, skills_dir: Path, confirm_cb) -> str:
    if not _SAFE_NAME.match(name):
        return "ERROR: invalid name. Use snake_case, 2-41 chars, must start with a letter."
    print("\n--- proposed skill -----------------------------")
    print(f"name        : {name}")
    print(f"when to use : {when_to_use}")
    print("--- code preview -------------------------------")
    preview = code if len(code) <= 800 else code[:800] + "\n... (truncated)"
    print(preview)
    print("------------------------------------------------")
    if not confirm_cb("Save this skill so future sessions can reuse it?"):
        return "user declined to save the skill"
    sdir = skills_dir / name
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "SKILL.md").write_text(
        f"# {name}\n\n## When to use\n{when_to_use.strip()}\n",
        encoding="utf-8",
    )
    (sdir / "tool.py").write_text(code, encoding="utf-8")
    return f"saved skill to {sdir} (will appear in next session's index)"


def _load_skill(name: str, skills_dir: Path) -> str:
    p = skills_dir / name / "tool.py"
    if not p.exists():
        return f"ERROR: no saved skill named '{name}'"
    return p.read_text(encoding="utf-8")


def load_skills_index(skills_dir: Path) -> str:
    """Return a compact one-line-per-skill index for the system prompt."""
    if not skills_dir.exists():
        return ""
    lines = []
    for sdir in sorted(skills_dir.iterdir()):
        if not sdir.is_dir():
            continue
        md = sdir / "SKILL.md"
        if not md.exists():
            continue
        text = md.read_text(encoding="utf-8")
        # extract section under "## When to use"
        when = ""
        if "## When to use" in text:
            when = text.split("## When to use", 1)[1].strip().split("\n\n", 1)[0]
        when = " ".join(when.split())[:200]
        lines.append(f"- `{sdir.name}` — {when}")
    return "\n".join(lines)
