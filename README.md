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
명령을 알려줍니다. 데이터는 `myviking-data` 볼륨에 남으므로 컨테이너를 지워도
살아있고, 백업은 이 볼륨만 챙기면 됩니다.

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

### 저장소를 프로젝트에 연결

```bash
cd ~/work/backend
jv link -t coding            # git remote 와 경로를 프로젝트에 묶습니다
```

이제 어느 머신에서든 그 remote 를 가진 체크아웃은 같은 프로젝트로 해석됩니다.
`git@github.com:me/backend.git` 과 `https://github.com/me/backend` 는 같은 것으로
취급합니다.

### 에이전트 붙이기

```bash
jv key create laptop         # 외부에 열 거라면 먼저 키를 발급하세요
jv agent config --client claude-code --url https://viking.example.com --key jv_...
```

출력된 명령과 **에이전트 지시문**을 그대로 붙이면 됩니다. 도구만 연결하고 언제
쓸지 알려주지 않으면 에이전트는 대개 쓰지 않으므로, 지시문이 절반입니다.

```markdown
이 저장소에서 작업할 때는 MyViking 을 컨텍스트 원천으로 사용한다.
1. 작업을 시작하기 전에 jarvis_context 를 호출한다 (repo=git remote URL).
   반환된 컨텍스트는 이미 확인된 사실이므로 다시 조사하지 않는다.
   reused=true 로 오면 이전 답변이므로 유효성만 확인하고 재사용한다.
2. 새로 확정된 규칙·명령·함정은 jarvis_remember 로 남긴다.
3. 작업을 마치면 jarvis_commit 에 trace_id 와 함께 결과를 기록한다.
4. 사용자가 만족했거나 수정을 요구했으면 jarvis_score 로 알린다.
```

`--client cursor` / `--client codex` 도 같은 방식입니다.

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

## 어떻게 쓰는 건가 — 하루 흐름

핵심은 **작업 중에는 이걸 안 본다는 것**입니다. 세 가지 시점으로 나뉩니다.

### 처음 한 번 (5분)

```bash
jv serve --host 0.0.0.0                       # 서버 상시 실행 (deploy/ 에 systemd 유닛)
jv key create laptop                          # 외부에 열면 키 필수
cd ~/work/backend && jv link -t coding        # 이 저장소 = 이 프로젝트
jv agent config --client claude-code --url https://viking.example.com --key jv_...
```

마지막 명령이 출력하는 **MCP 등록 명령 + 에이전트 지시문**을 붙입니다. 지시문이
절반입니다 — 도구만 연결하고 언제 쓸지 알려주지 않으면 에이전트는 안 씁니다.

이미 알고 있는 것을 몇 개 넣어두면 첫날부터 효과가 있습니다.

```bash
jv mem add -p backend commands "테스트 실행" "pytest -q 로 전체 테스트를 돌린다" \
   --detail "루트에서. PYTHONPATH=src 필요"
jv mem add -p backend pitfalls "PG 재시도 금지" \
   "승인 응답이 0000 이 아니면 재시도하지 않는다" --detail "재시도하면 중복 승인"
```

### 작업 중 (당신은 아무것도 안 함)

에이전트가 알아서 호출합니다. 당신은 평소처럼 Claude Code / Cursor 를 씁니다.

```
당신: "결제 승인 실패 처리 좀 봐줘"
  → 에이전트가 jarvis_context 호출  (규칙·구조·과거 결정을 받음)
  → 파일 탐색 왕복 없이 바로 작업
  → 끝나면 jarvis_commit + jarvis_score
```

대시보드를 열어둘 필요가 없습니다. 열어도 볼 게 없습니다.

### 나중에 (주 1~2회, 5분)

여기가 **당신이 개입하는 유일한 지점**이고, 이게 DB 품질을 결정합니다.

```bash
jv review                # 확인이 필요한 것만 요약
```

```
확인이 필요한 항목 4건

  확인 대기        3건  에이전트가 기록했고 아직 사람이 보지 않았습니다
  상충           1건  같은 주제에 반대되는 내용이 들어왔습니다
```

브라우저를 열면 같은 것이 첫 화면입니다: `http://localhost:8787/` → **검토** 탭.
각 항목을 클릭해 **맞음 — 확인** / **수정 저장** / **보관** 중 하나를 누릅니다.
터미널로도 됩니다.

```bash
jv review -p backend                          # 목록과 이유
jv mem show <uri>                             # 상세 (출처 세션·기여도·파일 경로)
jv mem confirm <uri>                          # 맞음 → 신뢰도 0.85, 큐에서 제거
jv mem edit <uri> --statement "커밋 메시지는 한글로 쓴다" --category conventions
jv mem forget <uri>                           # 보관함으로 (파일은 남음)
```

**왜 검토가 필요한가.** 에이전트가 대화에서 뽑아낸 것은 *추정*입니다. 사람이 쓴
것과 구분해서 표시하고, 확인받기 전까지는 낮은 신뢰도로 둡니다. 검토 큐가 올리는
이유는 다섯 가지이고, 각각이 요구하는 결정이 다릅니다.

