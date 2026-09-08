# myviking — 사람별 프로젝트 지식 도서관

> **코딩 에이전트가 자동으로 채우고, 자동으로 참조하고, 결과에 따라 적응하는
> 프로젝트 지식 도서관.** 사람마다 계정이 있고, 계정마다 프로젝트(서가)가 있고,
> 프로젝트마다 에이전트를 연결하면 지식이 쌓입니다.

```
   사람들 (여러 명)                    서버 (Docker · Portainer 스택 1개)
 ┌─────────────────────┐   HTTPS    ┌──────────────────────────────────┐
 │  브라우저            │──────────▶ │  대시보드 (가입 → 내 프로젝트)     │
 │  ─ 가입/로그인       │            │  · 프로젝트 만들기                │
 │  ─ 내 프로젝트 도서관 │            │  · 연결 탭 → 키 + 설치 명령        │
 │  ─ 지식 보기·고치기   │            │  · 지식 검색·확립/삭제            │
 └─────────────────────┘            ├──────────────────────────────────┤
                                     │  Agent API (Bearer 키, 프로젝트별) │
 ┌─────────────────────┐            │  brief → prepare → commit → score │
 │  코딩 에이전트       │──────────▶ │  · 세션 시작: 브리핑 주입          │
 │  (Claude Code 훅,   │            │  · 질문마다: 관련 지식 주입         │
 │   MCP, 셸)           │            │  · 작업 끝: 자동 기록·증류          │
 └─────────────────────┘            │  · 다음 질문: 지난 답 채점(적응)    │
                                     ├──────────────────────────────────┤
                                     │  SQLite 한 곳 (index.db)          │
                                     │  + 사람이 읽는 markdown 내보내기    │
                                     └──────────────────────────────────┘
```

- **관리자가 키를 나눠주는 구조가 아닙니다** — 사람들이 스스로 가입하고, 스스로
  프로젝트를 만들고, 스스로 에이전트를 연결합니다. (첫 가입자가 관리자)
- **도서관 모델** — 지식은 '권'입니다. 카테고리(📖 지식 / 🛠️ 명령 / ⚠️ 함정 / 🧭 결정)로
  분류되고, 상태 칩(검증 전/확립/검증 필요)이 붙어 서가에 꽂힙니다.
- **적응** — 주입된 지식에 결과가 좋으면 확립, 어긋나면 '검증 필요'로 내려가고,
  같은 제목으로 다시 쓰면 옛것은 대체됩니다.

---

## 빠른 시작 — Portainer 배포 (권장)

### 1. 스택 추가

Portainer → **Stacks → Add stack** →

- 이름: `myviking`
- Build method: **Repository** → 이 저장소 URL, `main` 브랜치, **Path: `deploy`** (deploy/stack.yml)
  - 또는 **Web editor** 에 `deploy/stack.yml` 내용을 붙여넣기
- Environment variables 에서 **`VIKING_PORT`** 만 채우고(기본 8787) **Deploy**

### 2. 열기

`http://<서버 IP>:8787/` → **새 계정 만들기로 가입** (첫 가입자 = 관리자)

### 3. 프로젝트 만들기

대시보드에서 **새 프로젝트** 생성 → 서가(빈 도서관)가 생깁니다.

### 4. 에이전트 연결

프로젝트 → **🔗 에이전트 연결** 탭 → **새 키 발급** → 한 번만 보이는 키와 함께
설치 명령이 나옵니다. 에이전트 머신에서:

```bash
pip install git+https://github.com/senghan1992/my-viking.git
cd ~/my-project
jv hook install --url http://<서버>:8787 --key jv_xxxx --project <slug>
# ✓ 서버 확인: …  (주소/키가 틀리면 여기서 멈춥니다)
```

이후 그 폴더에서 Claude Code 를 쓰면 끝입니다. 질문마다 기존 지식이 주입되고,
작업이 끝나면 자동으로 기록됩니다. 다음 세션은 브리핑을 받고 시작합니다.

> MCP(Cursor·Codex 등)는 연결 탭의 JSON 을, 셸 전용 에이전트는 `jv search/remember`
> 를 쓰면 됩니다. 어떤 에이전트든 연결됩니다.

---

## 동작 원리 (짧게)

| 순간 | 일어나는 일 |
|---|---|
| 세션 시작 | `brief` — 확립된 지식·최근 작업·검증 필요 목록을 브리핑으로 주입 |
| 질문 입력 | `prepare` — 도서관 검색 → 관련 지식만 티어(L0 요약/L1 개요/L2 전문)로 압축 주입 + `trace_id` |
| 답변 완료 | `commit` — 트랜스크립트에서 질문·답·변경 파일 추출 → **증류**: 지식 1권으로 승격 |
| 다음 질문 | 직전에 준 지식이 '안 되는데/틀렸어' 류로 판정되면 **검증 필요**로 강등 (적응) |
| 누구든 명시 | `remember`(직접 기록) · `score`(good/bad) · 대시보드 확인/고치기/삭제 |

비밀값(API 키·비밀번호·JWT·비공개 키)은 저장 전에 서버·클라이언트 양쪽에서
마스킹됩니다. 자세한 구조는 [ARCHITECTURE.md](ARCHITECTURE.md), 이전 코드와의
차이는 [docs/ANALYSIS.md](docs/ANALYSIS.md) 를 보세요.

---

## 설정 (Portainer environment)

| 변수 | 기본 | 설명 |
|---|---|---|
| `VIKING_PORT` | 8787 | 바깥에 열 포트 |
| `VIKING_BASE_URL` | 비움 | 연결 안내에 표시할 공개 주소 (역방향 프록시 뒤에서 권장) |
| `VIKING_ALLOW_SIGNUP` | true | 새 가입 허용. 운영 중 false 로 닫기 |
| `VIKING_FIRST_USER_ADMIN` | true | 첫 가입자를 관리자로 |
| `VIKING_ADMIN_EMAILS` | 비움 | 관리자 고정 (쉼표 구분) |
| `VIKING_SECRET` | 자동 생성 | 세션 서명 (볼륨에 보관) |
| `VIKING_LLM_BASE_URL/API_KEY/MODEL` | 비움 | (선택) LLM 한 줄 요약 |
| `VIKING_EMBED_BASE_URL/API_KEY/MODEL` | 비움 | (선택) 의미 검색 |

없어도 전부 동작합니다. (요약은 추출식, 검색은 키워드 기반으로 폴백)

---

## 개발

```bash
pip install -e .[dev]
python -m uvicorn app.main:app --port 8787   # VIKING_DATA=./data VIKING_SECRET=dev
pytest                                       # 24 tests — 가입→키→에이전트 루프→적응
```

로컬 컨테이너: `docker compose -f deploy/docker-compose.yml up -d`

## 메모리

- 프로젝트 삭제는 서가 전체 삭제 (확인 대화상자 있음)
- 데이터는 볼륨 `viking-data` 의 `index.db` 하나. 백업은 볼륨 스냅샷/`export.md`
- `legacy` 브랜치에 재건축 전 코드(17,000줄)가 보관되어 있습니다

## 라이선스

MIT