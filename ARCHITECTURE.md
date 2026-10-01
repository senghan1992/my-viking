# myviking 아키텍처 — 사람별 프로젝트 지식 도서관 (서기 agent 구조)

> **한 줄 요약** — 여러 사람이 가입해 프로젝트 도서관을 만들고, pi 세션은 작업의 **관찰**만
> 남긴다. **서기 agent**(별도 pi 세션)가 관찰함을 읽고 재발 실수·반복 요청만 골라 지식으로
> 등재하고, 그 지식은 다음 세션에 다시 주입되어 적응한다. 서버는 저장고·검색·반복 카운터일 뿐,
> 지식을 **추출하지 않는다.**

## 1. 계층 구조

```
작업 세션(pi/omp·Claude Code·jcode·MCP·셸)     서버 (FastAPI + SQLite)              서기 세션 (별도 pi)
   │ brief/prepare — 지식 읽기                    ┌───────────────────────────┐        │ /inbox — 관찰 읽기
   ├─observe──────────────────────────────────▶   │ 관찰함 observations        │  ◀─────┤ /remember — 등재
   │  (질문·답·파일·오류·트랜스크립트 경로)          │ 반복 지문 계산 (모델 안 씀)   │        │ /inbox/ack — 처리 통보
   │                                              │ 지식 memories · sessions   │        │ (필요하면 세션 원문 read)
   └───────────────────────────────────────────   │ 검색·주입·신뢰 상태(trust)   │  ─────▶│ 브리핑으로 되돌아온다
                                                   └───────────────────────────┘
```

| 층 | 무엇 | 무엇을 하는가 / 무엇을 하지 않는가 |
|---|---|---|
| 작업 세션 확장 | `jv` 가 까는 pi 허브 확장 | 관찰을 올리고, 브리핑·관련 지식을 주입받는다. **판단하지 않는다.** |
| Agent API | `routes/agent.py` | 적재·검색·등재·채점. **증류하지 않는다** (`auto_distill` 레거시만). |
| 관찰 엔진 | `engine/observe.py` | 지문·반복 클러스터·작업함·선점·처리통보·실행장. 결정론적 계산만. |
| 등재 | `engine/distill.py` | 같은 제목 갱신+`occurrences` 누적, 교정(superseded), L0/L1/L2 생성. |
| 적응 | `engine/trust.py` | good→확립, bad→검증 필요, evidence·브리핑 섹션. |
| 검색 | `engine/retrieve.py` | 키워드(+선택 임베딩) 점수, 티어 팩킹, 관련 없으면 빈 책. |
| 서기 agent | `jv secretary` + `secretary.md` | 관찰함→판단→등재→통보. **모델 연결은 내 pi 를 재사용** (서버에 키 불필요). |

## 2. 데이터 모델 (SQLite 가 원본)

```
users(id, email, pw_hash, role[admin|user], disabled)
 └─ projects(id, user_id, slug, name, description, auto_distill)   ← 레거시 증류 스위치
     ├─ api_keys(id, project_id, user_id, name, key_hash, key_prefix, revoked_at)
     ├─ memories(id, project_id, category, title, summary[L0], overview[L1], content[L2],
     │          status, trust, keywords, source[manual|secretary|session],
     │          corrects, superseded_by, session_ref,
     │          occurrences, last_seen_at,           ← 반복 요청 누적 (서기가 채운다)
     │          evidence[], embedding)
     ├─ observations(id, project_id, session_id, agent, turn,
     │          kind[prompt|reply|error|edit|note|decision|request],
     │          text, norm, hits, files, transcript,  ← 세션 공유: 원문 파일 위치
     │          state[open|claimed|filed|skipped], memory_id, worker, note,
     │          created_at, processed_at)
     ├─ secretary_runs(id, project_id, agent, started_at, finished_at,
     │          found, filed, merged, skipped, report)   ← 서기 업무 장부 (감사 추적)
     ├─ sessions(id, project_id, agent, started_at, ended_at, question_count, transcript)
     └─ events(id, project_id, kind[session|memory|key|project|secretary|trace], detail)
```

- 키 인증은 SHA-256 해시로만 판단합니다. 평문은 `VIKING_SECRET` 으로 봉인(seal_api_key, HMAC-CTR +
  encrypt-then-MAC)해 `api_keys.key_secret` 에 두며, 프로젝트 주인의 로그인된 연결 탭에서만
  풀립니다(폐기된 키는 풀지 않음). DB 백업 단독으로는 평문이 나오지 않습니다.
- 지식 URI: `viking://{project_id}/memories/{category}/{id}`
- 관찰은 원문을 `MAX_TEXT(3000)` 로 잘라 보관합니다. 전체 트랜스크립트는 에이전트 머신의
  `~/.pi/agent/sessions/**.jsonl` 에 있고, 서기가 같은 머신에서 직접 `read` 합니다.

## 3. 두 개의 루프

### 3a. 작업 세션 루프 (판단 없음)

