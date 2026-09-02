#!/usr/bin/env bash
# MyViking 을 Docker 로 띄우고, 붙일 준비까지 한 번에 합니다.
#
#   bash deploy/up.sh                      # 로컬 전용 (127.0.0.1:8787)
#   bash deploy/up.sh --public             # 외부 노출 + API 키 자동 발급
#   PORT=9000 bash deploy/up.sh
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8787}"
PUBLIC=""
[[ "${1:-}" == "--public" ]] && PUBLIC=1

if ! command -v docker > /dev/null; then
  echo "docker 를 찾을 수 없습니다. https://docs.docker.com/get-docker/" >&2
  exit 1
fi
COMPOSE=(docker compose)
docker compose version > /dev/null 2>&1 || COMPOSE=(docker-compose)

# 인증은 "첫 키가 만들어지는 순간" 켜진다. 그래서 외부 노출(--public)일 때는
# 반드시 키를 먼저 발급하고 나서 열어야 한다. 곧바로 0.0.0.0 에 바인딩하면
# 키가 생기기 전까지 인증 없이 열려 있는 창이 생긴다 — 여기서 그 순서를 지킨다.
LOCAL_BIND="127.0.0.1:${PORT}:8787"
PUBLIC_BIND="${PORT}:8787"

echo "▸ 이미지 빌드"
MYVIKING_PORTS="$LOCAL_BIND" "${COMPOSE[@]}" build

# 무엇을 하든 일단 루프백에서만 기동한다 (노출 전 안전한 상태).
echo "▸ 기동 (${LOCAL_BIND})"
MYVIKING_PORTS="$LOCAL_BIND" "${COMPOSE[@]}" up -d

echo "▸ 상태 확인"
for _ in $(seq 60); do
  if curl -sf "http://127.0.0.1:${PORT}/health" > /dev/null; then break; fi
  sleep 1
done
curl -s "http://127.0.0.1:${PORT}/health" | python3 -m json.tool 2>/dev/null || {
  echo "기동에 실패했습니다. 로그:" >&2
  "${COMPOSE[@]}" logs --tail 40 >&2
  exit 1
}

URL="http://127.0.0.1:${PORT}"
KEY=""
if [[ -n "$PUBLIC" ]]; then
  echo
  echo "▸ 외부 노출이므로, 열기 전에 API 키를 먼저 발급합니다"
  KEY=$("${COMPOSE[@]}" exec -T myviking jv --json key create default \
        | python3 -c 'import sys,json; print(json.load(sys.stdin)["key"])')
  echo "  키: $KEY"
  echo "  (다시 볼 수 없습니다. 지금 저장하세요.)"

  # 이제 인증이 켜졌으니 공개 인터페이스로 다시 띄운다 (컨테이너 재바인딩).
  echo "▸ 외부 노출로 재기동 (${PUBLIC_BIND})"
  MYVIKING_PORTS="$PUBLIC_BIND" "${COMPOSE[@]}" up -d
fi

echo
echo "완료했습니다."
echo "  대시보드   $URL/"
echo "  API 문서   $URL/docs"
echo "  원격 MCP   $URL/mcp"
echo
echo "다음 단계:"
echo "  1) 저장소를 프로젝트에 연결"
echo "     cd ~/work/내프로젝트"
echo "     docker compose -f $(pwd)/docker-compose.yml exec -T myviking \\"
echo "       jv link -p 내프로젝트 --repo \"\$(git remote get-url origin)\""
echo "  2) 에이전트 연동 설정 받기"
echo "     ${COMPOSE[*]} exec myviking jv agent config --client claude-code \\"
echo "       --url $URL${KEY:+ --key $KEY}"
echo "  3) (권장) 구글 드라이브 백업 연결 — 볼륨을 잃어도 지식은 남습니다"
echo "     ${COMPOSE[*]} exec myviking jv backup connect \\"
echo "       --client-id <OAuth클라이언트ID> --client-secret <시크릿>"
