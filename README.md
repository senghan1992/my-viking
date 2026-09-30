# myviking — 사람별 프로젝트 지식 도서관

> **작업 세션은 '관찰'만 남기고, 별도 세션으로 열린 서기 agent 가 그것을 지식으로 정리하는
> 프로젝트 지식 도서관.** 사람마다 계정이 있고, 계정마다 프로젝트(서가)가 있고, 프로젝트마다
> 에이전트를 연결하면 지식이 쌓입니다. 서버는 지식을 추출하지 않습니다 — 판단은 agent 의 일입니다.

```
 작업 세션 (pi)                 서버 (Docker 컨테이너 1개)              서기 세션 (pi, 별도 프로세스)
 ─────────────────             ──────────────────────────            ────────────────────────
 나는 코딩만 한다                저장고 · 검색 · 반복 카운터              관찰함을 읽고 '판단'한다
 턴의 흔적을 관찰로만 올린다 ──▶   · observations  (관찰함)          ◀──   · GET  /inbox   (뭐가 있었나)
 지식을 만들지 않는다             · 지문/중복 계산  (모델 안 씀)            · 세션 트랜스크립트 원문도 연다
                                 · memories      (지식 · L0/L1/L2)  ──▶   · POST /remember (남길 것만)
                                 · brief / prepare → 주입·적용             · POST /inbox/ack (버린 것도)
                                 · trust (확립 / 검증 필요 / 대체)          · 코드는 고치지 않는다
```

- **모델을 서버에 연결하지 않습니다** — 옛 버전은 서버가 (선택 LLM 으로) 매 턴을 증류했지만,
  지금은 **서기 agent**가 판단합니다. 서버에 API 키를 넣지 않아도 전체가 동작합니다
  (요약·임베딩용 LLM 은 어디까지나 품질 개선용 선택 사항).
- **세션 공유** — 관찰에는 그 세션의 pi 트랜스크립트 경로가 함께 기록됩니다. 같은 머신의 서기는
  `read` 로 원문을 열어 "무슨 일이 있었고 뭘 잃어버렸나"를 직접 확인합니다.
- **다 적지 않는다** — 서기는 ① 다음에 같은 실수를 하게 하는 것 ② 사람이 두 번 이상 요청한 것
  ③ 결정과 근거 ④ 재사용 절차를 남깁니다. 일회성 작업은 버립니다.
- **셀프서비스** — 관리자가 키를 나눠주지 않습니다. 가입 → 프로젝트 → 키 발급 → 명령 붙여넣기.
- **적응** — 주입된 지식에 결과가 좋으면 확립, 어긋나면 '검증 필요'로 내려가고, 같은 제목으로
  다시 쓰면 옛것은 대체됩니다. 반복 요청 횟수는 지식에 `occurrences` 로 누적됩니다.

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

### 에이전트 연결 (지금 중심은 **pi**)

프로젝트 → **🔗 에이전트 연결** 탭 → **새 키 발급** → 한 번만 보이는 키와 함께
설치 안내가 나옵니다. 탭의 단계별 안내(Claude Code · pi · jcode · MCP · 셸)를 따라 하면 됩니다.
**pi 외의 연결도 코드에는 그대로 남아 있습니다** — 다만 새 컨셉(서기)은 pi 를 기준으로 설계했고,
다른 에이전트는 '관찰을 서버에 넘기는' 수준까지 붙어 있습니다.

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
| `jv secretary once` | (서기 부르기) | 관찰함에 쌓인 작업을 서기 세션이 읽고 지식으로 정리 |
| `jv inbox` | (편지함 보기) | 정리 대기 관찰 · 반복 요청 · 재발 오류 확인 |

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

이후 그 폴더에서 pi(또는 Claude Code)를 열면 끝입니다. 질문마다 기존 지식이 주입되고,
작업은 **관찰**로 쌓입니다. 지식으로 승격하는 건 **서기 agent**가 합니다 — `jv secretary once`.
다음 세션은 그 결과가 담긴 브리핑을 받고 시작합니다.

