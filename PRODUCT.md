# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

- **코딩 에이전트 사용자(개발자)** — Claude Code·Cursor·Codex·셸 에이전트로 작업하는 사람. 브라우저에서 가입하고 프로젝트(서가)를 만들고, 에이전트를 연결하면 작업 지식이 자동으로 쌓인다.
- **관리자** — 첫 가입자가 자동 승격 (VIKING_FIRST_USER_ADMIN). 사용자 활성화/비활성화와 서버 LLM·임베딩 연결을 담당.
- *추론(inferred): 주 사용자는 한국어 사용 팀/개인 개발자. (저장소의 모든 UI·문서·테스트 카피가 한국어)*

## Product Purpose

사람별 프로젝트 지식 도서관. 코딩 에이전트가 작업 중 알게 된 규칙·함정·결정을 자동으로 증류해 도서관(지식 DB)에 기록하고, 이후 질문마다 관련 지식을 브리핑/주입해 에이전트가 같은 실수를 반복하지 않게 한다. 사람이 직접 기록(remember)·채점(score)·수정할 수도 있다.

## Positioning

"관리자가 키를 나눠주는" 지식 공유 도구가 아니라 **사람들이 스스로 가입·생성·연결하는 셀프서비스 도서관**이며, 지식이 시간이 지나며 **적응**한다: 주입한 지식이 결과가 좋으면 '확립', 어긋나면 '검증 필요'로 강등. 단일 컨테이너 + SQLite 하나로 운영 단순함이 제품 자체.

## Operating Context

- 브라우저 대시보드: 가입 → 프로젝트(서가) → 새 키 발급 → 에이전트 머신에 `jv hook install` 설치 명령 복사.
- 에이전트 머신: CLI `jv` 가 Claude Code 훅/SessionStart·UserPromptSubmit·Stop·SessionEnd 이벤트로 서버 API(brief/prepare/commit/score/remember)를 호출.
- 매일의 코딩 세션이 곧 지식의 원료. 세션 시작 시 브리핑, 질문마다 관련 지식 주입, 작업 끝나면 자동 기록.
- 배포: Docker 단일 컨테이너(루트 docker-compose.yml), 볼륨 `viking-data` 의 `index.db` 하나가 데이터 전부.

## Capabilities and Constraints

- 멀티유저 계정(가입/로그인/세션 쿠키), 역할(사용자/관리자), 비활성화.
- 프로젝트(서가) CRUD, 지식 4개 카테고리(📖 지식 / 🛠️ 명령 / ⚠️ 함정 / 🧭 결정), 상태 칩 3종(검증 전/확립/검증 필요), 동일 제목 대체·교정 기록.
- Agent API: brief / prepare / commit / remember / score / search / health — Bearer `jv_` 키 (프로젝트 스코프).
- 선택 기능: OpenAI 호환 LLM 한 줄 요약, 임베딩 의미 검색 — 없으면 추출식·키워드로 폴백. 설정은 env 또는 관리자 → 모델 설정 화면 (data/models.json, 0600, 웹>env>기본값, 핫스왑).
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
2. **지식은 자동으로 쌓이고 적응한다** — 기록의 부담은 에이전트가 지고, 사람은 확인·교정만.
3. **비밀값과 키는 어디서도 평문으로 보이지 않는다** — 발급 시 1회 노출 외 전부 마스킹/해시.
4. **운영은 단순할수록 좋다** — 컨테이너 1개, DB 파일 1개, 설정은 env 또는 관리자 화면.

## Accessibility & Inclusion

- 명시된 표준/요구사항 없음 (*미확정 — 대비·포커스·키보드 조작 기본 수준은 지킨다*).