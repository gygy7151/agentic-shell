# Adaptive Agent

A small CLI agent that solves arbitrary natural-language tasks by writing and
executing Python tools on the fly. When a generated tool turns out to be
genuinely reusable, the user can approve saving it to a local `skills/`
directory and the agent will see it in every future session.

The whole control loop is hand-rolled in stdlib Python — no agent library, no
SDK, just one `urllib` call to the Anthropic Messages API.

## Quick start

```bash
# requirements: Python 3.10+, an Anthropic API key
export ANTHROPIC_API_KEY=sk-ant-...

# (optional) override the default model
export ANTHROPIC_MODEL=claude-sonnet-4-5-20250929

python agent.py
```

You'll get an interactive REPL:

```
Adaptive Agent. Type a task. Ctrl-C or empty 'exit' to quit.

you> 아래 JSON 데이터에서 hp가 100 이상인 몬스터의 이름과 평균 hp를 알려줘.
     [{"name":"Goblin","hp":80},{"name":"Orc","hp":150},{"name":"Dragon","hp":300}]

agent> Plan: parse the JSON, filter where hp >= 100, then average those.
       Success criterion: print the qualifying names and their mean hp.

[tool: run_python]
agent> 조건을 만족하는 몬스터는 Orc, Dragon이고 평균 hp는 225.0입니다.
       이 로직을 'monster_hp_summary' 스킬로 저장해둘까요?

you> y
[saves to skills/monster_hp_summary/]
```

## Project layout

```
agent.py        main loop — single while-loop driving the API
tools.py        built-in tool schemas + dispatch
prompts.py      system prompt with the four operating principles
skills/         persisted, user-approved tools (SKILL.md + tool.py per skill)
workspace/      scratch directory for run_python invocations
```

### Built-in tools

| Tool          | Purpose                                                    |
| ------------- | ---------------------------------------------------------- |
| `run_python`  | Execute a Python script in a sandboxed subprocess          |
| `read_file`   | Read any UTF-8 file                                        |
| `write_file`  | Write to the workspace                                     |
| `ask_user`    | Ask a clarifying question (HITL)                           |
| `save_skill`  | Persist a generated tool — always gated on a y/n prompt    |
| `load_skill`  | Fetch a saved skill's source                               |
| `list_skills` | Index of saved skills with their "when to use" notes       |

## Design decisions

**Direct HTTP, no SDK, no framework.**
`agent.py` posts to `https://api.anthropic.com/v1/messages` via `urllib`.
The control loop is a single `while` loop in `run_turn()` that drives the
model: send messages → receive content blocks → run tool calls → append
`tool_result` blocks → repeat. This was a deliberate choice — agent
abstractions hide the very thing this exercise is about.

**Subprocess-isolated execution.**
`run_python` writes the model's code to `workspace/_run.py` and runs it in a
fresh `python3` subprocess with a hard timeout. This is the simplest viable
isolation: process state can't leak between calls, stdout/stderr are cleanly
separated, and the model gets real tracebacks back as `tool_result` content,
which is what makes self-correction work.

**Skills as data, not code.**
A saved skill is a directory with two files:
- `SKILL.md` — a one-paragraph "when to use" note
- `tool.py` — the actual code

At startup, the agent reads every `SKILL.md` and injects the *summaries* (not
the source) into the system prompt. The full source is only loaded when the
agent decides to use the skill, via `load_skill`. This keeps the prompt small
even as the skill library grows — the model sees a menu, fetches a recipe.

**Three explicit human-in-the-loop touchpoints.**
1. `ask_user` whenever the task is ambiguous or missing inputs.
2. `save_skill` always prints a preview and prompts y/n before writing.
3. The user types every task themselves at the `you>` prompt.

The system prompt explicitly tells the model to prefer `ask_user` over
guessing.

**Self-correction via traceback feedback.**
Up to 12 model iterations per user turn. When `run_python` returns a
non-zero exit, the full STDOUT/STDERR/exit-code goes back as the next
`tool_result`. The system prompt instructs the model to read the traceback,
fix the actual cause, and re-run — not to guess at fixes blindly.

**Four operating principles in the system prompt.**
The model is instructed to:
1. State assumptions and a success criterion before writing code.
2. Keep code minimal — no speculative features.
3. Make surgical edits when modifying saved skills.
4. Treat the success criterion as a verifiable test and loop until it passes.

These are baked into `prompts.py`. They are the main lever we have to keep
generations on-task and avoid the usual LLM tendency to overcomplicate.

## Limitations & possible improvements

- **Not a real sandbox.** `run_python` runs as the same user that started
  the agent. For untrusted tasks, swap subprocess for a container
  (firecracker, nsjail, or a Docker exec). I deliberately kept it simple
  here.
- **No streaming.** Each LLM response is awaited in full. Streaming would
  make long-running generations feel snappier; the loop structure is ready
  for it.
- **No conversation compaction.** The full message history is sent every
  turn, so very long sessions will eventually hit the context window. A
  summarizer pass when the prompt size exceeds some threshold would be the
  natural next step.
- **Python-only skills.** The pattern would work identically for shell or
  Node, but only Python is wired up right now.
- **No retry/backoff on API errors.** A transient 5xx aborts the run.
  Wrapping `call_llm` in exponential backoff is a few lines.
- **`load_skill` returns source text.** The model has to either re-run the
  code or import from `skills/<name>/tool.py`. A direct callable interface
  (e.g. registering each skill as its own tool) would be cleaner but
  expensive in prompt tokens at scale — the current approach is the
  progressive-disclosure compromise.
- **No automated tests.** Manual verification only against the four
  example flows. A small pytest suite that mocks `call_llm` and asserts
  the dispatch layer's behaviour would harden this.

## License

MIT.
