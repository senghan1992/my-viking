# myviking — 사람별 프로젝트 지식 도서관

> **코딩 에이전트가 자동으로 채우고, 자동으로 참조하고, 결과에 따라 적응하는
> 프로젝트 지식 도서관.** 사람마다 계정이 있고, 계정마다 프로젝트(서가)가 있고,
> 프로젝트마다 에이전트를 연결하면 지식이 쌓입니다.

```
   사람들 (여러 명)                    서버 (Docker 컨테이너 1개)
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

## 빠른 시작 — 배포 (Docker 하나면 끝)

배포 파일은 저장소 루트의 **`docker-compose.yml` 하나**입니다. Docker 가 있는
서버(VPS·NAS·클라우드·Portainer)면 어디든 그대로 씁니다.

### 방법 A — docker compose (일반 서버)

```bash
git clone https://github.com/senghan1992/my-viking.git
cd my-viking
cp .env.example .env      # 선택 — 포트·가입·키 설정 (기본만으로도 동작)
docker compose up -d --build
```

### 방법 B — Portainer

Portainer → **Stacks → Add stack** → 이름 `myviking` →

- Build method: **Repository** → 이 저장소 URL, `main` 브랜치 (Path 는 비움 — 루트 docker-compose.yml 자동 인식)
  - 또는 **Web editor** 에 `docker-compose.yml` 내용을 붙여넣기
- Environment variables 에서 **`VIKING_PORT`** 만 채우고(기본 8787) **Deploy**

### 열기

`http://<서버 IP>:8787/` → **새 계정 만들기로 가입** (첫 가입자 = 관리자)

### 프로젝트 만들기

대시보드에서 **새 프로젝트** 생성 → 서가(빈 도서관)가 생깁니다.

### 에이전트 연결

프로젝트 → **🔗 에이전트 연결** 탭 → **새 키 발급** → 한 번만 보이는 키와 함께
설치 안내가 나옵니다. 탭에서 자기 에이전트를 고르고 **단계별 안내**를 따라 하면 됩니다
(Claude Code 자동 캡처 · pi 확장 · MCP · 셸):

**가장 빠른 연결 — 명령 하나** (키는 발급 직후 화면에 이미 박혀 나옵니다):

```bash
# 프로젝트 폴더 터미널에서 (jv 가 없으면 함께 설치, 이 머신의 에이전트를 전부 감지해 연결)
curl -fsSL http://<서버>:8787/install.sh | bash -s -- --url http://<서버>:8787 --key jv_xxxx --project <slug>
```

한 번에: Claude Code 훅 · pi/omp 허브 확장 · jcode(훅+스킬+MCP)를 감지해 전부 설치하고,
그 외 에이전트(Cursor·Codex 등)용 MCP 설정을 출력합니다. 수동으로는:

```bash
pip install git+https://github.com/senghan1992/my-viking.git
cd ~/my-project
jv hook install --url http://<서버>:8787 --key jv_xxxx --project <slug>   # Claude Code
jv pi install   --url http://<서버>:8787 --key jv_xxxx   # pi → 이 폴더 연결 + /reload (--project 는 키로 자동 식별)
# ✓ 서버 확인: …  (주소/키가 틀리면 여기서 멈춥니다)
```

`jv connect` 가 위 설치를 하나로 묶은 만능 명령입니다
(`--agent claude|pi|omp|jcode|mcp` 로 하나만 고를 수도 있습니다).

### git 처럼 쓰기 — 설치 후엔 이것만 알면 됩니다

`pip install` 로 `jv` 가 생기면, 에이전트 종류를 몰라도 아래 여섯 개면 충분합니다.
폴더마다 붙였다 떼었다가 자유롭게 합니다.

| 명령 | git 으로 | 하는 일 |
|---|---|---|
| `jv connect` | `git remote add` | 주소·키만 묻고, 이 폴더를 그 프로젝트에 묶으며 이 머신의 에이전트를 전부 설치 |
| `jv status` | `git status` | 이 폴더가 도서관을 쓰는지 + 서버·에이전트 연동 상태를 한 화면으로 |
| `jv disconnect` | (해제) | **이 폴더에서 완전히 해제** — 폴더 연결 파일 + 폴더의 Claude Code 훅 제거. jcode 연동이라면 전역 MCP 환경도 함께 정리해 다른 프로젝트로 새지 않도록 |
| `jv connect <이름>` / `jv switch <이름>` | `git checkout` | 저장된 다른 프로젝트로 이 폴더만 전환 (키 재입력 없음) |
| `jv list` | `git remote -v` | 저장된 연결 목록 (키는 0600 파일에만) |
| `jv disable` / `jv enable` | 스위치 | 이 **컴퓨터 전체**에서 도서관 끄기/켜기 — 연결은 남겨 두고 어디에서도 안 쓰게 |

