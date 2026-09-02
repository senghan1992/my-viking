#!/usr/bin/env bash
# MyViking 전체 루프를 한 번에 시연합니다. 격리된 홈을 쓰므로 실제 데이터에
# 영향이 없습니다.
set -euo pipefail

export JARVIS_HOME="${JARVIS_HOME:-$(mktemp -d)/jarvis-demo}"
JV="${JV:-jv}"
echo "데모 홈: $JARVIS_HOME"
echo

echo "== 1. 프로젝트 두 개, 서로 다른 메모리 스키마 =="
$JV init backend -t coding -d "결제 API 서버"
$JV init market  -t research -d "경쟁사 조사"
echo

echo "== 2. 반복 지시문을 프롬프트로 저장 =="
$JV prompt save -p backend bugfix "증상: {{symptom}}
관련 파일: {{files}}

원인을 한 줄로 설명한 뒤 최소 변경으로 고쳐줘."
$JV prompt render -p backend bugfix --var symptom="결제 승인 500" --var files="api/pay.py"
echo

echo "== 3. 매번 설명하던 사실을 기억시킴 =="
$JV mem add -p backend commands "테스트 실행" "pytest -q 로 전체 테스트를 돌린다" \
  --detail "루트에서 실행. PYTHONPATH=src 필요"
$JV mem add -p backend architecture "결제 흐름" "결제는 PG 호출 후 웹훅으로 확정된다" \
  --detail "api/pay.py -> pg/client.py -> webhooks/pay.py"
$JV mem add -p market findings "PG 수수료" "경쟁사 A 는 2.8% 수수료를 공개하고 있다" \
  --detail "출처: https://example.com/pricing"
echo

echo "== 4. 긴 참고 문서를 티어링해 등록 =="
# 티어링 절감은 큰 문서에서 나옵니다. 작은 메모리 몇 개만 있으면 L1 == L2 이므로
# 절감이 0% 로 나오는 것이 정상입니다.
python3 - <<'PY' > /tmp/pg-spec.md
sections = []
for i in range(12):
    sections.append(f"## {i}. PG 연동 규격 항목 {i}\n")
    sections.append(
        "결제 승인 요청은 멱등키를 필수로 포함하며, 승인 응답 코드가 0000 이 아닌 경우 "
        "재시도하지 않고 실패로 확정한다. 웹훅은 최대 3회 재전송되고 서명 검증에 "
        "실패하면 폐기한다. " * 4
    )
    sections.append("\n\n")
print("".join(sections))
PY
$JV resource add -p backend pg-spec -f /tmp/pg-spec.md --title "PG 연동 규격"
echo

echo "== 5. 컨텍스트 조립 (캐시 미적중) =="
$JV ask -p backend "결제 승인 실패 시 재시도와 웹훅 서명은 어떻게 처리해?"
echo

echo "== 6. 답변 기록 → 자가학습 =="
$JV commit -p backend "결제 승인이 실패하는데 어디를 봐야 해?" \
  "api/pay.py 의 PG 응답 처리와 webhooks/pay.py 의 확정 로직을 확인하세요." \
  --tokens-in 1800 --tokens-out 90
echo

echo "== 7. 같은 질문 재요청 (캐시 적중) =="
$JV ask -p backend "결제 승인이 실패하는데 어디를 봐야 해?"
echo

echo "== 8. 프로젝트 격리 확인: market 에는 backend 메모리가 없음 =="
$JV mem list -p market
echo

echo "== 9. 토큰 회계 =="
$JV report -p backend
echo
$JV stats
