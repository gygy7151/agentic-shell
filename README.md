# Agentic Shell

자연어로 들어오는 임의의 작업을, Python 도구를 즉석에서 작성·실행해 해결하는 작은 CLI 에이전트입니다. 만들어진 도구가 재사용 가치가 있다고 판단되면, 사용자 승인을 거쳐 로컬 `skills/` 디렉토리에 저장되고, 이후 모든 세션에서 에이전트가 이를 인식합니다.

전체 컨트롤 루프는 표준 라이브러리 Python으로 손수 작성됐습니다 — 에이전트 라이브러리도, SDK도 없고, Groq의 OpenAI 호환 Chat Completions API에 `urllib`로 한 번 호출할 뿐.

## 빠른 시작

```bash
# 요구사항: Python 3.10+, Groq API 키 (free tier로 충분)
export GROQ_API_KEY=gsk_...

# (선택) 기본값 — 아래 조합이 권장 시작점
export GROQ_MODEL="openai/gpt-oss-20b"   # free tier에서 안정적인 tool calling
export GROQ_MAX_TOKENS=1024              # free tier TPM 한도 압박을 줄여줌

python3 agent.py
```

멀티라인 paste를 지원합니다 — 프롬프트와 데이터를 한 번에 붙여넣으면 단일 메시지로 들어갑니다:

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

## 프로젝트 구조

```
agent.py        메인 루프 — API를 구동하는 단일 while 루프
tools.py        내장 도구 스키마 + 디스패치
prompts.py      4가지 운영 원칙이 담긴 시스템 프롬프트
skills/         사용자 승인을 거쳐 저장된 도구들 (스킬당 SKILL.md + tool.py)
workspace/      run_python 호출용 임시 디렉토리
```

### 내장 도구

| 도구          | 용도                                                          |
| ------------- | ------------------------------------------------------------- |
| `run_python`  | 격리된 서브프로세스에서 Python 스크립트 실행                  |
| `read_file`   | UTF-8 텍스트 파일 읽기                                        |
| `write_file`  | workspace에 파일 쓰기                                         |
| `ask_user`    | 명확화 질문 (HITL)                                            |
| `save_skill`  | 생성된 도구 영속화 — 항상 y/n 확인 프롬프트로 게이팅          |
| `load_skill`  | 저장된 스킬의 소스 코드 가져오기                              |
| `list_skills` | 저장된 모든 스킬과 "언제 쓸지" 노트의 인덱스                  |

## 설계 결정

**SDK도, 프레임워크도 없는 직접 HTTP.**
`agent.py`는 `urllib`로 `https://api.groq.com/openai/v1/chat/completions`에 직접 POST합니다. 컨트롤 루프는 `run_turn()` 안의 단일 `while` 루프로 모델을 구동합니다: 메시지 전송 → `tool_calls` 수신 → 도구 실행 → `tool` role 결과 추가 → 반복. 의도적인 선택입니다 — 에이전트 추상화는 이 연습이 다루고자 하는 바로 그 부분을 가려버리니까요.

**서브프로세스 격리 실행.**
`run_python`은 모델이 작성한 코드를 `workspace/_run.py`에 쓰고, 새 `python3` 서브프로세스에서 하드 타임아웃과 함께 실행합니다. 가장 단순하면서 동작하는 격리 방식입니다 — 호출 간에 프로세스 상태가 새지 않고, stdout/stderr이 깔끔하게 분리되며, 모델은 실제 traceback을 `tool_result` 콘텐츠로 받아옵니다. 이게 자기 수정(self-correction)이 가능한 이유입니다.

**스킬은 코드가 아닌 데이터로.**
저장된 스킬은 두 파일을 가진 디렉토리:
- `SKILL.md` — "언제 쓸지" 한 단락 노트
- `tool.py` — 실제 코드

시작 시, 에이전트는 모든 `SKILL.md`를 읽어 *요약*만 (소스는 X) 시스템 프롬프트에 주입합니다. 전체 소스는 에이전트가 그 스킬을 쓰겠다고 결정한 시점에만 `load_skill`을 통해 로드됩니다. 스킬 라이브러리가 커져도 프롬프트가 작게 유지됩니다 — 모델은 메뉴를 보고, 레시피를 가져오는 구조.

**3개의 명시적 human-in-the-loop 접점.**
1. 작업이 모호하거나 입력이 빠졌을 때마다 `ask_user`.
2. `save_skill`은 항상 미리보기를 출력하고 y/n 확인을 받음.
3. 사용자가 매 작업을 직접 `you>` 프롬프트에서 입력.