**연결은 모든 에이전트에 '프로젝트(폴더) 단위'입니다 — 전역 "현재 연결" 이란 것 자체가
존재하지 않습니다** (pi hub v10 + CLI 통일). 그 폴더에서 여는 세션은 그 폴더의
`.myviking-connection.json` 이 가리키는 도서관에만 붙고, **설정 파일이 없는 폴더는
아무 설정 없이 자유롭게 쓰입니다** (기록도 주입도 0). pi/omp 허브 확장 파일은 이
머신에 한 번 설치되지만, **`viking_brief`·`viking_search`·`viking_remember`·`viking_score`
도구는 '연결된 폴더의 세션' 에서만 등록됩니다** — 연결 안 한 폴더에서는 도구 목록에
`viking_*` 가 아예 없고 브리핑도 "연결 없음" 안내도 뜨지 않습니다(연결을 돕는
`/myviking` 관리 명령만 남고, 그 명령은 스스로 연결·주입·기록하지 않습니다).
머신 전체를 끄는 `jv disable` 도 확장까지 관철되어, 꺼져 있으면 연결된 폴더라도
도구 등록·브리핑·기록이 모두 0입니다(`jv enable` 로 다시 켬). 다른 프로젝트에서 연결했다고
이 프로젝트가 그 도서관으로 붙지 않으며, 연결 파일 탐색은 git 저장소 루트까지만
올라갑니다(상위 공유 폴더·홈으로 새지 않음). 셸·프로필에 `MYVIKING_*` 환경변수를
걸어도 연결을 정하지 못하도록 끊어뒀습니다 — 전역으로 모든 폴더를 한 프로젝트로
새던 마지막 길이었습니다. (단, 에이전트 설정에 스스로 env 를 박아준 MCP 서버는 그
에이전트의 opt-in 으로 존중되되, 폴더 연결이 있으면 폴더가 우선입니다.)
키·주소는 `~/.myviking/connections.json`(0600)에 저장될 뿐이고, `jv pi install`·
`/myviking connect`·`/myviking switch` 는 그 키를 **현재 폴더에 묶습니다**. pi 는 연결된 세션의
매 턴 질문→답·건드린 파일·실패한 도구를 **관찰로** 그 프로젝트에만 보냅니다
(그래서 다른 프로젝트 기록이 섞이지 않고, 관찰만 보내므로 잡음 지식이 쌓이지 않습니다).
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

### 서기 agent — 지식을 만드는 사람

작업 세션은 지식을 만들지 않습니다. `pi` 를 **별도 세션**으로 열어 관찰함을 정리하는
**서기(secretary)** 가 판단·요약·등재를 담당합니다.

```bash
jv secretary once            # 서기 세션을 백그라운드로 한 번 실행
jv secretary once -f         # 화면으로 지켜보기 (무엇을 등재/버렸는지 출력)
jv secretary auto on         # 관찰이 N건 쌓이면 작업 세션이 스스로 서기를 깨운다
jv secretary auto on --every 8
jv secretary status          # 대기 관찰 · 반복 요청 · 마지막 업무 보고
jv secretary charter         # 서기 원칙 파일 경로 (직접 고쳐도 됩니다)
```

pi 안에서는 `/myviking secretary once` · `/myviking secretary auto on` ·
`/myviking secretary status` 가 같은 일이고, `/myviking note "메모"` 로 서기에 게
메모만 남길 수 있습니다.

서기가 쓰는 도구(`viking_inbox` · `viking_session` · `viking_search` · `viking_file` ·
`viking_ack` · `viking_report`)는 `MYVIKING_ROLE=secretary` 로 열린 세션에서만 등록됩니다.
같은 머신의 서기는 관찰에 적힌 트랜스크립트 경로를 열어 **실제 세션 내용**을 확인합니다
(관찰은 요약, 트랜스크립트는 원문). 코드 수정 도구는 주어지지 않습니다.

기타 관찰함 명령 (어떤 에이전트·셸에서도 동일):

```bash
jv note "이건 기록할 가치 있음"      # 서기에게 메모 (지식을 만들지 않음)
jv inbox                          # 정리 대기 관찰 · 반복 요청 · 재발 오류
jv ack 12,13 --outcome skipped --reason "일회성"   # 처리 통보
jv remember "제목" --content "내용"  # (급할 때) 직접 등재
```

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
(turn_end 마다 질문→답을 **관찰**로 전송, 이미 직접 설정한 이벤트는 보존),
② `~/.jcode/skills/myviking/SKILL.md` (세션 시작 시 `jv brief` 사용법),
③ `~/.jcode/mcp.json` (viking_brief·search·note·inbox·ack·remember·score MCP 도구):

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
`jv note "메모"`(서기에게 전달) · `jv remember`(직접 등재) · `jv score`(교정) 을
인자 없이 폴더 연결만으로 씁니다.

> MCP(Cursor·Codex 등)는 연결 탭의 JSON 을, 셸 전용 에이전트는 `jv search/note`
> 를 쓰면 됩니다. 어떤 에이전트든 관찰함에는 붙을 수 있습니다.
> 서기를 두지 않는 에이전트(Claude Code 훅 등)는 프로젝트 설정의 **레거시 자동 증류**를
> 켜면 옛 방식(서버가 매 턴을 지식으로 승격)으로 되돌아갑니다.