```
session_start   → GET  /brief     — 확립 지식·검증 필요·서기 대기 주입 (+ 세션 행 확보)
before_agent    → 질문 기억 (pi 명령어 / 로 시작하면 무시)
turn_end(stop)  → POST /observe   — {prompt, reply, error} + 건드린 파일 + transcript 경로
                    └ 서버: 지문 계산, 같은 세션 반복은 hits+1, 불만은 직전 주입 지식 강등(암묵 피드백)
agent_settled   → 최종 답 없이 멈춘 턴은 질문만이라도 관찰로 남기고 대기열 정리
                → (auto 켜면) 대기 관찰 ≥ threshold → jv secretary once --detach
```

### 3b. 서기 루프 (판단은 여기)

```
GET  /inbox?worker=<세션id>  → 미처리 관찰(세션별) + 반복 요청 클러스터 + 재발 오류
                             + 검증 필요 지식 + 각 세션의 transcript 경로
                             (worker 가 있으면 claimed 로 선점 — 두 서기가 같은 일을 안 한다)
read  <transcript>            → 같은 머신의 실제 pi 세션 원문을 열어 맥락 확인 (선택)
GET  /search?q=…             → 중복·근접 지식 확인
POST /remember               → source='secretary', occurrences, observation_ids → filed 로 처리
POST /score                  → 이번 증거로 검증 필요 지식을 확립(good)/재차 부정(bad)
POST /inbox/ack              → 버림(skipped)+이유, 마지막 한 줄 report → secretary_runs 에 집계
```

`commit` 은 레거시입니다. `projects.auto_distill=1` 인 프로젝트에서만 옛 방식(서버가 즉시
증류)으로 동작하고, 기본은 관찰로 저장됩니다. pi 는 `/commit` 을 아예 호출하지 않습니다.

## 4. 반복 감지 (서버의 유일한 '지능' — 모델 안 씁니다)

`engine/observe.py`

1. `fingerprint(text)` — 어미·조사·stopword 를 버린 어근 집합(`tokens.root_words`)을
   정렬한 지문. 같은 세션·같은 지문의 열려 있는 관찰은 `hits` 를 올립니다(중복 저감).
2. `repeats(project_id)` — 대기 관찰의 어근 집합을 서로 비교해 **공통 단어 2개 이상 +
   Jaccard ≥ 0.30** 이면 같은 요청으로 클러스터링하고, `hits` 합이 2 이상인 것만 넘깁니다.
   → "사람이 여러 번 요청한 항목". 이미 비슷한 지식이 있으면 `similar_memory_ids` 로 알려
   중복 등재를 막습니다.
3. `errors(project_id)` — `kind='error'` 의 지문 중복 → "다시 나타난 오류" (함정 후보).

재현 가능해야 하고 과금되면 안 되므로 이 계산은 결정론적으로 유지합니다. 좋은 판단은
서기(agent)의 모델에게 맡기는 역할 분담입니다.

## 5. 신뢰 상태 기계 (변화 없음)

```
fresh(검증 전) ──good / 사람 확인──▶ established(확립)
established ──bad──▶ contested(검증 필요, 일반 검색에서 제외 → 경고로만)
contested ──good──▶ established
established ──같은 제목으로 교정──▶ superseded(대체됨, 주입 안 됨)
```

원칙: **주입됐다는 것은 맞았다는 증거가 아닙니다.** 확립은 결과·사람 확인으로만 만들어지고,
서기가 남긴 지식도 예외가 아닙니다(`source='secretary'` 로 어디서 왔는지 구분).

## 6. 검색 (retrieve)

- 키워드(한국어 조사 제거 토큰화 + CJK 바이그램) + 선택 임베딩 혼합 점수
- 최고 점수의 40% 미만 후보는 버림 → "관련 없으면 빈 책" (에이전트를 방해하지 않음)
- contested 는 일반 결과에서 제외하고 경고 섹션으로만 전달
- `max_tier` 로 깊이 통제: 0=요약만, 1=개요까지, 2=전문

## 7. 인증·격리

| 경로 | 인증 | 비고 |
|---|---|---|
| 웹 화면 | 세션 쿠키(HMAC 서명, 30일) | `VIKING_SECRET` 으로 서명 |
| Agent API | `Authorization: Bearer jv_…` | 프로젝트 스코프 — 다른 프로젝트 접근 시 404 |
| 소유권 | 프로젝트 owner or admin | 그 외 403 |
| pi 확장 | 폴더 연결 파일(`.myviking-connection.json`) + `~/.myviking/connections.json`(0600) | 전역 env 로는 연결이 정해지지 않음 |

서기 세션도 같은 키·같은 폴더 연결을 씁니다. 단 `MYVIKING_ROLE=secretary` 로 열린 세션은
관찰을 올리지 않고(되먹임 차단), 관찰함 도구만 등록합니다.

## 8. 서기 agent — 실행 방식

```bash
jv secretary once [-f|--dry-run] [--limit N] [--task "..."]   # 관찰함 정리 한 번
jv secretary auto on|off [--every N]                          # 작업 세션이 스스로 깨우기
jv secretary status | log | stop | install | charter
```

