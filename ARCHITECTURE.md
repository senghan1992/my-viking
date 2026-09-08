# myviking 아키텍처 — 사람별 프로젝트 지식 도서관

> **한 줄 요약** — 여러 사람이 가입해 각자 프로젝트 도서관을 만들고, 자기 코딩
> 에이전트를 연결하면 작업이 자동으로 쌓이고, 다음 세션에 자동으로 참조되며,
> 결과에 따라 지식이 적응하는 구조.

## 1. 계층 구조

```
사람(브라우저)                    에이전트 (훅/MCP/셸)
    │  가입·로그인·서가·지식편집          │  Bearer 키 (jv_…, 프로젝트 스코프)
    ▼                                  ▼
┌───────────────────────────────────────────────┐
│  routes/web.py    routes/projects.py   routes/agent.py  │  (FastAPI)
│  세션쿠키(HMAC)    소유권 확인           brief/prepare/commit/remember/score │
├───────────────────────────────────────────────┤
│  engine/distill.py   세션 → 지식 승격 (증류)          │
│  engine/retrieve.py  검색·예산 패킹 (L0/L1/L2)        │
│  engine/trust.py     신뢰 상태 기계 (적응)            │
│  engine/tiers.py     요약/개요/키워드 (LLM 선택 폴백)  │
│  engine/redact.py    비밀값 마스킹                    │
├───────────────────────────────────────────────┤
│  db.py  SQLite(index.db) — users/projects/api_keys/  │
│         memories/sessions/events                    │
└───────────────────────────────────────────────┘
```

## 2. 데이터 모델 (SQLite 가 원본)

```
users(id, email, pw_hash, role[admin|user], disabled)
 └─ projects(id, user_id, slug, name, description)   ← '사람별 프로젝트'
     ├─ api_keys(id, project_id, name, key_hash, revoked_at)  ← 프로젝트 스코프
     ├─ memories(id, project_id, category, title,
     │          summary[L0], overview[L1], content[L2],
     │          status, trust, keywords, evidence[])  ← 지식 1권
     ├─ sessions(id, project_id, agent, …)
     └─ events(id, project_id, kind, detail)           ← 활동 로그·trace
```

- 키는 평문을 저장하지 않습니다(SHA-256, 발급 화면에서 한 번만).
- 지식 URI: `viking://{project_id}/memories/{category}/{id}`

## 3. 에이전트 한 바퀴 (자동 캡처 · 자동 참조 · 적응)

```
세션 시작  brief()   →  확립 지식·최근 작업·검증 필요 브리핑 주입
질문       prepare() →  관련 지식 검색 → 티어 압축 주입 + trace_id
답변 완료  commit()  →  트랜스크립트에서 질문/답/파일 추출
                        └ 증류(distill): 지식 1권 승격 (같은 제목이면 갱신)
다음 질문  (암묵 피드백) → '안 되는데/틀렸어' 류 = 직전 주입 지식에 bad
                        → 검증 필요(contested)로 강등, evidence 기록
명시      remember() confirmed=True → 확립. 같은 제목 확립 지식은 대체(superseded)
          score()    good → 확립    bad → 검증 필요
대시보드   확인/고치기/삭제/내보내기(markdown)
```

## 4. 신뢰 상태 기계

```
fresh(검증 전) ── good 결과/사람 확인 ──▶ established(확립)
established ── bad 결과 ──▶ contested(검증 필요, 일반 검색에서 제외 → 경고로만)
contested ── good 결과 ──▶ established
established ── 같은 제목 교정(사람/에이전트) ──▶ superseded(대체됨, 주입 안 됨)
```

원칙: **주입됐다는 것은 맞았다는 증거가 아닙니다.** 확립은 오직 결과·사람 확인으로만
만들어집니다.

## 5. 검색 (retrieve)

