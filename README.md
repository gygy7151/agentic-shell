# Agentic Shell

A small CLI agent that solves arbitrary natural-language tasks by writing and
executing Python tools on the fly. When a generated tool turns out to be
genuinely reusable, the user can approve saving it to a local `skills/`
directory and the agent will see it in every future session.

The whole control loop is hand-rolled in stdlib Python — no agent library, no
SDK, just one `urllib` call to Groq's OpenAI-compatible Chat Completions API.

## Quick start

```bash
# requirements: Python 3.10+, a Groq API key (free tier works)
export GROQ_API_KEY=gsk_...

# (optional) overrides — the defaults below are the recommended starting point
export GROQ_MODEL="openai/gpt-oss-20b"   # reliable tool calling on free tier
export GROQ_MAX_TOKENS=1024              # smaller cap eases free-tier TPM limits

python3 agent.py
```

Multi-line paste is supported — paste your prompt and any data together and it
will arrive as a single message:

```
you> 다음 JSON에서 HP가 100보다 큰 몬스터 이름을 뽑고, 그들의 평균 HP를 알려줘.
[
  {"name": "Slime",  "hp":  30},
  {"name": "Orc",    "hp": 120},
  {"name": "Dragon", "hp": 800},
  {"name": "Lich",   "hp": 250}
]

agent> Plan: parse the JSON, filter hp > 100, average those. Criterion: print
       qualifying names and their mean hp.

[tool: run_python]
agent> Orc, Dragon, Lich — 평균 HP 390.0.
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
`agent.py` posts to `https://api.groq.com/openai/v1/chat/completions` via
`urllib`. The control loop is a single `while` loop in `run_turn()` that drives
the model: send messages → receive `tool_calls` → execute tools → append `tool`
role results → repeat. This was a deliberate choice — agent abstractions hide
the very thing this exercise is about.

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

## Verified scenarios (2026-05-05)

Smoke-tested end-to-end on **`openai/gpt-oss-20b`** (Groq free tier, `GROQ_MAX_TOKENS=1024`).
All five scenarios passed.

**1. Regression — multi-line paste with inline JSON**

```
다음 JSON에서 HP가 100보다 큰 몬스터 이름을 뽑고, 그들의 평균 HP를 알려줘.
[
  {"name": "Slime",  "hp":  30},
  {"name": "Orc",    "hp": 120},
  {"name": "Dragon", "hp": 800},
  {"name": "Goblin", "hp":  45},
  {"name": "Lich",   "hp": 250}
]
```
Confirms the multi-line paste fix — the JSON arrives with the prompt as a single message instead of being split across several `input()` calls.

**2. Ambiguity → `ask_user`**

```
파일 정리 좀 해줘
```
The agent must call `ask_user` for clarification (which files / where / what kind of cleanup) instead of guessing or starting to write code.

**3. Simple computation → `run_python`**

```
1부터 1000까지 중에 7의 배수이면서 자릿수의 합이 짝수인 수의 개수를 세줘.
```
Single short script, single answer. Verifies the basic `run_python` loop and that the model doesn't over-engineer a one-shot task.

**4. Skill creation → `save_skill`**

```
"2026-05-05" 같은 ISO 날짜를 받아서 그 날이 그 해의 몇 번째 날인지 알려주는 기능 만들어줘. 앞으로 다른 날짜로도 자주 쓸 거야.
```
The "앞으로 자주 쓸 거" cue triggers a `save_skill` proposal (gated on y/n). Verifies the y/n confirmation path and persistence under `skills/day_of_year/`.

**5. Multi-step with file persistence**

```
workspace에 sample.csv 만들어줘 — 가짜 학생 10명 데이터(이름, 점수). 그다음 그 파일 읽어서 평균 점수 위/아래로 나눠서 두 그룹의 이름 출력.
```
Multiple `run_python` calls in one turn, with state passed via the workspace file (since each `run_python` is a fresh subprocess and memory does not persist).

**6. Open-ended planning / no-tool task**

```
카피바라가 배달하는 게임 시나리오 초안을 어떻게 설계하면 좋을지 알려줘.
```
A pure-knowledge question with no data to crunch and no file to touch. The
agent answered in plain markdown (an 8-step game-design methodology table)
without calling `run_python` or any other tool. Verifies that the agent
exercises restraint — the system prompt frames `run_python` as the primary
mechanism, but the model correctly judges when "talk it out" is the right
response mode and skips tool calls entirely. Complements test #2: together
they show the agent picks the right mode for the task (clarify, code, or
just answer).

**7. Multi-turn creative coding (planning → prototype → run instructions)**

Following directly on from test #6, two more turns extended the same
conversation:

```
you> 실제로 만들어볼래? python으로?
agent> [emits a complete pygame prototype — "Copy-Bara Delivery" — as a
       Python code block: keyboard-controlled capybara, item pickup with
       collision detection, delivery zone, full game loop]

you> 어떻게 실행해?
agent> [numbered run instructions: check Python version, pip install pygame,
       create the .py file, run it, key bindings, troubleshooting tip]
```

Verifies two things:

