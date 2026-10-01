# 컨셉 전환 — "서버 증류"에서 "서기 agent"로

## 옛 컨셉 (v10 까지)

```
내 세션의 질문→답 ──POST /commit──▶ 서버가 증류(distill) ──▶ 지식 1권 자동 등재
                    (매 턴 · 무조건 · 서버가 판단)
```

- 내 프롬프트와 결과가 서버로 건너간 뒤, **서버가(선택 LLM or 휴리스틱으로) 지식을 만들었다.**
- 그래서 "어떤 모델로 정리되는지", "요약 품질이 왜 그렇게 낮은지"가 내 몫이 아니었다.
- 매 턴 1권이 쌓여 서가가 잡음으로 가득 찼다.

## 새 컨셉 (v11 — 서기 agent)

```
┌─ 작업 세션 (pi) ────────────────┐        ┌─ 서기 세션 (pi, 별도 프로세스) ─────────┐
│ 나는 코딩만 한다.                  │        │ 별도의 세션이 열린다.                      │
│ 확장은 턴의 흔적을 관찰로 올린다    │─관찰──▶│ /inbox 로 대기 관찰 + 반복 요청을 읽는다   │
│ (질문·답·건드린 파일·오류)          │        │ 필요하면 세션 트랜스크립트 원문을 열어본다 │
│ 판단·저장은 하지 않는다            │        │ 남길 것만 판단 → /remember → /inbox/ack   │
└────────────────────────────────┘        └──────────────────────────────────────┘
              ▲                                        │
              └───── 지식·브리핑·관련 지식으로 되돌아온다 ┘
                (서버 = 저장고 · 검색 · 반복 카운터 — 판단하지 않는다)
```

| 역할 | 누구 | 하는 일 |
|---|---|---|
| 관찰 | 작업 세션의 pi 확장 / 훅 | 매 턴 이벤트 → `POST /observe` |
| 반복 감지 | 서버 (결정론적, 모델 안 씀) | 어휘 겹침으로 `repeats`·`recurrences` 계산 |
| **판단·요약·등재** | **서기 agent (별도 pi 세션)** | `GET /inbox` → `POST /remember` → `POST /inbox/ack` |
| 검색·주입·적응 | 서버 (예전 그대로) | `/prepare` `/brief` `/score` |

## 서기 원칙 (charter) — 전부 적지 않는다

`~/.scribe/secretary/secretary.md` 에 평문으로 놓이고, 사용자가 직접 고칠 수 있습니다
(`scribe secretary once` 가 `--append-system-prompt` 로 주입).

**남길 것 4가지**
1. 재발 실수 — 같은 함정에 빠지면 시간이 날아간다 → `pitfalls`
2. 반복 요청 — 같은 것을 두 번 이상 시켰으면 그건 규칙이다 → `knowledge`/`commands`
3. 결정과 근거 — 나중에 번복하기 쉬운 선택 → `decisions`
4. 재사용 절차 — 외우기 어려운 명령·설정·순서 → `commands`

**버릴 것** — 일회성 작업 내용, 이미 있는 지식의 재진술, 추측·미확인 단정, 비밀값.

**안전** — 서기 세션은 `--tools read,grep,find,ls,scribe_*` 로 열립니다.
`edit`/`write`/`bash` 가 없어 코드를 고치지 못하고, 자기 세션의 관찰을 올리지 않아
되먹임이 없습니다.

## API

| 엔드포인트 | 용도 |
|---|---|
| `POST /api/v1/projects/{slug}/observe` | 작업 세션의 관찰 배치 적재 (kind: prompt/reply/error/edit/note/decision/request) |
| `GET  /api/v1/projects/{slug}/inbox` | 서기 작업함 — 미처리 관찰(세션별) + 반복 요청 + 재발 오류 + 검증 필요 + 트랜스크립트 위치 (`?worker=` 이면 선점) |
| `POST /api/v1/projects/{slug}/inbox/ack` | 처리 통보 (filed/merged/skipped + 이유), 서기 실행장에 집계 |
| `POST /api/v1/projects/{slug}/remember` | 지식 등재 — `source='secretary'`, `occurrences`, `observation_ids`(함께 filed 처리) |
| `POST /api/v1/projects/{slug}/seen` | "이 지식이 또 쓰였다" — 반복 횟수만 누적 |
| `GET  /api/v1/projects/{slug}/secretary/status` | 대기 중 · 반복 후보 · 마지막 업무 보고 |
| `GET  /api/v1/projects/{slug}/brief` | 작업 세션 브리핑 (서기 대기 건수와 실행 명령 포함) |
| `POST /api/v1/projects/{slug}/commit` | **레거시** — 기본은 관찰로만 저장. `projects.auto_distill=1` 이면 옛 즉시 증류 |

