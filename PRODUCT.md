# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **코딩 에이전트 사용자(개발자)** — Claude Code·Cursor·Codex·셸 에이전트로 작업하는 사람. 브라우저에서 가입하고 프로젝트(서가)를 만들고, 에이전트를 연결하면 작업 지식이 자동으로 쌓인다.
- **관리자** — 첫 가입자가 자동 승격 (VIKING_FIRST_USER_ADMIN). 사용자 활성화/비활성화와 서버 LLM·임베딩 연결을 담당.
- *추론(inferred): 주 사용자는 한국어 사용 팀/개인 개발자. (저장소의 모든 UI·문서·테스트 카피가 한국어)*

## Product Purpose

사람별 프로젝트 지식 도서관. **작업 세션(pi)은 자신이 한 일을 '관찰'로만 남기고, 별도 세션으로
열린 서기(secretary) agent 가 그 관찰을 읽고 다음에 실수하지 않을 것·여러 번 요청된 것만 지식으로
등재한다.** 쌓인 지식은 다음 세션의 브리핑·관련 지식으로 주입되어 같은 함정을 막는다.
서버는 저장고·검색·반복 카운터이며, 지식을 추출하지 않는다.

## Positioning

"관리자가 키를 나눠주는" 지식 공유 도구가 아니라 **사람들이 스스로 가입·생성·연결하는 셀프서비스 도서관**.
차별점은 추출 주체다 — 서버에 모델을 연결해 매 턴을 증류하는 대신(그건 v10 까지의 방식),
**사용자 자기 pi 세션을 공유해 읽는 서기 agent** 가 판단한다. 그래서 서버에 API 키가 없어도
전체가 동작하고, 요약 품질이 아니라 '무엇을 남길 것인가' 가 제품 가치가 된다.
지식은 결과에 따라 **적응**한다(좋으면 확립, 어긋나면 검증 필요). 단일 컨테이너 + SQLite 하나.

## Operating Context

- 브라우저 대시보드: 가입 → 프로젝트(서가) → 새 키 발급 → 연결 탭에서 설치 명령 복사. 프로젝트 화면의 **서기** 탭에서 관찰함·반복 요청·마지막 업무 보고를 사람이 확인한다.
- 에이전트 머신: `jv` 가 폴더 연결(`.myviking-connection.json`) 기준으로 동작. pi 허브 확장은 매 턴을 `POST /observe` 로, Claude Code·jcode 훅도 같은 관찰함으로 보낸다.
- 서기: `jv secretary once`(또는 `jv secretary auto on`) 가 **별도 pi 세션**을 띄워 `/inbox` 를 읽고 `/remember`·`/inbox/ack` 로 정리한다. 코드 수정 도구는 없다.
- 매일의 코딩 세션이 원료(관찰)이고, 서기가 골라낸 것만 지식(권)이 된다.
- 배포: Docker 단일 컨테이너, 볼륨 `viking-data` 의 `index.db` 하나. 서버 LLM/임베딩은 선택(요약·의미 검색 품질용).

## Capabilities and Constraints

- 멀티유저 계정(가입/로그인/세션 쿠키), 역할(사용자/관리자), 비활성화.
- 프로젝트(서가) CRUD, 지식 4개 카테고리(📖 지식 / 🛠️ 명령 / ⚠️ 함정 / 🧭 결정), 상태 칩 3종(검증 전/확립/검증 필요), 동일 제목 대체·교정 기록.
- Agent API: brief / prepare / **observe** / **inbox** / **inbox/ack** / **secretary/status** / remember / seen / score / search / health (+레거시 commit) — Bearer `jv_` 키 (프로젝트 스코프).
- 관찰 데이터: 세션별 관찰(kind prompt|reply|error|edit|note|decision|request), 지문(`norm`)·`hits`, 세션 트랜스크립트 경로, 처리 상태(open|claimed|filed|skipped), 서기 실행장(secretary_runs: found/filed/merged/skipped/report).
- 지식 계보: `source`(manual|secretary|session), `occurrences`(반복 요청 누적), evidence(어떤 관찰에서 왔나).
- 서기 agent: `jv secretary once|auto|status|log|stop|install`, `~/.myviking/secretary/secretary.md`(원칙, 사용자 편집 가능), pi 에이전트 정의/프롬프트 템플릿 설치.
- 선택 기능: OpenAI 호환 LLM 한 줄 요약, 임베딩 의미 검색 — 없으면 추출식·키워드로 폴백. 설정은 env 또는 관리자 → 모델 설정 화면 (data/models.json, 0600, 웹>env>기본값, 핫스왑). **서기 동작에는 모델 연결이 필요 없다.**
- 비밀값 마스킹(서버·클라이언트), 키는 해시 저장, 세션 HMAC 서명. SQLite 단일 파일, export.md 내보내기.
- 기술 제약: FastAPI + Jinja2 서버 렌더 + 소량 JS, python:3.12-slim, `app/main.py` 단일 앱, 의존성은 pyproject.toml 에 명시(python-multipart 필요).
- *추론(inferred): 외부 CSS/JS CDN 자산·웹폰트 로딩 없이 자체 호스팅만 (컨테이너가 인터넷 폰트에 의존하지 않는 단순 운영).*

## Brand Commitments

- 이름 **myviking**, 도서관 은유를 제품 용어로 사용 (서가/권/도서관, 카테고리 이모지).
- 모든 UI 카피 한국어. 로고·이미지·외부 브랜드 에셋 없음 (텍스트 + 이모지 위주).
- MIT 라이선스.

## Evidence on Hand

- README.md (제품 설명·배포 절차), ARCHITECTURE.md (구조·보안), docs/ANALYSIS.md.
- 테스트 33개 (인증/격리/에이전트 루프/적응/마스킹/CLI/모델 설정).
- 합성 데모 데이터 없음 — 실제 사용자 데이터가 채워지는 셀프서비스 앱. 가짜 지식·가짜 사용자 소개를 만들지 않는다.

## Product Principles

1. **셀프서비스 개방** — 가입부터 연결까지 관리자 개입 없이 한 사람이 끝낼 수 있어야 한다.
2. **지식은 서기가 고라서 쌓는다** — 작업 세션은 관찰만 남긴다. 판단·요약·등재는 세션 맥락을 읽는
   서기 agent 의 일이고, 사람은 확인·교정만 하면 된다. 서버는 지식을 추출하지 않는다.
3. **반복은 카운트된다** — 사람이 두 번 이상 요청한 것과 다시 나타난 오류는 서버가 결정론적으로
   세어 서기에게 넘기고, 지식에 `occurrences` 로 남는다.
4. **비밀값과 키는 어디서도 평문으로 보이지 않는다** — 발급 시 1회 노출 외 전부 마스킹/해시.
   관찰은 마스킹 후 저장되고, 세션 원문(트랜스크립트)은 에이전트 머신에 남는다.
5. **운영은 단순할수록 좋다** — 컨테이너 1개, DB 파일 1개, 설정은 env 또는 관리자 화면.
   서기는 사용자 pi 런타임을 재사용하므로 별도 서비스·키가 없다.

## Accessibility & Inclusion

- 명시된 표준/요구사항 없음 (*미확정 — 대비·포커스·키보드 조작 기본 수준은 지킨다*).