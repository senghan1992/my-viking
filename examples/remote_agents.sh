#!/usr/bin/env bash
# 여러 머신의 코딩 에이전트가 하나의 MyViking 서버를 공유하는 흐름을 시연합니다.
# 실제 에이전트 대신 curl 로 MCP 를 호출합니다. 격리된 홈과 포트를 씁니다.
set -euo pipefail

PORT="${PORT:-8799}"
U="http://127.0.0.1:$PORT"
export JARVIS_HOME="${JARVIS_HOME:-$(mktemp -d)/jarvis-remote}"
JV="${JV:-jv}"

mcp() {  # mcp <tool> <json-args>
  curl -s -X POST "$U/mcp" -H 'content-type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"$1\",\"arguments\":$2}}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["result"]["content"][0]["text"])'
}
field() { python3 -c "import sys,json;print(json.load(sys.stdin)['$1'])"; }

echo "홈: $JARVIS_HOME"
echo
echo "== 1. 서버 준비: 프로젝트 + 저장소 연결 =="
$JV init backend -t coding -d "결제 API" > /dev/null
$JV mem add -p backend pitfalls "PG 재시도 금지" \
  "승인 응답 코드가 0000 이 아니면 재시도하지 않고 실패로 확정한다" \
  --detail "재시도하면 중복 승인이 발생한다. 멱등키가 있어도 마찬가지." > /dev/null
$JV mem add -p backend commands "테스트 실행" "pytest -q 로 전체 테스트를 돌린다" \
  --detail "루트에서 실행. PYTHONPATH=src 필요" > /dev/null
$JV link -p backend --repo "git@github.com:me/backend.git" --path "$PWD"
echo

echo "== 2. 서버 기동 =="
$JV serve --port "$PORT" > "$JARVIS_HOME/server.log" 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
for _ in $(seq 40); do curl -sf "$U/health" > /dev/null && break; sleep 0.4; done
curl -s "$U/health" | python3 -m json.tool
echo

Q="결제 승인이 실패하면 재시도해야 하나?"

echo "== 3. 노트북의 claude-code: 프로젝트 이름 없이 git remote 만으로 접속 =="
CTX=$(mcp jarvis_context "{\"repo\":\"https://github.com/me/backend\",\"question\":\"$Q\",\"agent\":\"claude-code@laptop\"}")
echo "$CTX" | python3 -c '
import sys, json
d = json.load(sys.stdin)
print("  프로젝트 해석: %s  (이름을 넘기지 않았습니다)" % d["project"])
print("  조립 시간: %sms · 항목 %d개" % (d["context_ms"], len(d["items"])))
for i in d["items"]:
    print("    %s %4dt  %s" % (i["tier"], i["tokens"], i["uri"].split("/", 4)[-1]))
'
TRACE=$(echo "$CTX" | field trace_id)
echo

echo "== 4. 작업 결과와 평가를 서버로 =="
mcp jarvis_commit "{\"repo\":\"https://github.com/me/backend\",\"question\":\"$Q\",\"answer\":\"재시도하지 않습니다. 응답 코드가 0000 이 아니면 실패로 확정하세요.\",\"trace_id\":\"$TRACE\",\"latency_ms\":2400,\"tokens_in\":1100,\"tokens_out\":45}" > /dev/null
mcp jarvis_score "{\"trace_id\":\"$TRACE\",\"value\":1.0,\"comment\":\"정확했음\"}" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("  신뢰도 조정된 메모리:"); [print("   ", u.split("/",4)[-1]) for u in d["memories_adjusted"]]'
echo

echo "== 5. 데스크톱의 cursor: 같은 질문 → 재사용 =="
mcp jarvis_context "{\"repo\":\"git@github.com:me/backend.git\",\"question\":\"$Q\",\"agent\":\"cursor@desktop\"}" \
  | python3 -c '
import sys, json
d = json.load(sys.stdin)
print("  재사용: %s (유사도 %s) · %sms" % (d["reused"], d["similarity"], d["context_ms"]))
print("  답변: %s" % d["answer"][:70])
'
echo

echo "== 6. 새 에이전트가 새로 알게 된 것을 남긴다 =="
mcp jarvis_remember '{"repo":"git@github.com:me/backend.git","category":"architecture","title":"웹훅 확정","statement":"결제는 PG 승인 후 웹훅으로 최종 확정된다","detail":"api/pay.py -> pg/client.py -> webhooks/pay.py"}' \
  | python3 -c 'import sys,json; print("  기록:", json.load(sys.stdin)["uri"])'
echo

echo "== 7. 서버 쪽에서 본 결과 =="
$JV metrics -p backend
echo
$JV agent list
echo
$JV impact -p backend
echo
echo "대시보드: $U/   (지금은 종료됩니다 — 직접 보려면 \`jv serve --port $PORT\`)"