```bash
cd ~/my-project
jv connect                       # 주소·키만 묻는다 (프로젝트는 키로 자동 식별)
jv status                        # 무엇이 붙어 있는지 확인
jv disconnect                    # 이 폴더에서는 안 쓴다
jv connect 데이터자판기            # 저장된 다른 프로젝트로 이 폴더 전환
jv disable                       # 이 컴퓨터 전체 OFF (여행·집 검증 등)
```

원칙은 셋입니다.

1. **폴더 파일(.myviking-connection.json)에는 비밀이 없습니다** — 키는
   `~/.myviking/connections.json`(0600) 에만 있고, 폴더 파일은 연결 *이름*만 담습니다
   (git 의 `.git/HEAD` 같은 것). 폴더를 복사해도 키가 새지 않습니다.
2. **훅도 주소를 박지 않습니다** — Claude Code 훅은 폴더 연결 파일을 보고 움직입니다.
   그래서 `jv switch` 하면 곧바로 따라가고, `jv disconnect` 하면 조용히 멈춥니다
   (기존 설치본처럼 주소가 박힌 예전 훅도 `jv connect` 한 번으로 최신으로 갱신됩니다).
3. **해제는 항상 되돌릴 수 있습니다** — `jv disconnect` 는 폴더만 떼고 저장된 연결(키)은
   남깁니다. `jv disconnect --all` 로 키까지 지울 수 있고, `jv uninstall --purge --yes` 로
   전부 초기화할 수 있습니다.

`git` 저장소 안에서 연결하면 `.git/info/exclude` 에 연결 파일이 자동으로 들어가서
`git status` 에 안 나타납니다.

이후 그 폴더에서 Claude Code 나 pi 를 쓰면 끝입니다. 질문마다 기존 지식이 주입되고,
작업이 끝나면 자동으로 기록됩니다. 다음 세션은 브리핑을 받고 시작합니다.

**연결은 모든 에이전트에 '프로젝트(폴더) 단위'입니다 — 전역 "현재 연결" 이란 것 자체가
존재하지 않습니다** (pi hub v8 + CLI 통일). 그 폴더에서 여는 세션은 그 폴더의
`.myviking-connection.json` 이 가리키는 도서관에만 붙고, **설정 파일이 없는 폴더는
아무 설정 없이 자유롭게 쓰입니다** (기록도 주입도 0). 다른 프로젝트에서 연결했다고
이 프로젝트가 그 도서관으로 붙지 않으며, 연결 파일 탐색은 git 저장소 루트까지만
올라갑니다(상위 공유 폴더·홈으로 새지 않음). 셸·프로필에 `MYVIKING_*` 환경변수를
걸어도 연결을 정하지 못하도록 끊어뒀습니다 — 전역으로 모든 폴더를 한 프로젝트로
새던 마지막 길이었습니다. (단, 에이전트 설정에 스스로 env 를 박아준 MCP 서버는 그
에이전트의 opt-in 으로 존중되되, 폴더 연결이 있으면 폴더가 우선입니다.)
키·주소는 `~/.myviking/connections.json`(0600)에 저장될 뿐이고, `jv pi install`·
`/myviking connect`·`/myviking switch` 는 그 키를 **현재 폴더에 묶습니다**. pi 는 연결된 세션의
매 턴 질문→답이 자동으로 도서관에 기록됩니다(그래서 다른 프로젝트 기록이 섞이지 않음).
그래서:

```bash
jv pi install --url ... --key ...                  # 연결 저장 + 이 폴더 기본값 + 허브 확장 설치
jv pi list                                            # 저장된 연결 목록
jv pi switch <이름>                                  # 이 폴더의 기본 연결을 바꿈 (git checkout 느낌)
jv pi disconnect                                      # 이 폴더 기본 연결 해제
jv pi remove <이름>                                  # 저장된 연결 삭제 (키 포함)
jv pi check                                           # 폴더 연결·서버 인증 확인
```

pi 안에서는 `/myviking`(이 프로젝트 상태/저장된 키 목록) · `/myviking use`(이 프로젝트의
설정를 이 세션에 다시 적용) · `/myviking switch`(이 프로젝트의 연결을 저장된 키로 교체)
· `/myviking connect`(새 연결을 만들어 **이 폴더**에 묶기) · `/myviking disconnect`
(이 세션 해제 + 이 폴더의 설정도 제거) · `/myviking remove`(저장된 연결 삭제, 이 폴더가
가리키면 함께 해제) 로 같은 일을 할 수 있습니다. 연결 파일이 없는 폴더에서 pi 는
지식 도서관 없이 그냥 자유롭게 쓰입니다.