- 키워드(한국어 조사 제거 토큰화 + CJK 바이그램) + 선택 임베딩(OpenAI 호환) 혼합 점수
- 최고 점수의 40% 미만 후보는 버림 → "관련 없으면 빈 책" (에이전트를 방해하지 않음)
- contested 는 일반 결과에서 제외하고 경고 섹션으로만 전달
- `max_tier` 로 깊이 통제: 0=요약만, 1=개요까지, 2=전문

## 6. 인증

| 경로 | 인증 | 비고 |
|---|---|---|
| 웹 화면 | 세션 쿠키(HMAC 서명, 30일) | `VIKING_SECRET` 으로 서명 |
| Agent API | `Authorization: Bearer jv_…` | 프로젝트 스코프 — 다른 프로젝트 접근 시 404 |
| 소유권 | 프로젝트 owner or admin | 그 외 403 |

## 7. 배포 (Portainer)

- `deploy/stack.yml` — 스택 하나. 볼륨 `viking-data` 에 데이터 전부.
- 환경 변수는 Portainer 의 Environment variables 로 치환 (`${VIKING_PORT:-8787}` 등)
- 역방향 프록시(Traefik/NPM) 뒤에서는 `VIKING_BASE_URL` 설정 → 연결 안내가 정확해짐
- 선택 LLM/임베딩: 없으면 추출식 요약·키워드 검색으로 폴백 (전체 기능 동작)

## 8. 에이전트 연결 3종

| 방법 | 대상 | 특징 |
|---|---|---|
| 훅 (`jv hook install`) | Claude Code | 무인 자동 캡처 (권장) — fail-open |
| MCP (`jv mcp`) | Cursor·Codex 등 | 도구 4종: brief/search/remember/score |
| 셸 (`jv brief/search/remember`) | 어떤 에이전트든 | 명령 한 줄 |

셋 다 같은 API 를 씁니다. 훅이 실패해도 코딩 세션은 막지 않습니다.

## 9. 파일 지도

```
app/
  main.py          앱 조립·전역 예외 처리 (웹=리다이렉트, API=JSON)
  config.py        환경 변수 — deploy/stack.yml 의 environment 와 1:1
  db.py            스키마·마이그레이션(컬럼 추가)·질의 헬퍼
  security.py      PBKDF2 비밀번호 · HMAC 세션 · 키 발급/해시
  deps.py          current_user / login_required / admin_required / bearer_auth
  routes/web.py    로그인·가입·대시보드·관리자
  routes/projects.py  서가·지식 CRUD·연결 탭·키·내보내기·설정·삭제
  routes/agent.py  /api/v1/* — brief prepare commit remember score search health
  engine/redact.py 비밀값 마스킹 (대입문 우선 → 키 패턴 → URL/블록)
  engine/tokens.py 한국어/영어/CJK 토큰화 + 키워드 추출
  engine/tiers.py  L0/L1/L2 생성 (LLM 선택·추출식 폴백)
  engine/retrieve.py  점수·티어 팩킹·warning 분리·주입 마크다운
  engine/distill.py   질문→제목 압축·카테고리 추정·같은 제목 갱신/대체
  engine/trust.py     상태 기계·evidence·브리핑 섹션
  engine/llm.py       OpenAI 호환 summarize/embed (조용한 폴백)
  templates/       base/login/signup/dashboard/project/connect/key_reveal/admin
  static/          style.css · app.js (검색·모달·복사)
jv/cli.py          에이전트 머신용 — 훅 설치/점검/4이벤트/원격/MCP stdio
deploy/            stack.yml(portainer) · docker-compose · .env.example
Dockerfile         python:3.12-slim 단일 스테이지
tests/             24개 — auth/격리/에이전트 루프/적응/마스킹/CLI
```

## 10. 운영 노트

- 지식이 계속 늘어도 문제없도록: 같은 제목은 갱신, superseded 는 검색 제외, 이벤트는 최근 20개 표시
- 백업 = 볼륨 스냅샷 또는 프로젝트 `export.md`
- 가입을 닫으려면 `VIKING_ALLOW_SIGNUP=false`
- 이전(재건축 전) 구현은 `legacy` 브랜치 — 차이 요약은 `docs/ANALYSIS.md`