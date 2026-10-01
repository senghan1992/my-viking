# 서기 폴더 옵트인 — design spec (2026-10-01)

## 배경
사용자 요구: 프로젝트 폴더마다 git처럼 쓸지 말지 결정. 라이브러리 설치만으로는
아무 일도 일어나지 않고, 붙이기로 한 폴더에서만 관찰→서기 정리→다음 브리핑으로
같은 실수 반복 방지.

## 현 상태 (검증됨)
- 연결 기준은 폴더 파일 하나(`.myviking-connection.json`, 비밀 없음). 없으면
  도구 미등록·브리핑 없음·관찰 0 (`_folder_conn` None, `noConn` 안내).
- `disconnect`는 폴더만 떼고 키 보관, `disable`은 머신 전체 OFF.
- `brief` 자동 주입은 이미 동작. 손댈 필요 없음.
- 갭 2개: (1) 서기 `auto/every`가 전역(`~/.myviking/config.json`) 하나라 폴더별
  제어가 안 됨. (2) `status`가 폴더 상태(연결됨/자유+서기 유효값)를 한 줄로 못 박지 않음.

## 결정
- A: 폴더별 서기 오버라이드 `.myviking-secretary.json` (`{auto, every}`), 없으면 전역 폴백.
- B: `jv status`에 `이 폴더: 연결됨/자유` + 서기 유효값 표시. 동작 변경 없이 인지 개선.
- C(서버 프로젝트 스위치)는 채택 안 함 — 오프라인·다중머신에서 꼬임.

## 컴포넌트
1. 폴더 서기 설정: `_secretary_effective(cwd)` = 폴더 파일 > 전역 > 기본 `{auto:false, every:12}`.
   폴더 파일(`.myviking-secretary.json`) 위치는 연결 파일과 같은 디렉토리(프로젝트 루트,
   `_project_link(cwd).parent`). 비밀 없음, git exclude 대상에 추가. `every`는 1 이상으로 clamp.
2. pi 확장 `secretarySetting()`에 폴더값 전달: 작업 세션의 auto 깨우기(`observe` 후
   `pending >= every` 시 `secretary once --detach`)가 그 폴더의 유효값을 씀.
   `observe`에 판단 로직 추가 없음. `priority` 힌트는 이번 스펙에 넣지 않음(다음 단계).
3. `jv status` / `pi check`: 폴더 링크 유무·연결 이름·서기 유효값·대기 건수를 한 화면으로.

## 데이터 플로우
- 옵트아웃 폴더: 링크 없음 → `getActive`=null → 도구 미등록, observe 호출 없음.
- 옵트인 폴더: 링크 있음 → 매 턴 observe → 서기는 `once`(수동) 또는 폴더 유효값 기준 auto.
- 서기 세션(`MYVIKING_ROLE=secretary`)은 관찰을 올리지 않음(되먹임 차단, 현행 유지).

## 에러 처리
- 폴더 파일 파손(JSON 오류) → 자유 사용으로 폴백 + `status`에 경고. 코딩 세션 차단 없음(fail-open).
- 서버 unreachable → 브리핑·관찰 조용히 스킵 (현행 유지).
- 전역 `disable`이면 폴더 설정과 무관하게 전부 OFF.

## 테스트
- 기존: `pytest` 해당 영역 + `node tools/pi-extension-smoke.mjs`.
- 신규: 폴더 오버라이드 폴백 3건(없음→전역 / 있음→덮어씀 / 파손→경고+폴백),
  `status` 폴더 한 줄 표시, auto 깨우기가 폴더값을 쓰는 것.
