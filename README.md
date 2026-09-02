# MyViking

**프로젝트별로 다르게 학습하는 개인 Jarvis 컨텍스트 데이터베이스.**

[volcengine/OpenViking](https://github.com/volcengine/OpenViking) 의 세 가지 아이디어를
가져왔습니다.

| OpenViking 개념 | MyViking 구현 |
|---|---|
| 컨텍스트를 가상 파일시스템으로 (`viking://`) | `jarvis://` — 사람이 읽고 편집할 수 있는 마크다운 파일이 진실의 원천 |
| L0/L1/L2 티어 로딩 | 모든 노드가 요약(~100t)·개요(~2000t)·전문 3단으로 저장, 검색 시 티어 선택 |
| 세션 → 장기 메모리 증류 | `commit` 시 자동 증류, 이름이 안정적인 메모리 파일에 누적 |

여기에 두 가지를 더했습니다.

1. **프로젝트별 메모리 스키마.** 코드 프로젝트와 리서치 프로젝트는 *다른 종류의*
   기억이 필요합니다. 프로젝트마다 `profile.yaml` 로 카테고리·추출 기준·보관
   정책·토큰 예산을 따로 정의하고, 그 파일을 바꾸면 학습 동작이 바뀝니다.
2. **측정된 토큰 회계.** "절감했다"는 주장을 검증할 수 있게, 캐시 절감과 티어링
   절감을 분리해 SQLite 에 기록합니다. 기준선은 가정이 아니라 *같은 항목을 전문으로
   실었을 때의 실측값*입니다.

LLM 없이도 완전히 동작합니다(`provider: none`). 모델을 붙이면 요약과 증류 품질만
올라가고, 저장·검색·캐시·예산은 동일하게 동작합니다.

---

## 30초 시작

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[dev,server]"
export PATH="$PWD/.venv/bin:$PATH"

jv init myapp -t coding -d "내 앱 백엔드"

# 반복하는 지시문은 저장해 두고 변수만 채워 씁니다
jv prompt save -p myapp bugfix "증상: {{symptom}}
관련 파일: {{files}}

원인을 한 줄로 설명한 뒤 최소 변경으로 고쳐줘."

# 매번 다시 설명하던 사실을 기억시킵니다
jv mem add -p myapp commands "테스트 실행" "pytest -q 로 전체 테스트를 돌린다" \
   --detail "루트에서 실행. PYTHONPATH=src 필요"

# 요청 준비: 캐시 확인 → 예산 안에서 컨텍스트 조립
jv ask -p myapp "테스트 어떻게 돌려?"

# 답변을 기록하면 다음부터 이 질문은 0 토큰
jv commit -p myapp "테스트 어떻게 돌려?" "루트에서 pytest -q 를 실행하세요." \
   --tokens-in 1200 --tokens-out 40

jv ask -p myapp "테스트 어떻게 돌려?"    # [캐시 적중 · 1240 토큰 절약]
jv report -p myapp                        # 누적 절감 회계
```

## 토큰이 어디서 줄어드나

세 가지 경로가 있고, 각각 따로 계상됩니다.

**1. 답변 캐시 (가장 큰 절감).** 같은 질문 — 또는 표현만 다른 질문 — 은 모델에
보내지 않습니다. 정확 일치는 정규화된 해시로, 유사 질문은 질문 벡터의 코사인
유사도가 임계값(`budget.cache_hit_threshold`, 기본 0.92)을 넘을 때 적중합니다.
적중 시 유사도와 원 질문을 함께 보여주므로, 재사용된 근거를 눈으로 확인할 수 있습니다.

**2. 티어링.** 검색은 많은 노드의 L0 를 읽고 아주 적은 노드의 L2 만 읽습니다.
어떤 노드를 넣을지와 *얼마나 깊게* 읽을지를 (점수/토큰) 밀도 기준 그리디
knapsack 으로 함께 풉니다. 예산은 약속이므로, 렌더링된 실제 텍스트를 측정해
초과하면 점수가 낮은 항목부터 잘라냅니다.

**3. 증류.** 세션 20건을 매번 싣는 대신, 재사용 가치가 있는 부분만 메모리 파일로
압축합니다. 안 쓰이는 메모리는 신뢰도가 감쇠하고 보관함으로 이동합니다 — 무한히
자라는 메모리 저장소는 그 자체로 토큰 세금입니다.

```
$ jv ask -p backend "결제 승인 실패 시 재시도와 웹훅 서명은 어떻게 처리해?" 2>&1 >/dev/null
사용 1264 / 동일 항목 전체 로드 5583 / 관련 전량 덤프 5583 토큰 · 절감 77.4%
  L1  1031t  jarvis://projects/backend/resources/pg-spec
  L1    45t  jarvis://projects/backend/memories/architecture/결제-흐름
  L1    38t  jarvis://projects/backend/memories/commands/테스트-실행
```

절감률은 코퍼스에 달려 있습니다. 작은 메모리 몇 개만 있으면 L1 과 L2 가 같아서
절감이 0% 로 나오는 것이 정상입니다 — 티어링이 버는 것은 긴 문서에서 나오고,
캐시가 버는 것은 반복 질문에서 나옵니다. `examples/quickstart.sh` 가 두 경우를
모두 보여줍니다.

## 프로젝트마다 다른 기억

`jv init` 의 `-t` 로 스키마를 고릅니다. 이후 `$JARVIS_HOME/projects/<이름>/profile.yaml`
을 직접 편집해도 됩니다.

| 템플릿 | 카테고리 |
|---|---|
| `default` | preferences, facts, patterns, cases |
| `coding` | conventions, architecture, commands, pitfalls, decisions, cases |
| `research` | questions, findings, sources, contradictions |
| `writing` | voice, audience, outline, phrasing |
| `ops` | topology, runbooks, incidents, thresholds |

각 카테고리는 다음을 정의합니다.

```yaml
- name: commands
  description: 빌드·테스트·배포 명령
  extract: 실제로 동작이 확인된 명령과 전제 조건. 추측한 명령은 저장 금지.
  priority: 9          # 검색 예산 경쟁에서의 우선순위
  keep: 20             # 이 수를 넘으면 약한 것부터 보관함으로
  budget_share: 0.15   # 메모리 예산 중 최소 확보 비율
  cumulative: true     # true = 같은 주제는 한 파일에 누적, false = 건별 파일
```

`extract` 는 증류기에게 그대로 전달되는 지시문입니다. 같은 세션이라도 프로파일이
다르면 다른 카테고리에 다른 형태로 저장됩니다. `fallback` 은 어떤 규칙에도
걸리지 않은 관찰이 갈 곳입니다 — 이게 없으면 학습 루프가 도는 것처럼 보이면서
아무것도 쌓이지 않습니다.

전역 스코프(`jarvis://global`)는 모든 프로젝트에 적용되는 선호를 담습니다.
프로젝트 메모리는 서로 섞이지 않습니다.

## 저장 구조

```
$JARVIS_HOME/                      # 기본 ~/.jarvis
├── jarvis.yaml                    # 설정
├── index.db                        # SQLite 색인 (파생물, jv reindex 로 재생성)
├── global/
│   ├── memories/preferences/*.md
│   └── prompts/*.md
└── projects/myapp/
    ├── profile.yaml                # 이 프로젝트의 메모리 스키마
    ├── memories/{카테고리}/*.md
    ├── prompts/*.md
    │   └── _versions/{이름}/v1.md   # 덮어쓸 때 자동 보관
    ├── sessions/{날짜}/*.md
    ├── resources/*.md
    └── _archive/…                  # 감쇠·캡·피드백으로 밀려난 것 (되돌릴 수 있음)
```

메모리 파일 하나:

```markdown
---
uri: jarvis://projects/myapp/memories/commands/테스트-실행
kind: memory
title: 테스트 실행
category: commands
abstract: pytest -q 로 전체 테스트를 돌린다
confidence: 0.92
hits: 4
sources:
- jarvis://projects/myapp/sessions/2026-09-02/테스트-어떻게-돌리지-274af010
---

## Overview
pytest -q 로 전체 테스트를 돌린다

## Details
루트에서 실행. PYTHONPATH=src 필요

## Observations
- 2026-09-02T01:47:38+00:00: 루트에서 pytest -q 를 실행
```

**파일이 진실의 원천입니다.** 왜 그렇게 답했는지 궁금하면 파일을 열어 보면 됩니다.
`index.db` 는 언제든 버리고 `jv reindex` 로 다시 만들 수 있습니다.

## Claude Code 에 붙이기

MCP 서버가 포함되어 있습니다(SDK 의존성 없음, stdio JSON-RPC 직접 구현).

```bash
claude mcp add myviking -- /절대경로/.venv/bin/python -m jarvis.mcp_server
```

도구 6개만 노출합니다 — 컨텍스트 도구가 스무 개면 에이전트가 선택에 주의를 씁니다.

| 도구 | 용도 |
|---|---|
| `jarvis_context` | 작업 시작 시 호출. 캐시 확인 + 예산 내 컨텍스트 |
| `jarvis_remember` | 다음에도 쓸 지식 기록 |
| `jarvis_commit` | 작업 종료 시 호출. 세션 기록 + 증류 |
| `jarvis_browse` | `ls`/`tree`/`find`/`grep`/`read` |
| `jarvis_prompt` | 저장된 프롬프트 목록/렌더 |
| `jarvis_profile` | 프로파일·프로젝트·절감 리포트 |

`CLAUDE.md` 에 이렇게 적어 두면 루프가 자동으로 돕니다.

```markdown
이 저장소에서 작업할 때:
1. 시작 전 `jarvis_context` 를 project="myapp" 으로 호출해 누적 컨텍스트를 받는다.
2. 새로 알게 된 규칙·명령·함정은 `jarvis_remember` 로 기록한다.
3. 작업을 마치면 `jarvis_commit` 으로 질문과 결과를 남긴다.
```

## Python API

```python
from jarvis import Jarvis

j = Jarvis()
j.init_project("myapp", template="coding")

prepared = j.prepare("myapp", "테스트 어떻게 돌려?")

if prepared.cache_hit:
    answer = prepared.cache_hit.answer          # 입력 토큰 0
else:
    answer = my_llm(prepared.messages)          # 예산이 이미 맞춰진 요청
    print(f"절감 {prepared.packed.saved_ratio:.0%}")

j.commit("myapp", "테스트 어떻게 돌려?", answer)  # 자가학습 투입
```

`prepare` 는 무엇을 요청에 *넣을지* 결정하고, `commit` 은 요청에서 무엇을 *남길지*
결정합니다. 나머지 모듈은 전부 이 둘을 위해 존재합니다.

## HTTP API

```bash
jv serve --port 8787          # 기본 127.0.0.1 바인딩
curl localhost:8787/docs      # OpenAPI 문서
```

주요 엔드포인트: `POST /prepare`, `POST /commit`, `POST /memories`,
`POST /prompts/render`, `GET /find`, `GET /report`, `PUT /projects/{p}/profile`.

## 모델 붙이기 (선택)

```bash
jv config --set llm.provider=anthropic \
          --set llm.model=claude-sonnet-5 \
          --set llm.api_key_env=ANTHROPIC_API_KEY
export ANTHROPIC_API_KEY=...
```

`anthropic` / `openai` / `volcengine` / `ollama` 를 지원합니다. 임베딩도 같은
방식으로 교체할 수 있고(`embed.provider`), 기본값은 오프라인 해시 임베딩입니다.
해시 임베딩은 의미 모델이 아니지만 이 시스템이 벡터를 실제로 쓰는 두 가지 일 —
중복 질문 탐지와 동일 지식 병합 — 에는 충분하며, 문자 n-gram 이라 한국어를
토크나이저 없이 처리합니다.

## 명령어

```
jv init <프로젝트> -t <템플릿>        프로젝트 생성
jv project list|profile|templates    프로젝트와 메모리 스키마
jv prompt save|list|show|render|versions|rollback|delete
jv mem add|list|forget|feedback       메모리 직접 조작
jv resource add                       참고 자료를 티어링해 등록
jv ask -p <프로젝트> "<질문>"         캐시 확인 + 컨텍스트 조립
jv commit -p <프로젝트> "<질문>" "<답변>"
jv distill -p <프로젝트>              미증류 세션 반영 + 감쇠
jv ls|tree|find|grep|read             저장소 탐색
jv report|stats|cache|sessions        토큰 회계
jv reindex|config|serve
```

`jv find --trace` 는 검색이 어느 디렉터리로 들어갔고 왜 그 결과가 나왔는지
경로를 보여줍니다.

## 개발

```bash
uv pip install --python .venv -e ".[dev,server]"
.venv/bin/python -m pytest -q        # 126 tests
```

## 설계 노트

- **디렉터리 우선 검색.** 디렉터리 중심점(descendant 벡터 평균)을 먼저 점수화하고
  상위 몇 개에만 들어갑니다. L0 읽기 횟수가 코퍼스 크기가 아니라 깊이에 비례합니다.
- **안정적인 파일명.** OpenViking 은 메모리를 `mem_{uuid}.md` 로 씁니다
  ([RFC #1251](https://github.com/volcengine/OpenViking/issues/1251) 에서 논의 중).
  세션을 넘어 참조할 수 없으면 누적 메모리가 쌓일 자리가 없기 때문에, 여기서는
  제목에서 파일명을 만들고 같은 제목은 같은 파일에 누적합니다.
- **상충은 덮어쓰지 않습니다.** 같은 주제인데 부정 극성이 다른 관찰이 들어오면
  병합하되 `⚠ 기존 내용과 상충` 을 남기고 `extra.conflict` 에 양쪽을 보존합니다.
- **잊는 것도 기능.** 감쇠·보관 캡·부정 피드백으로 밀려난 메모리는 삭제가 아니라
  `_archive/` 로 이동합니다. 감쇠로 사라진 메모리는 프로젝트가 다시 살아날 때
  필요해지는 경우가 많습니다.
- **한국어 토큰 계상.** `len/4` 규칙은 한글을 2.5배쯤 과소 계상하므로, 스크립트를
  구분해 추정합니다. tiktoken 이 설치되어 있으면 그것을 씁니다.

라이선스: MIT