| 이유 | 무엇을 결정해야 하나 |
|---|---|
| **상충** | 같은 주제에 반대되는 내용이 들어왔습니다. 어느 쪽이 맞는지 정하세요 |
| **나쁜 결과** | 이 메모리가 들어간 작업들의 평가가 낮습니다. 틀렸는지 확인하세요 |
| **미검증** | 여러 번 쓰였지만 평가가 없습니다. 자주 쓰이는 것과 맞는 것은 다릅니다 |
| **확인 대기** | 에이전트가 기록했고 아직 아무도 보지 않았습니다 |
| **잊히는 중** | 오래 안 쓰여 신뢰도가 떨어졌습니다. 필요하면 확인해 살리세요 |

방치해도 시스템은 돕니다. 확인하지 않은 메모리는 낮은 신뢰도로 남고, 안 쓰이면
감쇠해 `_archive/` 로 갑니다. 검토는 **품질을 올리는 일**이지 유지보수 잡무가
아닙니다.

## 화면 네 개

| 탭 | 언제 여나 |
|---|---|
| **검토** | 주 1~2회. 에이전트가 기록한 것을 확인·수정 (기본 화면) |
| **데이터베이스** | 내 에이전트가 뭘 알고 있는지 볼 때. 카테고리별로 훑고 바로 수정 |
| **작업 기록** | 뭔가 느리거나 답이 이상했을 때. 단계별 지연과 그 답에 쓰인 컨텍스트 |
| **프롬프트** | 저장해 둔 지시문 확인 |

**데이터베이스** 탭이 "나만의 agent database" 그 자체입니다. 왼쪽에 프로파일
카테고리, 오른쪽에 메모리 목록. 클릭하면 편집기가 열립니다 — 제목(파일명),
카테고리, 요약(L0, 검색에서 먼저 읽히는 문장), 본문(L2), 신뢰도. 저장하면
검색에 즉시 반영되고, 카테고리를 바꾸면 파일이 실제로 이동합니다.

**작업 기록** 탭의 트레이스 상세에서는 그 답에 들어간 컨텍스트가 링크로 나옵니다.
"이 답이 왜 이렇게 나왔지?" → 클릭 → 바로 그 메모리를 수정할 수 있습니다.

### 작업 기록 탭의 지표

지연을 **조립(우리 몫)** 과 **생성(모델 몫)** 으로 나눠 기록합니다. 이 구분이 없으면
느린 요청을 보고도 어느 쪽을 고쳐야 할지 알 수 없습니다.

```
$ jv metrics -p backend
프로젝트: backend · 최근 7일 · 작업 2건 (오류 0)

응답 시간   p50 0ms · p95 2467ms
  재사용    p50 0ms
  생성      p50 2467ms
컨텍스트 조립 p50 0ms · p95 67ms

재사용률 50.0% (1/2)
점수: helpfulness 1.00 (1건)

단계별 평균:
  generation    2400ms  (최대 2400ms, 1회)
  retrieval       61ms  (최대 61ms, 1회)
  distill          7ms  (최대 7ms, 1회)
```

## 점수는 대시보드로 끝나지 않습니다

이 부분이 모니터링과 학습을 가르는 지점입니다. 트레이스에 점수를 남기면 **그 작업에
어떤 컨텍스트가 들어가 있었는지 기록되어 있으므로**, 같은 신호가 메모리 신뢰도를
움직입니다.

- 좋은 결과에 함께 있던 메모리 → 신뢰도 상승 → 다음에 더 먼저 선택됨
- 나쁜 결과에 함께 있던 메모리 → 하락 → 임계 아래로 떨어지면 `_archive/` 로 이동

하락 가중치를 상승보다 크게 두었습니다(-0.44 vs +0.24). 확신을 갖고 틀린 메모리가
없는 메모리보다 더 해롭기 때문입니다.

`jv impact -p backend` 는 무엇을 남기고 무엇을 고쳐야 하는지 보여줍니다. 자주
쓰이지만 점수가 없는 메모리는 *유용한 것이 아니라 검증되지 않은 것*입니다.

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

## MCP 도구 7개

도구가 스무 개면 에이전트가 선택에 주의를 씁니다. 이 7개가 루프를 덮습니다.

| 도구 | 용도 |
|---|---|
| `jarvis_context` | 작업 시작 시. 재사용 확인 + 예산 내 컨텍스트 + `trace_id` |
| `jarvis_remember` | 다음에도 쓸 지식 기록 |
| `jarvis_commit` | 작업 종료 시. 세션 기록 + 증류 |
| `jarvis_score` | 결과 평가 → 메모리 신뢰도 반영 |
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
jv digest                             전체 프로젝트 요약
jv maintain                           증류·감쇠 일괄 (서버가 자동 실행)
jv review [-p 프로젝트]                확인이 필요한 것 (주 1~2회 여기서 시작)
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
.venv/bin/python -m pytest -q        # 230 tests
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
- **예산은 약속.** 렌더링된 실제 텍스트를 측정해 초과하면 점수가 낮은 항목부터
  잘라냅니다.

라이선스: MIT