## 실행

```bash
scribe secretary once                 # 백그라운드 서기 세션 한 번
scribe secretary once -f              # 지켜보기 (pi 출력이 터미널에 뜬다)
scribe secretary auto on --every 8     # 관찰 8건마다 작업 세션이 스스로 서기를 깨운다
scribe secretary status               # 대기 · 반복 후보 · 마지막 보고
scribe note "이건 기록할 가치 있음"     # 서기에게 메모 (pi 안: /scribe note)
scribe inbox                          # 관찰함 내용 (pi 안: /scribe inbox)
```

다른 에이전트(Claude Code 훅 · jcode 훅 · MCP · 셸)는 그대로 두되, 이제 **관찰함으로
재료를 보내는 쪽**입니다. 정리는 같은 서버의 서기가 합니다.

## 안전장치 (컨셉이 새도 사고는 옛날처럼 나면 안 된다)

- **되먹임 차단** — `SCRIBE_ROLE=secretary` 로 열린 세션은 관찰을 올리지 않는다.
  (서기가 자기 작업을 기록하면 무한 루프)
- **선점과 회수** — `/inbox?worker=` 가 읽은 관찰은 `claimed` 로 선점되어 두 서기가 같은 것을
  정리하지 않는다. 선점 후 45분 넘게 처리하지 않으면 자동으로 대기에 복귀 — 서기가 죽어도 일이 사라지지 않는다.
- **읽기는 자유롭게** — 사람/CLI 의 `scribe inbox` 는 기본 선점 안 함(`--claim` 일 때만)이라,
  보면 서기 일이 사라지는 일이 없다.
- **멈춘 턴도 남긴다** — 오류/abort 로 최종 답 없이 끝나면 `agent_settled` 에서 질문만이라도
  관찰로 남기고 대기열을 비운다 (다음 턴이 막히지 않는다).
- **보존** — 등재/폐기된 관찰은 14일 후 정리, 전체 세션 원문은 서버로 보내지 않고
  에이전트 머신의 트랜스크립트에 둔다. 마스킹은 클라이언트·서버 양쪽.
- **검증** — `node tools/pi-extension-smoke.mjs` 가 확장(scribe.ts)을 실제로 import 해
  워커는 `/observe` 만, 서기는 `/inbox`·`/remember`·`/ack` 만 쓰는 것을 25 건으로 확인한다.

## 왜 이렇게 바꿨나

- **판단은 세션 맥락을 아는 쪽이 잘한다.** 서버는 질문·답의 조각만 본다. 서기는 같은 머신의
  pi 트랜스크립트(`~/.pi/agent/sessions/**.jsonl`)를 열어 "무슨 일이 있었고 뭘 잃어버렸나"를
  스스로 확인한다.
- **모델을 서버에 연결하지 않는다.** 서기는 내 pi 가 쓰는 모델을 그대로 쓴다. 서버에 API 키를
  넣지 않아도 전체가 동작하며(요약·임베딩은 선택), 서버 LLM 을 갈아끼우는 고민이 사라진다.
- **저장소가 오염되지 않는다.** 매 턴 1권이 아니라 서기가 골라낸 것만 권이 된다. 등재된 지식은
  어떤 관찰에서 왔는지(evidence)와 몇 번 요청됐는지(occurrences)를 남긴다.
- **사람이 확인할 수 있다.** 대시보드 → 프로젝트 → **서기** 화면에 관찰함·반복 후보·마지막
  업무 보고가 그대로 보인다.
