# 분석 보고서 — 기존 코드는 어디까지 구현돼 있었나

> 이 문서는 재건축 전 상태(`legacy` 브랜치)를 기록한 것입니다.
> [README.md](../README.md) 는 새 구조를, [ARCHITECTURE.md](../ARCHITECTURE.md) 는
> 현재(재건축 후) 구조를 설명합니다.

## 1. 시작하기 전에: 요청하신 그림 vs 기존 코드

| 원하시는 것 | 기존 코드 상태 (my-viking @ legacy) | 결론 |
|---|---|---|
| 서버로 띄워서(docker) 도서관처럼 | ✅ 완성 — docker-compose/up.sh/stack.yml + Caddy | 유지 방향 맞음 |
| **사람들별로** 프로젝트 생성 | ⚠️ 절반 — '사람' 개념은 없고 **관리자 1인이** 프로젝트·키 발급 | **없던 것 — 새로 구현** |
| 대시보드에서 만들고 연결정보 제공 | ⚠️ 절반 — 대시보드·연결 안내는 있지만 관리자 중심 | 그대로 승계해서 재구성 |
| 연결정보를 coding agent에 연결 | ✅ 완성 — Claude Code 훅·MCP·셸 브리지 3종 | 그대로 승계 |
| 알아서 쌓이고 (자동 캡처) | ✅ 완성 — 훅이 세션 시작/질문/답변/종료를 자동 기록 | 그대로 승계 |
| 참고하고 적응 (검색·신뢰 적응) | ✅ 완성 — L0/L1/L2 티어, 신뢰 상태 기계(적응 루프) | 그대로 승계 (단순화) |

즉 **"지식 엔진"은 이미 동작했고, "여러 사람이 스스로 쓰는 구조(가입·로그인·프로젝트 소유권)"가
없었습니다.** 게다가 코드가 17,000줄로 자라서(모듈 30개) "어디까지 구현됐는지" 한눈에
보기 어려웠습니다.

## 2. 기존 구현 상세 (재건축 전 파일 기준, `src/jarvis/`)

### 구현되어 있었던 것 (동작 확인된 것들)

| 영역 | 파일 | 내용 |
|---|---|---|
| 자동 캡처 | `hooks.py`, `cli.py` | Claude Code 훅 4종(SessionStart/UserPromptSubmit/Stop/SessionEnd), fail-open, 트랜스크립트에서 마지막 질문·답·변경 파일 추출, `settings.local.json` 자동 병합 |
| 컨텍스트 티어 | `tiers.py`, `retrieve.py`, `tokens.py` | L0/L1/L2 저장, 예산 기반 검색·패킹, 한국어 조사 제거 토큰화, 카테고리 우선, 초점(중복 제거), 최소 관련성 하한 |
| 적응 루프 | `service.py`(trust), `learn.py` | 신뢰 상태 기계(fresh→established→contested→stale→superseded), 암묵 피드백(다음 질문이 직전 답 채점), blame 귀속, 교정 두 속도(즉시/도전), evidence 트레일 |
| 연동 3종 | `mcp_core.py`, `remote.py`, `connect.py` | MCP 도구, 셸 브리지, 에이전트별 연결 정보 생성 |
| 저장 | `store.py`, `db.py`, `models.py` | 마크다운 원본 + SQLite 색인, URI 모델 |
| 대시보드 | `ui.py` (1,611줄) | 프로젝트·지식·세션·설정·백업·모델 설정 화면 |
| 보안 | `auth.py`, `tokens.py`, `redact.py` | 관리자 키 + 스코프 키, SHA-256 저장, 비밀값 마스킹 |
| 백업 | `backup.py` | Google Drive 스냅샷 로테이션 |

### 없었던 것 (이번에 새로 만든 것)

- **사용자 가입/로그인(세션)** — 존재하지 않음. "개인 서버" 전제였음
- **프로젝트 소유권(owner)** — 프로젝트가 관리자 소유였고 사람에게 키만 나눠줌
- **사람별 대시보드** — 모든 화면이 관리자 화면이었음
- **Portainer 단일 스택 배포** — stack.yml 은 있었지만 이미지 선빌드 필수, 사용자 개념 없음
- **Markdown 내보내기**(도서관 '책'으로 뽑아 읽기) — 없음

### 버려진 것 (목적 대비 과함)

- LLM 제공자 웹 설정 탭, 임베딩 제공자 웹 설정 탭 (env 로 충분)
- Google Drive 백업 (볼륨 스냅샷으로 충분 — 필요 시 다시 추가 가능)
- 트레이스 상세 화면·리포트·metrics 등 관측계 화면 (trace_id 정도만 유지)

## 3. OpenViking 에서 가져온 아이디어 → 새 구조에서의 위치

| OpenViking 개념 | 새 구조에서 |
|---|---|
| L0/L1/L2 티어 로딩 | `app/engine/tiers.py` — 그대로 |
| 세션이 장기 메모리로 증류 | `app/engine/distill.py` — 그대로 |
| viking:// 가상 파일시스템 | `viking://{project}/memories/{category}/{id}` URI + 검색 결과로 노출 |
| 검색 결과 관찰(trace) | `trace_id` + 프로젝트 활동 로그로 단순화 |
| 멀티 에이전트 허브 | **이번 재건축의 핵심 변경: 사용자(사람) 계층 추가** — 사람 → 내 프로젝트 → 에이전트 연결 |

## 4. 결정 사항

1. **SQLite 를 원본으로** (기존: 마크다운이 원본 + SQLite 색인) — 마크다운 동기화·보관 디렉터리
   관리가 복잡도를 크게 올렸고, "책으로 읽고 싶다"는 요구는 `export.md` 로 충족.
2. **신뢰 상태는 4개로 단순화** (fresh/established/contested/superseded) — stale·tentative 제거.
3. **의존성 최소화** — FastAPI + uvicorn + jinja2 + httpx + pydantic. (기존: firecrawl, scrapy,
   pdfplumber, litellm, SQLAlchemy 등 20여 개)
4. **키는 프로젝트 스코프만** (사용자 전역 키 없음) — "내 에이전트 = 내 프로젝트"가 명확.
5. **첫 가입자 = 관리자**, `VIKING_ADMIN_EMAILS` 로 고정 가능.

## 5. 파일 지도 (재건축 후, 약 4,000줄)

```
app/            서버 (FastAPI)
  main.py       앱 조립·예외 처리
  config.py     환경 변수 + 웹 모델 설정 병합 (웹 > env > 기본값)
  db.py         SQLite 스키마·질의
  security.py   비밀번호·세션·API 키
  deps.py       인증 의존성 (웹 세션 / Bearer 키)
  routes/       web(로그인·대시보드·관리자) / admin_models(모델 설정) / projects(서가·연결·키) / agent(API)
  engine/       redact·tokens·tiers·retrieve·distill·trust·llm
  templates/    Jinja2 8개 (서버 렌더 + 가벼운 JS)
  static/       style.css, app.js
jv/             에이전트 머신용 CLI (훅 설치·4종 이벤트·원격·MCP stdio)
docker-compose.yml   배포 파일 하나 · .env.example
Dockerfile      단일 스테이지
tests/          가입→키→에이전트 루프→적응까지 통합 테스트 24건
```