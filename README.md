# MyViking

**코딩 에이전트를 위한 셀프호스트 컨텍스트 서버.** 한 번 띄워 두면 어느 머신의
어떤 에이전트든 같은 프로젝트 지식을 공유합니다.

Langfuse 가 LLM 호출을 추적하듯 MyViking 은 **에이전트가 무엇을 알고 시작했는지**를
추적합니다. 다른 점은 저장한 것을 되돌려 준다는 것입니다 — 규칙·명령·결정·함정을
쌓아 두고, 다음 요청 때 관련 있는 것만 예산 안에서 골라 넣습니다.

```
             ┌── claude-code @ 노트북 ─┐
             ├── cursor @ 데스크톱   ─┤   HTTP/MCP    ┌──────────────────┐
             ├── codex @ CI          ─┼──────────────▶│  MyViking 서버    │
             └── 직접 만든 에이전트   ─┘                │  (당신의 머신)    │
                                                     │                  │
        같은 git remote → 같은 프로젝트 컨텍스트          │ 메모리·프롬프트    │
        결과 점수 → 메모리 신뢰도 조정                    │ 세션·추적·지표     │
                                                     └──────────────────┘
```

목표는 토큰을 줄이는 것이 아니라 **더 빨리 더 나은 답을 얻는 것**입니다. 에이전트가
매번 저장소를 다시 탐색하고, 이미 정한 규칙을 다시 묻고, 지난주에 밟은 함정을 다시
밟는 일을 없애는 쪽입니다. 토큰은 그 결과로 줄어들고, 회계는 부수적으로 남깁니다.