- `once` 는 **pi 를 별도 세션으로 띄웁니다**(`pi --print --append-system-prompt
  ~/.myviking/secretary/secretary.md --tools read,grep,find,ls,viking_* --session-dir
  ~/.myviking/secretary/sessions`, env `MYVIKING_ROLE=secretary`). 기본은 백그라운드 +
  `run.pid` 로 이중 실행 방지, `-f` 로 지켜볼 수 있습니다.
- `secretary.md`(서기 원칙)는 설치 시 생성되고 사용자가 고칠 수 있습니다(기본 덮어쓰기 안 함).
  같은 내용을 `~/.pi/agent/agents/myviking-secretary.md`(pi 에이전트 정의) 와
  `~/.pi/agent/prompts/myviking-secretary.md`( `/myviking-secretary` 템플릿) 으로도 깔아
  두므로, pi 안에서 직접 서기를 부를 수도 있습니다.
- `pi` 실행파일이 없으면 명확히 안내하고 멈니다(3 exit). 관찰은 계속 쌓여 있습니다.
- `/myviking secretary once|status|auto on|off|stop` 은 pi 안에서 같은 일을 합니다.

## 9. 에이전트 연결 (pi 중심, 나머지는 유지)

| 방법 | 대상 | 하는 일 |
|---|---|---|
| pi 확장(`jv connect`/`jv pi install`) | pi·omp | 관찰 전송 + 브리핑/검색/메모 도구 + 서기 세션 호출 |
| 훅(`jv hook install`) | Claude Code | SessionStart/UserPromptSubmit/Stop → brief·prepare·**observe** |
| 훅+스킬+MCP(`jv jcode install`) | jcode | turn_end → **observe**, 스킬로 brief 안내, MCP 도구 |
| MCP(`jv mcp`) | Cursor·Codex 등 | brief/search/note/inbox/ack/remember/score 도구 |
| 셸 | 어떤 에이전트든 | `jv brief·search·note·observe·inbox·ack·remember·score` |

셋 다 같은 API 를 씁니다. 훅이 실패해도 코딩 세션은 막지 않습니다(fail-open).

## 10. 파일 지도

```
app/
  main.py          앱 조립·전역 예외 처리 (웹=리다이렉트, API=JSON)
  config.py        환경 변수 + 웹 모델 설정 병합
  db.py            스키마·마이그레이션(observations/secretary_runs/occurrences/auto_distill)
  security.py      PBKDF2 비밀번호 · HMAC 세션 · 키 발급/해시
  deps.py          current_user / login_required / admin_required / bearer_auth
  routes/web.py    로그인·가입·대시보드(서기 대기 수)·관리자·install.sh
  routes/admin_models.py  관리자 → 모델 설정 (선택 요약/임베딩)
  routes/projects.py  서가·지식 CRUD·연결 탭·키·내보내기·**서기 화면**·설정(auto_distill)
  routes/agent.py  /api/v1/* — brief prepare observe inbox inbox/ack secretary remember
                   seen score search commit(레거시) health me
  engine/observe.py   관찰 적재·지문·반복 클러스터·작업함·선점·처리통보·서기 실행장
  engine/distill.py   등재(remember)+중복 갱신+occurrences·[레거시 commit]
  engine/trust.py     상태 기계·evidence·브리핑 섹션·죽은 세션 정리
  engine/retrieve.py  점수·티어 팩킹·warning 분리·주입 마크다운
  engine/tiers.py     L0/L1/L2 생성 (LLM 선택·추출식 폴백)
  engine/tokens.py    토큰화 + root_words(반복 감지용 어근)
  engine/redact.py    비밀값 마스킹 (관찰·등재 모두 통과)
  engine/llm.py       OpenAI 호환 summarize/embed (조용한 폴백)
  templates/       + secretary.html (관찰함·반복 후보·마지막 업무)
  static/          style.css (지도 제작실 월드 + 관찰함 판) · app.js · fonts/
jv/cli.py          jv — 허브 확장(v11)·서기 조립·observe/inbox/ack·훅·MCP
docker-compose.yml  배포 파일 하나 · .env.example
docs/CONCEPT-SECRETARY.md  컨셉 전환의 이유와 경계
tools/pi-extension-smoke.mjs  pi 확장(myviking.ts)을 실제로 load 시켜 관찰/서기 경로를 검증
tests/             관찰함·서기·레거시·CLI·훅·MCP·웹 화면
```

## 11. 운영 노트

- 관찰 정리: 등재/폐기된(`filed`/`skipped`) 관찰은 14일 후 자동 삭제. 대시보드에서 수동 폐기도 가능.
- 브리핑만 받고 죽은 세션(질문 0회)은 24시간 유예 후 정리. 관찰이 있는 세션은 지우지 않습니다.
- 대시보드 → 프로젝트 → **서기** 에서 관찰함·반복 요청·마지막 업무 보고를 사람이 확인한다.
- 백업 = 볼륨 스냅샷 또는 프로젝트 `export.md`
- 가입을 닫으려면 `VIKING_ALLOW_SIGNUP=false`
- `legacy` 브랜치 = 재건축 전 코드. 서버 증류 컨셉(v10)은 git 히스토리와
  `auto_distill` 스위치로 되살릴 수 있습니다.