**omp(Oh My Pi)도 pi 와 완전히 동일하게 지원됩니다** — pi 를 포크한 같은 계열
런타임이라 같은 TS 확장(`myviking.ts`)을 그대로 로드하고(`--extension` 로 직접
로드해 검증됨: 도구·`/myviking` 명령 모두 그대로 동작), 연결 저장소·폴더 링크도
pi 와 공유합니다. 설치 위치만 다릅니다 (`~/.omp/agent/extensions/myviking.ts`):

```bash
jv omp install --url ... --key ...    # 연결 저장 + 이 폴더 기본값 + 허브 확장 설치 (~/.omp)
jv omp list / switch <이름> / disconnect / remove <이름> / check   # pi 와 동일
```

**jcode(J-Code)는 폴더 단위로 붙습니다** — pi 와 같은 연결 저장소(`~/.myviking/`)
를 쓰고, 설치가 3가지를 만듭니다: ① `~/.jcode/config.toml` 의 `[hooks]`
(turn_end 마다 질문→답 자동 기록, 이미 직접 설정한 이벤트는 보존),
② `~/.jcode/skills/myviking/SKILL.md` (세션 시작 시 `jv brief` 사용법),
③ `~/.jcode/mcp.json` (viking_brief·search·remember·score MCP 도구):

```bash
jv jcode install --url ... --key ...      # 연결 저장 + 폴더 링크 + 훅/스킬/MCP 설치
jv jcode check                              # 훅·스킬·MCP·서버 인증 점검
jv jcode status                             # 이 폴더 연결 + 연동 상태
jv jcode list / jv jcode switch <이름>     # 저장된 연결 목록 / 폴더 전환 (MCP env 도 갱신)
jv jcode disconnect                         # 폴더 연결 해제 (+ 전역 jcode MCP 의 myviking 제거 — 다른 프로젝트로 새지 않음)
jv jcode remove <이름>                     # 연결 삭제 (키 포함) + 연동 제거
jv jcode uninstall                          # 연동만 제거 (연결은 유지)
```

세션 안에서 에이전트는 `jv brief`(브리핑) · `jv search "개념"`(검색) ·
`jv remember`(기록) · `jv score`(교정) 을 인자 없이 폴더 연결만으로 씁니다.

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

## 설정 (환경 변수)

docker compose 는 루트의 `.env` 를, Portainer 는 스택의 Environment variables 를
읽습니다. 값이 같으므로 아래 표만 보면 됩니다.

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

> **설정 UI**: 환경변수를 안 넣어도 됩니다. 로그인 후 **관리자 → ⚙ 모델 설정** 화면에서
> LLM/임베딩 제공자·키를 넣으면 즉시 적용됩니다 (웹 설정 > 환경변수 > 기본값).
>
> **모델 드롭다운**: 화면에 pi(코딩 에이전트)에서 쓰는 Databricks Serving 목록이
> **사전 등록**되어 있어 한 번에 골라 쓸 수 있습니다 (`app/model_catalog.py` — 호스트를
> 바꾸려면 `DATABRICKS_HOST` 수정). 다른 커스텀 모델은 **커스텀 모델 등록** 폼으로
> 추가하면 드롭다운에 바로 나타납니다. `/invocations` 로 끝나는 직접 호출 주소는
> 자동 감지되어 그대로 호출합니다 (뒤에 `/chat/completions` 를 붙이지 않음).

---

## 개발

```bash
pip install -e .[dev]
python -m uvicorn app.main:app --port 8787   # VIKING_DATA=./data VIKING_SECRET=dev
pytest                                       # 73 tests — 가입→키→에이전트 루프→적응→연결/해제
```

로컬 컨테이너: `docker compose up -d`

## 메모리

- 프로젝트 삭제는 서가 전체 삭제 (확인 대화상자 있음)
- 질문 0회 세션(브리핑만 받고 죽은 세션)은 화면(브리핑 최근 작업·대시보드)에서 숨고,
  24시간 유예 후 자동 정리됩니다. 질문이 기록된 세션은 지우지 않습니다.
- 데이터는 볼륨 `viking-data` 의 `index.db` 하나. 백업은 볼륨 스냅샷/`export.md`
- `legacy` 브랜치에 재건축 전 코드(17,000줄)가 보관되어 있습니다

## 라이선스

MIT