시스템 프롬프트는 추측보다 `ask_user`를 선호하라고 모델에 명시적으로 지시합니다.

**Traceback 피드백을 통한 자기 수정.**
사용자 턴당 최대 12회 모델 반복. `run_python`이 0이 아닌 코드로 종료되면, 전체 STDOUT/STDERR/exit-code가 다음 `tool_result`로 돌아갑니다. 시스템 프롬프트는 모델에게 traceback을 읽고 *실제* 원인을 고치고 다시 실행하라고 지시합니다 — 추측으로 패치하지 말고.

**시스템 프롬프트의 4가지 운영 원칙.**
모델은 다음을 따르도록 지시받습니다:
1. 코드를 작성하기 전에 가정과 성공 기준을 명시.
2. 코드는 최소한으로 — 예측에 기반한 기능 추가 금지.
3. 저장된 스킬을 수정할 때는 외과 수술적으로(surgical).
4. 성공 기준을 검증 가능한 테스트로 취급하고, 통과할 때까지 반복.

이 원칙들은 `prompts.py`에 새겨져 있습니다. LLM이 흔히 빠지는 과잉 설계 경향을 막고, 생성을 작업에 집중시키는 주된 레버입니다.

## 검증된 시나리오 (2026-05-05)

**`openai/gpt-oss-20b`** (Groq free tier, `GROQ_MAX_TOKENS=1024`)에서 end-to-end로 스모크 테스트했습니다. 7개 시나리오 모두 통과.

**1. 회귀 테스트 — 인라인 JSON과 멀티라인 paste**

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
멀티라인 paste 수정을 검증합니다 — JSON이 여러 `input()` 호출로 쪼개지지 않고 프롬프트와 함께 단일 메시지로 도달합니다.

**2. 모호성 → `ask_user`**

```
파일 정리 좀 해줘
```
에이전트는 추측하거나 코드를 쓰기 시작하지 않고, 명확화를 위해 `ask_user`를 호출해야 합니다 (어떤 파일 / 어디에 / 어떤 종류의 정리).

**3. 단순 계산 → `run_python`**

```
1부터 1000까지 중에 7의 배수이면서 자릿수의 합이 짝수인 수의 개수를 세줘.
```
짧은 스크립트 한 번, 답 한 번. 기본 `run_python` 루프와, 모델이 일회성 작업을 과잉 설계하지 않는다는 점을 검증합니다.

**4. 스킬 생성 → `save_skill`**

```
"2026-05-05" 같은 ISO 날짜를 받아서 그 날이 그 해의 몇 번째 날인지 알려주는 기능 만들어줘. 앞으로 다른 날짜로도 자주 쓸 거야.
```
"앞으로 자주 쓸 거"라는 단서가 `save_skill` 제안을 트리거합니다 (y/n로 게이팅). y/n 확인 경로와 `skills/day_of_year/` 아래 영속화를 검증합니다.

**5. 멀티스텝 + 파일 영속화**

```
workspace에 sample.csv 만들어줘 — 가짜 학생 10명 데이터(이름, 점수). 그다음 그 파일 읽어서 평균 점수 위/아래로 나눠서 두 그룹의 이름 출력.
```
한 턴 안에서 여러 번의 `run_python` 호출, 그리고 workspace 파일을 통한 상태 전달 (각 `run_python`은 새 서브프로세스이고 메모리는 유지되지 않음).

**6. 열린 기획 / 도구 미사용 작업**

```
카피바라가 배달하는 게임 시나리오 초안을 어떻게 설계하면 좋을지 알려줘.
```
계산할 데이터도, 만질 파일도 없는 순수 지식 질문. 에이전트는 `run_python`이나 다른 도구를 호출하지 않고 plain markdown으로 답변(8단계 게임 디자인 방법론 표)했습니다. 시스템 프롬프트는 `run_python`을 1차 메커니즘으로 제시하지만, 모델은 "그냥 답하기"가 적절한 모드인 시점을 정확히 판단해 도구 호출을 생략합니다 — 이 절제(restraint)를 검증합니다. 테스트 #2와 짝을 이뤄, 에이전트가 작업에 맞는 모드(명확화 / 코딩 / 그냥 답하기)를 고른다는 점을 보여줍니다.

**7. 멀티턴 창작 코딩 (기획 → 프로토타입 → 실행 안내)**

테스트 #6에서 이어진 두 턴이 같은 대화를 확장합니다:

