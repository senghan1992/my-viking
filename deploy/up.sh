#!/usr/bin/env bash
# MyViking 을 Docker 로 띄웁니다. 한 명령이면 끝나고, 다시 실행하면 업데이트입니다.
#
#   bash deploy/up.sh                              # 이 머신에서만 (127.0.0.1:8787)
#   bash deploy/up.sh --public                     # 같은 네트워크/포트포워딩으로 노출 (평문 HTTP)
#   bash deploy/up.sh --domain viking.duckdns.org  # HTTPS 로 노출 (Caddy 자동 인증서) ← 권장
#   PORT=9000 bash deploy/up.sh
#
# 필요한 것: docker (compose 포함). 그 외 호스트에 아무것도 설치하지 않습니다.
# 선택한 모드는 deploy/.env 에 남아, 이후 `git pull && bash deploy/up.sh` 나
# `docker compose up -d` 가 같은 모드를 유지합니다.
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8787}"
MODE=""      # local | public | domain
DOMAIN=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --public) MODE="public" ;;
    --domain) MODE="domain"; DOMAIN="${2:?--domain 뒤에 도메인을 적어 주세요}"; shift ;;
    --local)  MODE="local" ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "알 수 없는 옵션: $1 (--public | --domain <도메인> | --local)" >&2; exit 2 ;;
  esac
  shift
done

if ! command -v docker > /dev/null; then
  echo "docker 를 찾을 수 없습니다. https://docs.docker.com/get-docker/" >&2
  exit 1
fi
COMPOSE=(docker compose)
docker compose version > /dev/null 2>&1 || COMPOSE=(docker-compose)

# ----- 모드 결정: 플래그 > 이전에 .env 에 남긴 모드 > 로컬 -----
touch .env
env_get() { sed -n "s/^$1=//p" .env | tail -1; }
env_set() {  # env_set KEY VALUE — 있으면 바꾸고 없으면 추가
  if grep -q "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else echo "$1=$2" >> .env; fi
}
if [[ -z "$MODE" ]]; then
  if [[ -n "$(env_get MYVIKING_DOMAIN)" ]]; then MODE="domain"; DOMAIN="$(env_get MYVIKING_DOMAIN)"
  elif [[ "$(env_get MYVIKING_PORTS)" == "${PORT}:8787" ]]; then MODE="public"
  else MODE="local"; fi
fi

LOCAL_BIND="127.0.0.1:${PORT}:8787"
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"   # 리눅스; 없으면 아래서 127.0.0.1
case "$MODE" in
  local)  BIND="$LOCAL_BIND";   URL="http://127.0.0.1:${PORT}" ;;
  public) BIND="${PORT}:8787";  URL="http://${LAN_IP:-127.0.0.1}:${PORT}" ;;
  domain) BIND="$LOCAL_BIND";   URL="https://${DOMAIN}" ;;   # 8787 은 로컬에 두고 Caddy 만 밖으로
esac

# ----- 빌드 + 기동. 무엇을 하든 먼저 루프백에서만 띄운다 -----
# 인증은 "첫 키가 만들어지는 순간" 켜진다. 키가 생기기 전에 0.0.0.0 에 바인딩하면
# 인증 없이 열린 창이 생기므로, 노출은 키를 확인한 뒤에만 한다.
echo "▸ 이미지 빌드"
MYVIKING_PORTS="$LOCAL_BIND" "${COMPOSE[@]}" build --quiet
echo "▸ 기동 (${LOCAL_BIND})"
MYVIKING_PORTS="$LOCAL_BIND" "${COMPOSE[@]}" up -d myviking

echo "▸ 상태 확인"
healthy() {
  if command -v curl > /dev/null; then curl -sf "http://127.0.0.1:${PORT}/health" > /dev/null
  else [[ "$(docker inspect -f '{{.State.Health.Status}}' "$("${COMPOSE[@]}" ps -q myviking)" 2>/dev/null)" == "healthy" ]]
  fi
}
for _ in $(seq 60); do healthy && break; sleep 1; done
if ! healthy; then
  echo "기동에 실패했습니다. 로그:" >&2
  "${COMPOSE[@]}" logs --tail 40 myviking >&2
  exit 1
fi

# ----- 노출 모드면 키를 먼저 확보한다 (한 번만; 다시 실행해도 새 키를 만들지 않는다) -----
KEY=""
jv() { "${COMPOSE[@]}" exec -T myviking jv "$@"; }
if [[ "$MODE" != "local" ]]; then
  if jv --json key list | grep -q '"revoked": 0'; then
    echo "▸ API 키가 이미 있습니다 — 그대로 씁니다 (대시보드 연결 탭에서 관리)"
  else
    echo "▸ 외부에 열기 전에 관리자 API 키를 발급합니다"
    KEY=$(jv --json key create admin | sed -n 's/.*"key": *"\([^"]*\)".*/\1/p')
    [[ -n "$KEY" ]] || { echo "키 발급에 실패했습니다" >&2; exit 1; }
  fi
fi

# ----- 선택한 모드로 (재)기동하고 .env 에 남긴다 -----
case "$MODE" in
  local)
    env_set MYVIKING_PORTS "$LOCAL_BIND"; env_set MYVIKING_DOMAIN ""; env_set MYVIKING_TRUST_PROXY ""
    ;;
  public)
    env_set MYVIKING_PORTS "$BIND"; env_set MYVIKING_DOMAIN ""; env_set MYVIKING_TRUST_PROXY ""
    echo "▸ 외부 노출로 재기동 (${BIND}) — 평문 HTTP 입니다. 집 밖에서 쓸 거면 --domain 을 권합니다."
    "${COMPOSE[@]}" up -d myviking
    ;;
  domain)
    env_set MYVIKING_PORTS "$LOCAL_BIND"; env_set MYVIKING_DOMAIN "$DOMAIN"; env_set MYVIKING_TRUST_PROXY 1
    echo "▸ HTTPS 프록시(Caddy) 기동 — ${DOMAIN} 의 인증서를 자동 발급합니다"
    "${COMPOSE[@]}" --profile tls up -d
    echo "  공유기에서 80/443 을 이 머신으로 포워딩하세요. 8787 은 열지 마세요."
    ;;
esac

echo
echo "완료했습니다."
echo "  대시보드   $URL/"
echo "  원격 MCP   $URL/mcp"
if [[ -n "$KEY" ]]; then
  echo
  echo "  관리자 API 키:  $KEY"
  echo "  (다시 볼 수 없습니다. 지금 저장하세요. 대시보드 오른쪽 위 칸에 넣으면 들어갑니다.)"
fi
echo
echo "다음 단계:"
echo "  1) 대시보드 $URL/ 를 열고 → 프로젝트 만들기 → 카드 클릭 → 연결정보를 복사"
echo "     (다른 사람·기기용 키는 '연결' 탭에서 따로 발급하세요)"
echo "  2) 에이전트가 도는 머신의 그 저장소 폴더에서 (자동 기록을 켜는 한 줄):"
echo "       pip install git+https://github.com/senghan1992/my-viking.git"
echo "       jv agent hooks --install --url $URL${KEY:+ --key $KEY}"
echo "  3) (권장) 백업 — 볼륨을 잃어도 지식은 남습니다: 대시보드 첫 화면의 백업 패널"
echo
echo "업데이트:  git pull && bash deploy/up.sh      중지:  (cd deploy && docker compose down)"