- **Mode-switching across turns.** The same agent pivots from advisory prose
  (#6) to producing a real, runnable prototype, to step-by-step operational
  instructions, all driven by short user nudges. No re-prompting or context
  reset needed.
- **Tool selection for code that can't run headless.** A pygame app needs
  an interactive display, so the agent correctly emitted the source as a
  code block in chat instead of trying to execute it via `run_python`
  (which runs in a sandboxed subprocess with no display attached). This is
  the right call — `run_python` is for code whose output is observable as
  stdout/stderr, not for GUI/interactive programs.

### Notes from the run

- **Skill reuse worked smoothly.** A skill saved in one turn (e.g. `day_of_year` from #4) was visible in the system prompt's `Saved skills` index on the next turn, and the model picked it up via `load_skill` rather than re-implementing.
- **Cross-session reuse is excellent — and discovery is purely description-based.**
  After restarting the agent, a fresh session was able to use `day_of_year` from
  a vague natural-language reference, with no path, file name, or skill name
  given by the user:

  ```
  you> 내가 아까 ISO 날짜를 받아서 그 날이 그 해의 몇 번째 날인지 알려주는
       기능을 만들었는데 한번 사용해볼 수 있을까?

  [tool: ask_user]
  agent asks> Please provide an ISO date string (YYYY-MM-DD) to test the
              day_of_year tool.
  you> 1994-09-19

  [tool: load_skill]
  [tool: run_python]
  agent> The ISO date **1994-09-19** is the **262nd** day of the year.
  ```

  This works because `load_skills_index()` reads each skill's `SKILL.md` at
  startup and injects only the *"when to use"* summaries into the system
  prompt. The model matches the user's intent against those summaries
  semantically, then calls `load_skill(name=...)` to fetch the source. The
  user never has to know where the skill lives on disk.
- In-session message history is *not* persisted across sessions by design; that's the role of skills. (Conversation persistence remains an open Tier-3 item below.)

## Known issues & roadmap

This section is a living changelog of what we've found, fixed, and want to
fix next. New findings should be appended in the same shape so future
sessions can pick up where the last one left off.

### Recently resolved (2026-05-05)

A first round of usability fixes addressed a cascade of bugs that surfaced
during the initial Groq integration. They look like model failures from
the outside — they are not.

| Symptom | Real cause | Fix |
|---|---|---|
| Agent keeps asking for JSON even after the user pasted it | `input()` reads only one line; the remaining lines stay buffered in stdin and get fed as empty answers to the next `ask_user` | `_read_user_input()` in `agent.py` drains buffered lines via `select` so a multi-line paste arrives as one message |
| Agent fabricates an answer when `ask_user` receives empty input | `tools.py` returned an ambiguous `"(user gave no answer)"` string | Returns an explicit `"USER PROVIDED NO INPUT — do not guess or fabricate"` signal |
| `429 (TPM)` aborts the session | `call_llm` `sys.exit`'d on any `HTTPError` | Up to 2 auto-retries; wait time parsed from Groq's `try again in Xs` body, capped at 30s |
| Free-tier TPM exhausts within a few turns | Full message history sent every call, growing without bound | `_trim_history` caps the history at 30 messages, cutting at user-message boundaries to avoid orphaning tool-result messages |

### Model gotchas

- **Tool-calling reliability varies by model on Groq.**  
  `llama-3.3-70b-versatile` (the original default) intermittently emits tool
  calls as plain text in Llama's native `<function=name{json}</function>`
  format instead of the structured `tool_calls` field. Groq's server-side
  parser then 400s with `tool_use_failed`. `openai/gpt-oss-20b` is the
  recommended replacement on the free tier.
- **Smaller is not better.** `llama-3.1-8b-instant` has a higher TPM ceiling
  but is too weak for an agent loop — it ignores inline data, asks
  redundant clarifying questions, and burns tokens iterating without
  progress. Reserve it for trivial single-shot tasks.
- **TPM is the dominant free-tier constraint.** Each call sends the entire
  history + tool schemas + system prompt; with `GROQ_MAX_TOKENS=4096`
  reserved for the response, a single request can consume more than 5k of
  an 8k/min budget. Lowering `GROQ_MAX_TOKENS` to ~1024 is the cheapest
  mitigation.

### Still open

- **Not a real sandbox.** `run_python` runs as the host user. For untrusted
  inputs, swap subprocess for a container (firecracker, nsjail, Docker exec).
- **History trim is message-count based, not token-aware.** A single huge
  tool result (e.g., a large file dump) can still blow context even under
  the 30-message cap. A token estimator + summarisation pass is the next
  step.
- **Hallucination guard is best-effort.** The "no input" signal is a string
  the model can still ignore. A harder guard would live in `run_turn()`:
  if the assistant tries to emit a final answer immediately after a NO-INPUT
  tool result, refuse and force another `ask_user`.
- **System prompt favours long preambles.** "THINK BEFORE CODING" produces
  multi-paragraph rationales every turn that eat TPM budget. Tightening the
  prompt to "one sentence assumption + criterion, then act" is a Tier-3
  follow-up.
- **No streaming.** Each response is awaited in full.
- **No conversation persistence.** Session state is lost on quit.
- **`load_skill` returns source text, not a callable.** Progressive-disclosure
  compromise; a registered-tool-per-skill model would be cleaner at the cost
  of prompt tokens.
- **Python-only skills.** The pattern would work for shell or Node; not wired up.
- **No automated tests.** A pytest suite mocking `call_llm` and asserting the
  dispatch layer would harden refactors.

### Improvement tiers (planned)

Use these tiers as a triage guide when picking up new work.

- **Tier 1 — must-have for usability.** *Satisfied by the four fixes above.*
- **Tier 2 — stability & safety.**
  - Token-aware history compaction (summariser fallback when trim isn't enough)
  - Real sandbox for `run_python`
  - Model fallback chain (auto-downgrade on persistent 429)
  - Stronger hallucination guard in `run_turn` itself
- **Tier 3 — quality & polish.**
  - Tighter system prompt (less preamble per turn)
  - Streaming responses
  - pytest suite over the dispatch layer
  - Conversation persistence across sessions
  - Multi-language skills (shell/Node)

## License

MIT.