[volcengine/OpenViking](https://github.com/volcengine/OpenViking) 의 세 가지
아이디어에서 출발했습니다: 컨텍스트를 가상 파일시스템으로 다루기(`jarvis://`),
L0/L1/L2 티어 로딩, 세션에서 장기 메모리 증류.

---

## Docker 로 띄우기 (권장)

```bash
git clone <이 저장소> && cd my-viking
bash deploy/up.sh                # 로컬 전용 (127.0.0.1:8787)
bash deploy/up.sh --public       # 외부 노출 + API 키 자동 발급
```

`up.sh` 가 이미지를 빌드하고 기동한 뒤 헬스체크까지 확인하고, 다음에 실행할
명령을 알려줍니다. 데이터는 `myviking-data` 볼륨에 남으므로 `docker compose
down && up` 은 물론 컨테이너를 지워도 살아있습니다. 볼륨이나 호스트 자체를
잃는 경우는 아래 [백업](#백업--볼륨이-사라져도-살아남는-사본)이 커버합니다.

```
대시보드   http://127.0.0.1:8787/
API 문서   http://127.0.0.1:8787/docs
원격 MCP   http://127.0.0.1:8787/mcp
```

컨테이너 안에서 명령을 쓰려면:

```bash
cd deploy
docker compose exec myviking jv key create laptop
docker compose exec myviking jv review
docker compose exec myviking jv agent config --client claude-code --url http://내주소:8787
```

이미지는 논루트(`viking`, uid 10001)로 돌고, 컴포즈가 루트 파일시스템을 읽기
전용으로 잠그며(`/data` 볼륨과 `/tmp` 만 쓰기 가능), 기본 포트 바인딩은
`127.0.0.1` 입니다. `--public` 은 API 키를 먼저 발급한 뒤에만 외부로 엽니다.

서버 안에서 6시간마다 증류·감쇠가 돌아갑니다(`--maintain-every`, 0 이면 끔).
별도 스케줄러를 둘 필요가 없습니다.

### Docker 없이

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -e ".[all,dev]"
export PATH="$PWD/.venv/bin:$PATH"
jv serve
```

`deploy/` 에 systemd 유닛과 Caddyfile(자동 TLS)도 있습니다. 코어는 PyYAML 하나만
의존하므로 `[server]` extra 없이 설치하면 CLI 만 동작하고 `jv serve` 는 어떤
패키지가 필요한지 알려주며 종료합니다.

### 백업 — 볼륨이 사라져도 살아남는 사본

쌓이는 것은 몇 달치 프로젝트 지식입니다. 볼륨은 `down/up` 에는 살아남지만
볼륨 삭제나 호스트 장애에는 살아남지 못하므로, 서버가 **개인 구글 드라이브로
직접 백업**합니다. 서비스를 띄운 뒤 언제든 연결할 수 있습니다.

1. [Google Cloud 콘솔](https://console.cloud.google.com/apis/credentials)에서
   OAuth 클라이언트를 만듭니다 — 유형은 **"TV 및 제한된 입력 장치"**. Drive API
   를 사용 설정하고 ID·시크릿을 받아둡니다. (개인 계정이면 한 번이면 됩니다.)
2. 대시보드 첫 화면의 **백업** 패널에 넣거나, 터미널에서:

```bash
docker compose exec myviking jv backup connect \
  --client-id 123-abc.apps.googleusercontent.com --client-secret GOCSPX-...
# → 표시된 URL 을 아무 브라우저에서 열고 코드를 입력하면 연결 끝
```

서버에 브라우저가 없어도 되는 기기 코드 방식이고, 발급된 토큰은 `drive.file`
범위라 **이 앱이 만든 폴더 밖은 읽지 못합니다**. 대시보드의 백업 패널은 이
과정을 3단계(클라이언트 만들기 → 서버에 알려주기 → 계정 승인)로 안내합니다.

연결이 끝나면 **어디로 가는지가 화면에 남습니다**: 연결된 계정 이메일, 저장
폴더 이름과 Drive 바로가기 링크. "지금 백업"을 누르면 방금 올라간 파일이
원격 폴더의 실제 목록으로 바로 조회되므로, 백업이 진짜 도착했는지 서버 기록이
아니라 드라이브 쪽을 보고 확인할 수 있습니다. 이후 서버가 주기(기본
12시간)마다 스냅샷을 올리고 보관 개수(기본 14개)를 넘는 것은 지웁니다.
스냅샷은 실행 중에도 안전합니다(SQLite backup API). 그만 쓰려면
`jv backup disconnect` — 토큰만 잊고, 올라간 백업은 남습니다.

```bash
jv backup status          # 대상·주기·마지막 결과
jv backup run             # 지금 즉시 한 번
jv backup list            # 드라이브에 있는 스냅샷
jv backup restore --yes   # 최신 스냅샷으로 복원 (복원 후 컨테이너 재시작)
```

새 머신에서의 재해 복구는 세 줄입니다: `up.sh` 로 띄우고 → `jv backup connect`
로 같은 드라이브에 연결하고 → `jv backup restore --yes`.

구글 드라이브 대신 마운트한 디렉터리(NAS 등)에 남기려면
`jv backup config --provider local --path /backups` 로 바꾸고 compose 에 그
경로를 볼륨으로 추가하면 됩니다.

### 집 서버를 밖으로 여는 체크리스트 (포트포워딩)

"집에 서버를 두고 회사·카페 어디서든 붙는다"가 이 서비스의 표준 시나리오라서,
그 순서를 그대로 적어둡니다.

1. **키 먼저, 노출은 나중** — `bash deploy/up.sh --public` 은 API 키를 발급한
   뒤에만 외부 바인딩으로 엽니다. 키가 생기는 순간 전 API 가 인증을 요구하고,
   틀린 키를 반복하는 IP 는 자동으로 차단됩니다(분당 10회 초과 시 429).
2. **공유기 포트포워딩** — TLS 를 쓸 거면 80/443 만, 아니면 8787 을 이 머신으로.
3. **TLS (강력 권장)** — 평문 HTTP 로 열면 API 키와 프로젝트 지식이 그대로
   지나갑니다. 도메인이 없어도 DuckDNS 같은 무료 DDNS 면 됩니다:
   ```bash
   MYVIKING_DOMAIN=viking.duckdns.org docker compose --profile tls up -d
   ```
   Caddy 가 인증서를 자동 발급/갱신하고, 에이전트는
   `https://viking.duckdns.org/mcp` 로 붙습니다. 8787 은 포워딩하지 마세요.
4. **키는 용도별로** — `jv key create 회사노트북 --project backend` 처럼
   프로젝트 스코프를 걸어 두면, 키 하나가 새어도 그 프로젝트 밖은 못 봅니다.
   `jv key list` 로 마지막 사용 시각을 보고, 안 쓰는 키는 `jv key revoke`.
5. **백업 연결** — 위의 [백업](#백업--볼륨이-사라져도-살아남는-사본) 절 참고.
   외부에 열린 서버라면 더더욱.

### 저장소를 프로젝트에 연결

```bash
cd ~/work/backend
jv link -t coding            # git remote 와 경로를 프로젝트에 묶습니다
```

이제 어느 머신에서든 그 remote 를 가진 체크아웃은 같은 프로젝트로 해석됩니다.
`git@github.com:me/backend.git` 과 `https://github.com/me/backend` 는 같은 것으로
취급합니다.

### 에이전트 붙이기 — Claude Code 는 훅으로 (권장)

MCP 도구는 에이전트가 *호출을 선택해야* 동작합니다. 지시문을 잊거나 건너뛰면
아무것도 기록되지 않습니다. Claude Code 에는 그 선택을 없애는 길이 있습니다:

```bash
jv key create laptop         # 외부에 열 거라면 먼저 키를 발급하세요
claude mcp add --transport http myviking https://viking.example.com/mcp
cd ~/work/backend
jv agent hooks --install --url https://viking.example.com --key jv_...
```

`--install` 이 저장소의 `.claude/settings.json` 에 훅 네 개를 병합합니다.
그 뒤로는 에이전트의 협조 없이도 루프 전체가 돌아갑니다:

| 훅 | 하는 일 |
|---|---|
| **SessionStart** | 체크아웃의 git remote 로 프로젝트를 해석하고, 최근 작업·새로 정해진 것·주의·미해결을 대화에 자동 주입 — 새 세션이 곧바로 이전 맥락에서 시작합니다 |
| **UserPromptSubmit** | 모든 프롬프트를 트레이스로 기록하고(암묵 피드백도 여기서 돌아갑니다), 예산 안의 L0 컨텍스트를 주입합니다 |
| **Stop** | 대화록에서 방금 끝난 질문·답변을 추출해 자동 commit — Langfuse 처럼, 매 턴이 데이터베이스에 남습니다 |
| **SessionEnd** | 세션 상태 정리 |

훅은 전부 fail-open 입니다: 서버가 죽어 있어도 코딩 세션은 깨지지 않고,
그 턴의 기록만 빠집니다.

에이전트 지시문은 두 가지로 줄어듭니다 — 훅이 판단할 수 없는 것들입니다:

```markdown
이 저장소는 MyViking 훅이 컨텍스트 주입과 작업 기록을 자동으로 처리한다.
- 주입된 [MyViking] 블록은 이미 확인된 사실이다. 다시 조사하지 않는다.
- 작업 중 새로 확정된 규칙·명령·함정은 jarvis_remember 로 남긴다.
- 사용자가 만족/불만을 표현하면 jarvis_score 로 보고한다 (trace_id 는 블록에 있다).
```

### 훅이 없는 클라이언트 (cursor / codex / 그 외 MCP)

```bash
jv agent config --client cursor --url https://viking.example.com --key jv_...
```

출력된 MCP 설정과 **수동 지시문**을 그대로 붙이면 됩니다. 이 경우 도구만
연결하고 언제 쓸지 알려주지 않으면 에이전트는 대개 쓰지 않으므로, 지시문이
절반입니다. 수동 지시문은 jarvis_context(시작) → jarvis_remember(작업 중) →
jarvis_commit(끝) → jarvis_score(평가) 의 전체 루프를 에이전트에게 맡깁니다.

### MCP 조차 없는 에이전트 — 셸 브리지

코딩 에이전트는 종류가 많고, 전부가 MCP 를 지원하지는 않습니다. 하지만 셸
명령은 **모든** 에이전트가 실행할 수 있으므로, 같은 루프가 명령으로도
열려 있습니다:

```bash
pip install my-viking            # 에이전트 머신에 코어만 (의존성: PyYAML 하나)
export MYVIKING_URL=https://viking.example.com
export MYVIKING_KEY=jv_...

jv remote brief                  # 세션 시작: 최근 작업·주의·미해결
jv remote ctx "결제 실패 처리?"    # 작업 전: 축적된 컨텍스트 + trace_id
jv remote remember pitfalls "PG 재시도 금지" "재시도하면 이중 결제"
jv remote commit "질문" "답변 요약" --trace tr_...
jv remote score tr_... 1.0
```

프로젝트는 cwd 의 git remote 로 자동 해석되므로 `-p` 없이도 됩니다.
에이전트 지시문은 `jv agent config --client shell` 이 출력해 주며(대시보드
연결정보의 드롭다운에도 있습니다), 그 에이전트의 지시문 파일에 붙이면
끝입니다. 출력은 에이전트가 읽는 것을 전제로 설계되어 있습니다 — 컨텍스트
다음 줄에 "끝나면 이 명령을 실행하라"가 붙어 나옵니다.

## 실제로 무엇이 일어나나

노트북의 Claude Code 가 프로젝트 이름도 모른 채 git remote 만으로 붙습니다.

```json
→ jarvis_context {"repo": "https://github.com/me/backend",
                  "question": "결제 승인 실패하면 재시도해야 하나?"}

← {"project": "backend", "trace_id": "tr_493f17…", "reused": false,
   "context_ms": 67,
   "items": [{"tier":"L0","tokens":31,"uri":"…/memories/pitfalls/pg-재시도-금지"},
             {"tier":"L1","tokens":35,"uri":"…/memories/commands/테스트-실행"}],
   "note": "위 컨텍스트는 이 프로젝트에 대해 이미 확인된 내용입니다. 다시 조사하지
            말고 여기서 시작하세요."}
```

작업이 끝나면 결과와 평가를 돌려줍니다.

```json
→ jarvis_commit {"trace_id": "tr_493f17…", "answer": "재시도하지 않습니다…",
                 "latency_ms": 2400}
→ jarvis_score  {"trace_id": "tr_493f17…", "value": 1.0, "comment": "정확"}
← {"memories_adjusted": ["…/pitfalls/pg-재시도-금지", "…/commands/테스트-실행"]}
```

데스크톱의 Cursor 가 같은 질문을 하면 재사용됩니다.

```json
→ jarvis_context {"repo": "git@github.com:me/backend.git", "question": "…"}
← {"reused": true, "similarity": 1.0, "context_ms": 0,
   "answer": "재시도하지 않습니다. 응답 코드가 0000 이 아니면 실패로 확정합니다."}
```

## 사람이 하는 일은 두 번뿐

MyViking 은 사람이 들어가서 작업하는 서비스가 아닙니다. 코딩 에이전트를 위한
인프라이고, 사람의 몫은 **프로젝트를 만들고 연결정보를 가져가는 것**까지입니다.

### 처음 한 번

대시보드(`http://localhost:8787/`)를 열면 첫 화면이 프로젝트 목록입니다.

1. **새 프로젝트** — 이름, 메모리 스키마, git remote(선택)를 넣고 만들기.
2. 카드를 클릭하면 **연결정보**가 나옵니다. 블록을 복사하면 끝입니다.
   - `claude mcp add --transport http myviking https://…/mcp` — 도구 연결
   - **자동 캡처 훅** (claude-code) — `.claude/settings.json` 에 병합.
     이게 기록을 에이전트의 선의에서 떼어내는 블록입니다.
   - 에이전트 지시문 — 저장소의 `CLAUDE.md` 에 붙여넣기

클라이언트는 드롭다운에서 고릅니다 (claude-code / cursor / codex / 그 외 MCP).
API 키를 켜 두었다면 입력란에 넣으면 설정에 자동으로 포함됩니다.

훅이 없는 클라이언트에서는 **지시문 블록을 빼면 아무 일도 일어나지 않습니다.**
도구는 연결되지만 에이전트가 호출할 이유를 모르고, 지식은 계속 비어 있습니다.

### 그다음부터

없습니다. 그 저장소에서 평소처럼 작업하면 됩니다.

```
당신: "결제 승인 실패 처리 좀 봐줘"
  → [훅] 세션 첫 프롬프트면 이전 작업 브리핑 자동 주입
  → [훅] 관련 규칙·함정 컨텍스트 주입 + 트레이스 기록
  → 에이전트가 작업, 확정된 것은 jarvis_remember
  → [훅] 턴이 끝나면 질문·답변 자동 commit → 증류
  → 다음 프롬프트가 직전 답을 암묵적으로 채점
```

## 저장소가 스스로 정리되는 규칙

사람이 검토하지 않아도 품질이 유지되도록, 네 가지가 자동으로 돕니다.

| 규칙 | 동작 |
|---|---|
| **쓰이면 강화** | 검색에 포함될 때마다 신뢰도가 오르고, 다음에 더 먼저 선택됩니다 |
| **결과로 조정** | `jarvis_score` 가 그 작업에 쓰인 지식의 신뢰도를 올리거나 내립니다 |
| **안 쓰이면 잊힘** | 오래 쓰이지 않으면 신뢰도가 감쇠하고, 바닥을 치면 `_archive/` 로 |
| **나중 것이 대체** | 모순된 지시가 오면 나중 것이 이깁니다. 이전 것은 보관되고 검색에서 빠집니다 |

마지막 규칙이 핵심입니다. "커밋은 한글로" 다음에 "커밋은 영어로"가 오면, 나중
지시가 현재 규칙이 되고 이전 것은 보관함으로 갑니다 — 에이전트가 서로 반대되는
두 규칙을 동시에 보는 일이 없습니다. 파일명도 내용에 맞게 따라갑니다.

되돌릴 수 있습니다. 보관된 파일에 무엇으로 대체됐는지 기록되고, 카테고리
디렉터리에 그대로 남아 있습니다. 사람이 판단하고 싶다면
`jv config --set learn.conflict_policy=flag` 로 양쪽을 남기고 표시만 하게
바꿀 수 있습니다.

세션 기록은 컨텍스트에 싣지 않습니다. 세션에서 가치 있는 것은 이미 메모리로
증류되었으므로, 원문까지 넣으면 같은 내용을 두 번 싣고 폐기된 지시가 대화록에
인용된 채 읽힙니다. 반복 질문은 답변 캐시가 따로 처리합니다.

## 화면 세 개

| 탭 | 무엇을 하나 |
|---|---|
| **프로젝트** | 프로젝트 생성, 연결정보 복사, git remote 등록. **이게 전부입니다** |
| **지식** | 에이전트가 쌓은 것 확인. 원하면 고칠 수 있지만 필수는 아닙니다 |
| **활동** | 답이 이상하거나 느렸을 때. 단계별 지연과 그 답에 쓰인 지식 |

**지식** 탭 아래의 "점검이 필요한 것"에는 **자동 규칙이 정할 수 없는 것만**
올라옵니다 — 미해소 상충, 나쁜 평가를 받은 지식, 여러 번 쓰였지만 평가가 없는
지식. "에이전트가 기록했고 사람이 보지 않았다"는 것은 정상 상태이므로 세지
않습니다. 보통은 0건입니다.

```bash
jv review              # 0건이 기본입니다
jv review --all        # 감사하고 싶을 때: 사람이 보지 않은 것까지 전부
```

## 다음 프롬프트가 진짜 채점표입니다

명시적 평가는 거의 남지 않습니다. 하지만 **같은 일을 다시 시키는 행동은 거짓말을
하지 않습니다.** "A기능이 구현이 안되었는데 제대로 동작하도록 해줘" 는 직전 작업이
실패했다는 진술이고, 다른 주제로 넘어간 것은 그럭저럭 됐다는 뜻입니다.

그래서 한 자리 안에서 다음 요청이 들어오면 **직전 답변을 자동으로 채점합니다.**
아무도 아무것도 누르지 않아도 됩니다.

| 다음 요청 | 판정 | 값 | 근거 |
|---|---|---|---|
| 교정 요청 ("안 되는데 고쳐줘", "여전히", "still doesn't work") | **다시 요청됨** | 0.1 | 문구 |
| 같은 요청이 곧 다시 (주제 일치) | **같은 요청 반복** | 0.35 | 주제 일치 |
| 다른 주제로 넘어감 | **넘어감** | 0.62 | 주제 불일치 |
| "그럼 …", "추가로 …" | 판정 없음 | — | 이어지는 질문 |

`jv metrics` 의 **한 번에 해결** 이 그 결과입니다.

```
한 번에 해결 83%  (다시 요청 1 / 판정 6건)
  판정 근거: 넘어감 5, 다시 요청됨 1
```

세 가지를 신중하게 다뤘습니다.

**넘어간 것은 약한 신호입니다.** 잘 됐어서 넘어간 것과 포기하고 직접 한 것이
여기서는 똑같이 보입니다. 그래서 긍정(0.62)은 부정(0.1)보다 중심에서 훨씬 가깝고,
암묵적 신호 전체가 명시적 평가보다 절반 세기로만 반영됩니다
(`learn.implicit_strength`). 사람이 직접 준 점수는 절대 덮어쓰지 않습니다.

**"실패"는 불만이 아닐 수 있습니다.** 결제 프로젝트에서 "결제 승인 실패하면
재시도해야 하나?" 는 주제어를 쓴 질문입니다. 그래서 교정 표현(주제와 무관하게
직전 작업을 가리킴)과 장애 어휘(주제가 같을 때만 불만)를 분리했습니다 — 이걸
합쳐 뒀을 때 정상적인 답변이 실패로 기록되는 것을 실제로 확인했습니다.

**다시 요청된 일은 다음 세션이 먼저 압니다.** `catch_up.open_threads` 와
`jv brief` 에 올라오고, 대시보드 지식 탭에도 패널이 있습니다. 새 세션에 넘길
가장 유용한 정보는 "전부 이렇습니다" 가 아니라 **"지난번에 두 번 물었고 아직
안 끝났을 수 있습니다"** 입니다.

```bash
jv metrics -p backend        # 한 번에 해결 / 다시 요청
jv history -p backend        # 세션별로 각 요청의 결말까지
jv config --set learn.implicit_feedback=false   # 끄기
```

## 점수는 지표로 끝나지 않습니다

에이전트가 `jarvis_score` 를 호출하면 **그 작업에 어떤 지식이 들어가 있었는지
기록되어 있으므로**, 같은 신호가 신뢰도를 움직입니다. 이게 모니터링과 학습을
가르는 지점이고, 사람 없이 품질이 오르는 이유입니다.

- 좋은 결과에 함께 있던 지식 → 검색 기여도에 비례해 상승
- 나쁜 결과를 **실제로 주도한** 지식 → 하락 → 임계 아래면 `_archive/` 로

책임은 배분하지 않고 **귀속**합니다. 검색은 관련도 순으로 10여 개를 넘기는데,
오답은 거의 항상 상위 항목이 원인입니다. 전부 똑같이 깎으면 옆에 있었을 뿐인
지식이 자기와 무관한 실패의 손해를 누적합니다. 그래서 조정폭을 기여도로 가중하고,
**부정 평가는 상위 기여자에게만** 닿습니다. 무엇이 틀렸는지 알면 `uris` 로
지목하면 그것만 움직입니다. 전역 선호는 당신의 지시이므로 점수로 깎이지 않습니다.

하락 가중치를 상승보다 크게 두었습니다(-0.44 vs +0.24). 확신을 갖고 틀린 지식이
없는 지식보다 더 해롭기 때문입니다.

`jv impact -p backend` 는 어떤 지식이 좋은 결과에 기여했는지 보여줍니다. 자주
쓰이지만 점수가 없는 지식은 *유용한 것이 아니라 검증되지 않은 것*입니다.

## 정말 "나만의" Jarvis로 만드는 세 가지

컨텍스트 DB 는 물어봐야 답합니다. 어시스턴트는 그 이상을 합니다.

### 1. 전역 선호 — 매 프로젝트에서 다시 말하지 않기

프로젝트가 아니라 *당신*에 관한 것은 `jarvis://global` 에 두고, 모든 프로젝트의
컨텍스트에 함께 실립니다.

```bash
jv me add "답변과 주석은 항상 한글로 작성한다"
jv me add "설명은 짧게, 근거를 먼저 말한다"
jv me add "추측한 명령을 알려주지 말고 확인된 것만 말한다"
jv me list
```

에이전트도 직접 넣을 수 있습니다: `jarvis_remember` 에 `project="global"`.
당신이 직접 쓴 것이므로 검토 대기에 오르지 않고 처음부터 높은 신뢰도를 갖습니다.

### 2. 선제적 경고 — 이미 밟은 함정을 앞에 세우기

프로파일이 **경고 카테고리**를 정합니다(`coding` 은 `pitfalls`, `ops` 는
`incidents`, `research` 는 `contradictions`). 여기 걸리는 메모리는 일반 컨텍스트에
섞이지 않고 프롬프트 맨 앞의 `⚠ 주의` 블록으로 올라가고, 시스템 프롬프트가
"이것을 거스르는 제안은 하지 마세요" 로 지시합니다.

```
# 아래 '주의' 항목을 먼저 읽고, 거스르는 제안은 하지 마세요: PG 재시도 금지

# 컨텍스트
## ⚠ 주의 — 이 프로젝트에서 이미 밟은 함정
### PG 재시도 금지
승인 응답이 0000 이 아니면 재시도하지 않는다
...
```

텍스트를 두 번 싣지 않습니다 — 강조는 위치와 제목으로 하고, 본문은 한 곳에만
둡니다. 어떤 카테고리를 경고로 볼지는 `profile.yaml` 의 `warn: true` 로 바꿉니다.

### 3. 프로젝트 간 회상 — "저번에 어떻게 했었지"

프로젝트는 서로 격리됩니다. 한 프로젝트의 사실이 다른 프로젝트의 사실로 오해되면
안 되기 때문입니다. 하지만 개인 어시스턴트가 답할 수 있는 가장 유용한 질문이
"이거 저번에 어떻게 풀었지"이고, 그 답은 보통 다른 저장소에 있습니다.

그래서 찾아보되 **크게 라벨을 붙입니다**: 요약만, 최대 3건, 그리고

```
# 다른 프로젝트에서 온 참고 — 이 프로젝트에서 검증된 것이 아닙니다
- [infra] 중복 승인 대응: 중복 승인이 발생하면 정산 배치를 멈추고 수동 취소한다
```

이 프로젝트의 컨텍스트 블록에는 들어가지 않습니다. 끄려면 `cross_project=false`.

### 리듬

```bash
jv brief -p backend     # 오랜만에 돌아왔을 때: 확립된 지식·주의사항·미해결
jv digest               # 주기적으로: 전체 프로젝트 한 화면
jv review               # 확인이 필요한 것 (위 두 개가 여기로 안내합니다)
```

`jv brief` 는 한 달 만에 프로젝트로 돌아왔을 때 읽는 것입니다. 전부 쏟아내지 않고
신뢰도 높은 지식, 서 있는 경고, 미해결 결정, 최근 작업으로 나눠 보여줍니다.
에이전트도 `jarvis_profile` 의 `op="brief"` 로 같은 것을 받습니다.

## 프로젝트마다 다른 기억

코드 프로젝트와 리서치 프로젝트는 *다른 종류의* 기억이 필요합니다. 프로젝트마다
`profile.yaml` 로 카테고리·추출 기준·보관 정책·토큰 예산을 따로 정의합니다.

| 템플릿 | 카테고리 |
|---|---|
| `coding` | conventions, architecture, commands, pitfalls, decisions, cases |
| `research` | questions, findings, sources, contradictions |
| `writing` | voice, audience, outline, phrasing |
| `ops` | topology, runbooks, incidents, thresholds |
| `default` | preferences, facts, patterns, cases |

```yaml
- name: commands
  description: 빌드·테스트·배포 명령
  extract: 실제로 동작이 확인된 명령과 전제 조건. 추측한 명령은 저장 금지.
  priority: 9          # 검색 예산 경쟁에서의 우선순위
  keep: 20             # 이 수를 넘으면 약한 것부터 보관함으로
  cumulative: true     # true = 같은 주제는 한 파일에 누적
```

`extract` 는 증류기에게 그대로 전달되는 지시문입니다. 같은 세션이라도 프로파일이
다르면 다른 카테고리에 다른 형태로 저장됩니다. `fallback` 은 어떤 규칙에도 걸리지
않은 관찰이 갈 곳입니다 — 이게 없으면 학습 루프가 도는 것처럼 보이면서 아무것도
쌓이지 않습니다.

`jarvis://global` 은 모든 프로젝트에 적용되는 선호를 담습니다. 프로젝트 메모리는
서로 섞이지 않습니다.

## 왜 빨라지나

세 가지가 각각 다른 이유로 기여합니다.

**1. 탐색을 건너뜁니다.** 규칙·명령·구조를 이미 알고 시작하므로 에이전트가 파일을
훑는 왕복이 사라집니다. 이게 가장 큰 체감 차이이고, 토큰 절감은 그 부산물입니다.

**2. 반복 질문은 재사용합니다.** 정확 일치는 정규화된 해시로, 표현만 다른 질문은
벡터 유사도로. 적중 시 유사도와 원 질문을 함께 보여주므로 눈으로 확인할 수 있습니다.
재사용 응답은 3~4ms 입니다.

**3. 컨텍스트 조립 자체가 빠릅니다.** 1500개 노드에서 p50 50ms. 여기까지 오면서
측정으로 네 가지를 고쳤습니다.

| 문제 | 원인 | 결과 |
|---|---|---|
| 적재가 사실상 멈춤 | 쓰기마다 스코프 전체 재스캔해 디렉터리 중심점 재계산 | 조상만 갱신(O(depth)). 1500건 10분+ → 27초 |
| 검색이 코퍼스 크기에 비례 | 스코프 전체를 가져와 파이썬에서 폐기 | SQL 에서 좁힘 |
| pack 시간의 38% | 항목마다 YAML frontmatter 재파싱 | L0/L1 은 색인에서, L2 는 YAML 없이 |
| 벡터 점수화 | 인터프리터 루프 | 배치화 (numpy 있으면 사용) |
| 큰 카테고리에서 다시 전수 스캔 | 진입한 디렉터리가 수천 개를 포함 | 후보 상한 + 디렉터리별 슬라이스 (`capped` 로 표시) |
| 한국어 검색이 불용어로만 점수 | 조사 때문에 "배포는"≠"배포", trigram 이 안 겹침 | CJK 문자 bigram (색인 자동 재생성) |
| prepare 시간의 56% | 강화가 카운터 하나 올리려 파일 재파싱 | frontmatter 직접 패치 + 일괄 커밋 |

1500 노드 기준 `pack` p50 **61ms**, `prepare`(경고·타프로젝트 포함) p50 **81ms**,
재사용 응답 **4ms**. `numpy` 는 선택이지만 검색 지연을 3배 낮춥니다 — 없어도
**같은 결과**로 동작합니다.

## 토큰 회계 (부수적으로)

여전히 측정해 남깁니다. 캐시 절감과 티어링 절감을 분리하고, 기준선은 가정이 아니라
*같은 항목을 전문으로 실었을 때의 실측값*입니다.

```
$ jv ask -p backend "결제 승인 실패 시 재시도와 웹훅 서명은?" 2>&1 >/dev/null
사용 1264 / 동일 항목 전체 로드 5583 / 관련 전량 덤프 5583 토큰 · 절감 77.4%
```

절감률은 코퍼스에 달려 있습니다. 작은 메모리 몇 개만 있으면 L1 과 L2 가 같아서
절감이 0% 로 나오는 것이 정상입니다.

## 보안

로컬 시험 중에는 열려 있습니다. **첫 키를 만드는 순간 전체 표면이 인증을 요구합니다** —
반쯤 보호되는 상태를 잘못 읽을 여지를 두지 않았습니다.

```bash
jv key create laptop                     # 전체 접근
jv key create ci --project backend       # 이 프로젝트만
jv key list
jv key revoke key_a1b2c3
```

- 키는 해시로 저장되고 평문은 발급 시 1회만 보입니다.
- 검증은 상수 시간 비교입니다.
- `/health` 와 대시보드는 키 없이도 열립니다(대시보드는 브라우저에 키를 보관).
- `--host 0.0.0.0` 으로 열면서 키가 없으면 `jv serve` 가 경고합니다.
- 외부 노출은 `deploy/Caddyfile` 로 TLS 를 앞에 두는 것을 권합니다.

## 새 세션이 프로젝트를 빠르게 파악하는 방법

Langfuse 가 트레이스를 세션으로 묶는 것처럼, MyViking 은 작업을 **한 자리(sitting)**
단위로 묶고 그것을 다음 세션에 넘깁니다.

**세션 시작 시 `jarvis_brief`** — 확립된 지식, 서 있는 주의사항, 최근에 무슨 작업을
했고 무엇이 새로 정해졌는지, 아직 미해결인 것을 한 번에 돌려줍니다. 저장소를
처음부터 훑는 대신 여기서 시작합니다.

**첫 호출에 자동으로 따라오는 `catch_up`** — 세션의 첫 `jarvis_context` 응답에만
붙습니다. 에이전트가 별도로 호출할 것을 기억하지 않아도 오리엔테이션을 받습니다.

```json
"catch_up": {
  "last_worked_on": "2026-09-01T14:22",
  "recent_threads": [
    {"agent": "cursor@desktop", "questions": ["결제 확정 흐름이 어떻게 되어 있어?"], "score": 1.0},
    {"agent": "claude-code@macbook", "questions": ["테스트는 어떻게 돌려?", "배포는 어떻게 해?"]}
  ],
  "new_since_last_time": [{"category": "pitfalls", "title": "PG 승인 실패 시 재시도 금지"}],
  "unsettled": []
}
```

**"지난번에 뭘 하다 말았지"는 `jarvis_history`** — 세션 단위로 질문·답변·평가가
시간순으로 나옵니다. 대시보드의 **활동 > 작업 세션** 이 같은 것을 보여줍니다.

세션 id 를 넘기지 않아도 됩니다. 없으면 서버가 `에이전트@날짜` 로 한 자리를
유도합니다 — 에이전트에게 세션 관리를 요구하면 그냥 무시되고, 그러면 "지난번"을
묶을 근거가 사라집니다.

```bash
jv brief -p backend          # 사람이 읽는 같은 내용
jv history -p backend -v     # 세션 단위 기록
```

## MCP 도구 9개

도구가 스무 개면 에이전트가 선택에 주의를 씁니다. 이 7개가 루프를 덮습니다.

| 도구 | 용도 |
|---|---|
| `jarvis_brief` | **세션 시작 시.** 프로젝트 파악 — 지식·주의사항·최근 작업·미해결 |
| `jarvis_history` | 이전 작업을 세션 단위로 되짚기 |
| `jarvis_context` | 개별 작업 시작 시. 재사용 확인 + 예산 내 컨텍스트 + `trace_id` |
| `jarvis_remember` | 다음에도 쓸 지식 기록 |
| `jarvis_commit` | 작업 종료 시. 세션 기록 + 증류 |
| `jarvis_score` | 결과 평가 → 기여도에 따라 신뢰도 반영 (`uris` 로 지목 가능) |
| `jarvis_browse` | `ls`/`tree`/`find`/`grep`/`read` |
| `jarvis_prompt` | 저장된 프롬프트 목록/렌더 |
| `jarvis_profile` | brief·프로파일·검토·지표·추적·기여도·에이전트 |

로컬 stdio 도 그대로 씁니다 (같은 도구 표면):

```bash
claude mcp add myviking -- /경로/.venv/bin/python -m jarvis.mcp_server
```

## 저장 구조

```
$JARVIS_HOME/                      # 기본 ~/.jarvis
├── jarvis.yaml                    # 설정
├── index.db                       # SQLite: 색인 + 추적 + 키 (jv reindex 로 재생성)
├── global/                        # 모든 프로젝트에 적용되는 선호
└── projects/backend/
    ├── profile.yaml               # 이 프로젝트의 메모리 스키마
    ├── memories/{카테고리}/*.md
    ├── prompts/*.md               # _versions/ 에 이전 버전 자동 보관
    ├── sessions/{날짜}/*.md
    ├── resources/*.md
    └── _archive/…                 # 감쇠·캡·낮은 점수로 밀려난 것 (되돌릴 수 있음)
```

**파일이 진실의 원천입니다.** 왜 그렇게 답했는지 궁금하면 파일을 열면 됩니다.
`index.db` 는 언제든 버리고 `jv reindex` 로 다시 만들 수 있습니다.

## Python / HTTP API

```python
from jarvis import Jarvis

j = Jarvis()
prepared = j.prepare("backend", question, agent="my-bot@ci")

if prepared.cache_hit:
    answer = prepared.cache_hit.answer
else:
    answer = my_llm(prepared.messages)

j.commit("backend", question, answer, trace_id=prepared.trace_id, latency_ms=elapsed)
j.score(prepared.trace_id, "helpfulness", 1.0)
```

HTTP: `POST /prepare` · `POST /commit` · `POST /scores` · `GET /traces` ·
`GET /metrics` · `POST /resolve` · `POST /mcp` · `GET /docs` (전체 OpenAPI).

## 명령어

```
jv serve [--host 0.0.0.0]             서버 (대시보드 + API + 원격 MCP)
jv link [-p 프로젝트]                  현재 저장소를 프로젝트에 연결
jv agent config --client claude-code  연동 설정과 지시문 생성
jv agent list                         연결된 에이전트
jv key create|list|revoke             API 키

jv metrics [-p 프로젝트]               속도·재사용·품질
jv traces [-p 프로젝트] [--slower-than 1000]
jv trace <id>                         단계별 지연과 사용된 컨텍스트
jv score <id> <0~1> [--comment]       평가 → 메모리 신뢰도 반영
jv impact -p 프로젝트                  어떤 메모리가 좋은 결과에 기여했나

jv me add|list|forget                 모든 프로젝트에 적용되는 내 선호
jv brief -p 프로젝트                   오랜만에 돌아왔을 때 알아야 할 것
jv history [-p 프로젝트] [-v]          이전 작업을 세션 단위로 되짚기
jv digest                             전체 프로젝트 요약
jv maintain                           증류·감쇠 일괄 (서버가 자동 실행)
jv review [-p 프로젝트] [--all]        자동 규칙이 정할 수 없는 것 (보통 0건)
jv mem show|confirm|edit|forget <uri>  메모리 확인·수정·보관

jv init <프로젝트> -t <템플릿>          프로젝트 생성
jv project list|profile|templates
jv prompt save|list|show|render|versions|rollback|delete
jv mem add|list|forget|feedback
jv resource add                       문서를 티어링해 등록
jv ask -p 프로젝트 "<질문>"             컨텍스트 조립 (CLI 에서 직접)
jv commit / jv distill
jv ls|tree|find|grep|read             저장소 탐색 (find --trace 로 검색 경로)
jv report|stats|cache|sessions        토큰 회계
jv reindex|config
```

## LLM 붙이기 (선택)

없어도 **완전히 동작합니다**. 붙이면 요약과 증류 품질만 올라갑니다.

```bash
jv config --set llm.provider=anthropic \
          --set llm.model=claude-sonnet-5 \
          --set llm.api_key_env=ANTHROPIC_API_KEY
```

`anthropic` / `openai` / `volcengine` / `ollama`. 임베딩도 같은 방식으로
교체할 수 있고(`embed.provider`), 기본값은 오프라인 해시 임베딩입니다. 문자
n-gram 이라 한국어를 토크나이저 없이 처리합니다.

## 개발

```bash
uv pip install --python .venv -e ".[all,dev]"
.venv/bin/python -m pytest -q        # 317 tests
.venv/bin/ruff check src tests
bash examples/quickstart.sh          # 전체 루프 시연
```

## 설계 노트

- **디렉터리 우선 검색.** 디렉터리 중심점을 먼저 점수화하고 상위 몇 개에만
  들어갑니다. 중심점은 정규화 전 합계로 보관해 쓰기가 O(depth) 입니다.
- **안정적인 파일명.** OpenViking 은 메모리를 `mem_{uuid}.md` 로 씁니다
  ([RFC #1251](https://github.com/volcengine/OpenViking/issues/1251) 에서 논의 중).
  세션을 넘어 참조할 수 없으면 누적 메모리가 쌓일 자리가 없으므로, 여기서는 제목에서
  파일명을 만들고 같은 제목은 같은 파일에 누적합니다.
- **상충은 덮어쓰지 않습니다.** 같은 주제인데 부정 극성이 다른 관찰이 오면 병합하되
  `⚠ 기존 내용과 상충` 을 남기고 양쪽을 보존합니다.
- **잊는 것도 기능.** 감쇠·보관 캡·낮은 점수로 밀려난 메모리는 삭제가 아니라
  `_archive/` 로 이동합니다.
- **티어 단조성.** L0 ≤ L1 ≤ L2 를 쓰기 시점에 강제합니다. 이게 깨지면 "절감"이
  음수로 나옵니다.
- **한국어 토큰 계상.** `len/4` 규칙은 한글을 2.5배쯤 과소 계상하므로 스크립트를
  구분해 추정합니다.
- **한국어 조사.** 조사가 어간에 붙기 때문에 단어·trigram 단위 특징은 원형과 절대
  겹치지 않습니다("배포는" vs "배포"). 측정해 보니 "배포는 어떻게 해?" 가 배포 노트와
  무관한 테스트 노트에 **동일한 점수**를 주고 있었습니다 — 주제어가 기여를 못 하고
  불용어만 기여한 것입니다. CJK 는 문자 bigram 을 쓰고, 임베딩 방식이 바뀌면 색인을
  자동으로 다시 만듭니다(낡은 색인은 조용히 품질만 떨어뜨립니다).
- **책임은 배분하지 않고 귀속.** 부정 평가는 검색 1위와 사실상 동률인 것에만
  닿습니다. 상대 임계를 느슨하게(0.6) 뒀을 때 옆에 있었을 뿐인 지식이 실제
  원인보다 더 깎이는 것을 확인했습니다.
- **예산은 약속.** 렌더링된 실제 텍스트를 측정해 초과하면 점수가 낮은 항목부터
  잘라냅니다.

라이선스: MIT