```
you> 실제로 만들어볼래? python으로?
agent> [완전한 pygame 프로토타입을 — "Copy-Bara Delivery" — Python 코드 블록으로
       생성: 키보드로 조작하는 카피바라, 충돌 감지가 포함된 아이템 픽업,
       배달 지점, 전체 게임 루프]

you> 어떻게 실행해?
agent> [번호 매긴 실행 안내: Python 버전 확인, pip install pygame, .py 파일 생성,
       실행, 키 바인딩, 트러블슈팅 팁]
```

두 가지를 검증합니다:

- **턴 사이의 모드 전환.** 같은 에이전트가 자문성 산문(#6) → 실제 실행 가능한 프로토타입 → 단계별 실행 안내로 자연스럽게 전환됩니다. 짧은 사용자 nudge만으로 구동되며 재프롬프팅이나 컨텍스트 리셋이 필요하지 않습니다.
- **headless로 실행할 수 없는 코드의 도구 선택.** pygame 앱은 인터랙티브 디스플레이가 필요하므로, 에이전트는 `run_python`(디스플레이 없는 격리 서브프로세스)으로 실행을 시도하지 않고 채팅에 코드 블록으로 출력했습니다. 올바른 판단입니다 — `run_python`은 stdout/stderr로 결과를 관찰할 수 있는 코드용이지, GUI/인터랙티브 프로그램용이 아닙니다.

### 실행에서 얻은 메모

- **스킬 재사용이 매끄러웠음.** 한 턴에서 저장한 스킬(예: #4의 `day_of_year`)이 다음 턴 시스템 프롬프트의 `Saved skills` 인덱스에 보였고, 모델은 다시 구현하지 않고 `load_skill`로 가져다 썼습니다.
- **세션 간 재사용이 우수 — 그리고 발견(discovery)이 순수하게 description 기반.**
  에이전트를 재시작한 뒤, 새 세션이 사용자가 경로/파일명/스킬명을 전혀 주지 않은 모호한 자연어 참조만으로 `day_of_year`를 사용할 수 있었습니다:

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

  이게 가능한 이유는 `load_skills_index()`가 시작 시 각 스킬의 `SKILL.md`를 읽어 *"when to use"* 요약만 시스템 프롬프트에 주입하기 때문입니다. 모델이 사용자의 의도를 그 요약과 의미적으로 매칭한 뒤 `load_skill(name=...)`로 소스를 가져옵니다. 사용자는 스킬이 디스크 어디에 있는지 알 필요가 없습니다.
- 세션 내 메시지 히스토리는 *의도적으로* 세션 간에 영속되지 않습니다 — 그건 스킬의 역할입니다. (대화 영속화는 아래 Tier-3 미해결 항목으로 남아 있습니다.)

## 알려진 이슈 & 로드맵

이 섹션은 발견되고 고쳐진 것, 그리고 다음에 고쳐야 할 것을 기록하는 살아있는 changelog입니다. 새 발견은 같은 형태로 추가해서, 다음 세션이 이전 세션이 멈춘 자리에서 이어 일할 수 있게 합니다.

### 최근 해결됨 (2026-05-05)

초기 Groq 통합 과정에서 드러난 일련의 버그를 첫 번째 라운드의 사용성 수정에서 처리했습니다. 겉으로는 모델 실패처럼 보였지만 실제로는 에이전트 측 버그였습니다.

| 증상 | 실제 원인 | 수정 |
|---|---|---|
| 사용자가 JSON을 paste했는데도 에이전트가 계속 JSON을 요청 | `input()`은 한 줄만 읽음; 나머지 줄은 stdin에 버퍼로 남았다가 다음 `ask_user`에 빈 답변으로 들어감 | `agent.py`의 `_read_user_input()`이 `select`로 버퍼를 비워서 멀티라인 paste가 단일 메시지로 도달 |
| `ask_user`가 빈 입력을 받으면 에이전트가 답을 만들어냄(환각) | `tools.py`가 모호한 `"(user gave no answer)"` 문자열을 반환했었음 | 명시적인 `"USER PROVIDED NO INPUT — do not guess or fabricate"` 시그널 반환 |
| `429 (TPM)`이 세션을 종료시킴 | `call_llm`이 모든 `HTTPError`에 대해 `sys.exit` | 최대 2회 자동 재시도; Groq의 `try again in Xs` 본문에서 대기 시간 파싱, 30초 상한 |
| Free-tier TPM이 몇 턴 만에 소진 | 매 호출마다 전체 메시지 히스토리를 보내고, 무한정 증가 | `_trim_history`가 히스토리를 20 메시지로 제한(env: `GROQ_HISTORY_CAP`); tool-result 메시지가 고립되지 않도록 user-message 경계에서 잘라냄 |

### 모델 함정(gotchas)

- **Groq의 모델별 tool-calling 신뢰도 차이.**
  `llama-3.3-70b-versatile`(원래 기본값)은 가끔 구조화된 `tool_calls` 필드 대신 Llama의 네이티브 `<function=name{json}</function>` 평문 포맷으로 도구 호출을 발행합니다. 그러면 Groq의 서버측 파서가 `tool_use_failed`로 400을 던집니다. Free tier에서는 `openai/gpt-oss-20b`가 권장 대체.
- **작은 모델이 답이 아님.** `llama-3.1-8b-instant`는 TPM 상한이 더 높지만 에이전트 루프에는 너무 약합니다 — 인라인 데이터를 무시하고, 중복 질문을 던지고, 진전 없이 토큰만 소진합니다. 단순한 일회성 작업에만 쓰세요.
- **Free tier에서는 TPM이 지배적인 제약.** 매 호출이 전체 히스토리 + 도구 스키마 + 시스템 프롬프트를 전송하고, 응답용으로 `GROQ_MAX_TOKENS=4096`이 예약되면 단일 요청이 8k/분 예산의 5k 이상을 소비할 수 있습니다. `GROQ_MAX_TOKENS`를 ~1024로 낮추는 것이 가장 저렴한 완화책.

### 미해결

- **진짜 샌드박스가 아님.** `run_python`은 호스트 사용자 권한으로 실행됩니다. 신뢰할 수 없는 입력을 다루려면 서브프로세스를 컨테이너(firecracker, nsjail, Docker exec)로 교체해야 합니다.
- **히스토리 트림이 메시지 수 기반, 토큰 인식 기반이 아님.** 거대한 단일 도구 결과(예: 큰 파일 덤프)는 20-메시지 cap 안에서도 컨텍스트를 터뜨릴 수 있습니다. 토큰 추정기 + 요약 패스가 다음 단계.
- **환각 가드는 best-effort.** "no input" 시그널은 모델이 무시할 수 있는 문자열입니다. 더 강한 가드는 `run_turn()`에 들어가는 형태가 됩니다 — assistant가 NO-INPUT 도구 결과 직후에 최종 답변을 내려고 하면 거부하고 다른 `ask_user`를 강제.
- **시스템 프롬프트가 긴 prelude를 유도.** "THINK BEFORE CODING"이 매 턴 여러 단락의 근거를 만들어 TPM 예산을 잡아먹습니다. "한 문장 가정 + 기준, 그 다음 행동"으로 프롬프트를 조이는 것은 Tier-3 후속 작업.
- **스트리밍 없음.** 매 응답을 끝까지 기다림.
- **대화 영속화 없음.** 종료 시 세션 상태가 사라짐.
- **`load_skill`은 호출 가능한 객체가 아니라 소스 텍스트를 반환.** 점진적 노출(progressive disclosure) 절충안 — 스킬당 도구를 등록하는 모델이 더 깔끔하지만 프롬프트 토큰 비용이 큼.
- **Python 전용 스킬.** 패턴은 shell이나 Node에서도 동작하겠지만 연결돼 있지 않음.
- **자동 테스트 없음.** `call_llm`을 mock하고 디스패치 레이어를 검증하는 pytest 스위트가 리팩토링을 안전하게 만들 것.

### 개선 계층 (계획)

새 작업을 시작할 때 트리아지 가이드로 사용:

- **Tier 1 — 사용성 필수.** *위 4개 수정으로 충족됨.*
- **Tier 2 — 안정성 & 안전성.**
  - 토큰 인식 히스토리 압축 (트림으로 부족할 때 요약 fallback)
  - `run_python`을 위한 진짜 샌드박스
  - 모델 fallback 체인 (지속적인 429 시 자동 다운그레이드)
  - `run_turn` 자체에서의 더 강한 환각 가드
- **Tier 3 — 품질 & 다듬기.**
  - 더 짧은 시스템 프롬프트 (턴당 prelude 줄이기)
  - 스트리밍 응답
  - 디스패치 레이어 위의 pytest 스위트
  - 세션 간 대화 영속화
  - 다국어 스킬 (shell/Node)

## 라이선스

MIT.