---

## 동작 원리 (짧게)

| 순간 | 누가 | 일어나는 일 |
|---|---|---|
| 세션 시작 | 작업 세션 | `brief` — 확립 지식·검증 필요·서기 대기가 브리핑으로 주입 |
| 질문 입력 | 작업 세션 | `prepare` — 도서관 검색 → 관련 지식만 티어(L0/L1/L2)로 압축 주입 + `trace_id` |
| 답변 완료 | 작업 세션 | `observe` — 질문·답·건드린 파일·오류를 **관찰로** 저장 (지식을 만들지 않음) |
| 서버 계산 | 서버 | 같은 요청 지문을 세어 **반복 요청**·**재발 오류**를 묶는다 (모델 안 씀) |
| 정리 | **서기 agent** | `inbox` 로 관찰 + 세션 트랜스크립트를 읽고 → `remember` 로 등재 → `ack` 로 통보 |
| 다음 질문 | 작업 세션 | 직전 주입 지식이 '안 되는데/틀렸어' 류면 **검증 필요**로 강등 (적응) |
| 누구든 명시 | 사람/agent | `note`(메모) · `remember`(즉시 등재) · `score`(good/bad) · 대시보드 확인/고치기/삭제 |

비밀값(API 키·비밀번호·JWT·비공개 키)은 저장 전에 서버·클라이언트 양쪽에서
마스킹됩니다. 관찰 원문은 잘라서 저장되고, 전체 트랜스크립트는 에이전트 머신에 남습니다.
자세한 구조는 [ARCHITECTURE.md](ARCHITECTURE.md), 컨셉 전환 이유는
[docs/CONCEPT-SECRETARY.md](docs/CONCEPT-SECRETARY.md), 이전 코드와의 차이는
[docs/ANALYSIS.md](docs/ANALYSIS.md) 를 보세요.

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
| `VIKING_LLM_BASE_URL/API_KEY/MODEL` | 비움 | (선택) 지식 **한 줄 요약** 품질 — 판단·추출은 여전히 서기가 한다 |
| `VIKING_EMBED_BASE_URL/API_KEY/MODEL` | 비움 | (선택) 의미 검색 |

없어도 전부 동작합니다. (요약은 추출식, 검색은 키워드 기반으로 폴백)

> **모델 연결은 '지식 추출' 수단이 아닙니다.** 옛 버전은 서버 LLM 이 매 턴을 증류했지만,
> 지금은 서기 agent(pi 세션)가 판단하고 서버는 보관만 합니다. 여기 설정은 요약 한 줄과
> 의미 검색 품질을 올리는 선택 항목일 뿐입니다.

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
pytest   # 106 tests — 가입→키→관찰→서기 등재→주입→적응→연결/해제→웹 화면→pi 확장 스모크

# pi 확장(myviking.ts)의 컨셉을 실제로 굴러가게 검증 (node + pi 의 esbuild 재사용)
node tools/pi-extension-smoke.mjs            # 22 checks — 워커는 /observe, 서기는 /inbox·/remember
```

확장 스모크는 서버도 pi 도 필요 없습니다 — `jv` 가 까는 TS 템플릿을 실제로 import 한 뒤
pi ExtensionAPI 를 가짜로 대고 `fetch` 를 가로채서 "어떤 API 가 어떤 몸체로 불리는지" 봅니다.
(`PI_ROOT=…` 로 pi 설치 경로를 지정할 수 있습니다.)

로컬 컨테이너: `docker compose up -d`

## 메모리

- 프로젝트 삭제는 서가 전체 삭제 (확인 대화상자 있음) — 관찰함·서기 실행장도 함께 지웁니다
- **관찰 보존**: 등재/폐기된(`filed`/`skipped`) 관찰은 14일 뒤 자동 정리, 대기 관찰함은 대시보드에서 수동 폐기 가능
- 선점만 하고 45분 넘게 처리하지 않은 관찰은 자동으로 대기에 복귀 — 서기가 죽어도 일이 사라지지 않습니다
- 질문 0회 세션(브리핑만 받고 죽은 세션)은 화면에서 숨고 24시간 유예 후 정리됩니다. 관찰이 있는 세션은 지우지 않습니다.
- 데이터는 볼륨 `viking-data` 의 `index.db` 하나. 백업은 볼륨 스냅샷/`export.md`
- `legacy` 브랜치에 재건축 전 코드(17,000줄)가 보관되어 있습니다

## 라이선스

MIT