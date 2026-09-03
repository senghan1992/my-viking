#!/usr/bin/env bash
# MyViking 을 Docker 로 띄웁니다. 한 명령이면 끝나고, 다시 실행하면 업데이트입니다.
#
#   bash deploy/up.sh                              # 이 머신에서만 (127.0.0.1:8787)
#   bash deploy/up.sh --domain viking.duckdns.org  # 포트포워딩 + HTTPS (Caddy 자동 인증서)
#   bash deploy/up.sh --tunnel                     # 포트 안 열고 Cloudflare Tunnel (.env 의 CLOUDFLARE_TUNNEL_TOKEN)
#   bash deploy/up.sh --public --bind 100.64.0.5   # Tailscale/VPN IP 에만 열기 (평문이지만 터널이 암호화)
#   bash deploy/up.sh --public                     # 같은 LAN 안에서만 (평문 HTTP)
#   bash deploy/up.sh --behind-proxy               # 이미 있는 nginx/Traefik 뒤의 업스트림으로 (127.0.0.1:8787)
#   bash deploy/up.sh --public --url http://203.0.113.5:8787   # 클라우드: 안내에 쓸 공개 주소 지정
#   bash deploy/up.sh --dry-run ...                # docker 없이 무엇을 할지 보기만
#   PORT=9000 bash deploy/up.sh
#
# 필요한 것: docker (compose 포함). 그 외 호스트에 아무것도 설치하지 않습니다.
# 선택한 모드는 deploy/.env (MYVIKING_MODE) 에 남아, 이후 `git pull && bash deploy/up.sh` 나
# `docker compose up -d` 가 같은 모드를 유지합니다. DuckDNS 면 .env 에 DUCKDNS_TOKEN= 을 넣으세요.
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8787}"
MODE=""      # local | public | domain | tunnel | proxy
DOMAIN=""; BIND_IP=""; URL_OVERRIDE=""; DRY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --public)       MODE="public" ;;
    --domain)       MODE="domain"; DOMAIN="${2:?--domain 뒤에 도메인을 적어 주세요}"; shift ;;
    --tunnel)       MODE="tunnel" ;;
    --behind-proxy) MODE="proxy" ;;
    --local)        MODE="local" ;;
    --bind)         BIND_IP="${2:?--bind 뒤에 IP 를 적어 주세요}"; shift ;;
    --url)          URL_OVERRIDE="${2:?--url 뒤에 주소를 적어 주세요}"; shift ;;
    --dry-run)      DRY=1 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "알 수 없는 옵션: $1 (--public | --domain <도메인> | --tunnel | --behind-proxy | --bind <ip> | --url <주소> | --local | --dry-run)" >&2; exit 2 ;;
  esac
  shift
done

# ----- .env: 모드·도메인·토큰이 여기 남는다 -----
touch .env
env_get() { sed -n "s/^$1=//p" .env | tail -1; }
env_set() {  # env_set KEY VALUE — 있으면 바꾸고 없으면 추가
  if grep -q "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else echo "$1=$2" >> .env; fi
}
if [[ -z "$MODE" ]]; then
  MODE="$(env_get MYVIKING_MODE)"
  if [[ -z "$MODE" ]]; then  # 이전 버전의 .env: 도메인/포트로 추정
    if [[ -n "$(env_get MYVIKING_DOMAIN)" ]]; then MODE="domain"
    elif [[ "$(env_get MYVIKING_PORTS)" == *":8787" && "$(env_get MYVIKING_PORTS)" != "127.0.0.1:"* ]]; then MODE="public"
    else MODE="local"; fi
  fi
  [[ "$MODE" == "domain" && -z "$DOMAIN" ]] && DOMAIN="$(env_get MYVIKING_DOMAIN)"
  [[ "$MODE" == "public" && -z "$BIND_IP" ]] && BIND_IP="$(env_get MYVIKING_BIND_IP)"
fi
[[ "$MODE" == "domain" && -z "$DOMAIN" ]] && { echo "--domain <도메인> 이 필요합니다" >&2; exit 2; }

CF_TOKEN="$(env_get CLOUDFLARE_TUNNEL_TOKEN)"
CF_DOMAIN="$(env_get CLOUDFLARE_DOMAIN)"
if [[ "$MODE" == "tunnel" && -z "$CF_TOKEN" ]]; then
  echo "--tunnel 은 deploy/.env 에 CLOUDFLARE_TUNNEL_TOKEN=<토큰> 이 필요합니다." >&2
  echo "  Cloudflare Zero Trust → Networks → Tunnels 에서 터널을 만들고, Public hostname 을" >&2
  echo "  <도메인> → HTTP → myviking:8787 로 잡은 뒤 나오는 토큰입니다. CLOUDFLARE_DOMAIN= 도 적어 두면 안내에 씁니다." >&2
  exit 2
fi

# ----- 바인딩과 안내용 주소 -----
LOCAL_BIND="127.0.0.1:${PORT}:8787"
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"                  # Linux
[[ -z "$LAN_IP" ]] && LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)" # macOS
case "$MODE" in
  local)  BIND="$LOCAL_BIND"; URL="http://127.0.0.1:${PORT}"; TRUST="" ;;
  public)
    if [[ -n "$BIND_IP" ]]; then BIND="${BIND_IP}:${PORT}:8787"; URL="http://${BIND_IP}:${PORT}"
    else BIND="${PORT}:8787"; URL="http://${LAN_IP:-127.0.0.1}:${PORT}"; fi
    TRUST="" ;;
  domain) BIND="$LOCAL_BIND"; URL="https://${DOMAIN}"; TRUST=1 ;;          # 8787 은 로컬, Caddy 만 밖으로
  tunnel) BIND="$LOCAL_BIND"; URL="https://${CF_DOMAIN:-<Cloudflare 도메인>}"; TRUST=1 ;;
  proxy)  BIND="$LOCAL_BIND"; URL="${URL_OVERRIDE:-https://<프록시 도메인>}"; TRUST=1 ;;
esac
# hostname -I 는 사설 IP 다. 클라우드(EC2 등)에서는 퍼블릭 IP/도메인이 따로 있으므로 --url 로 덮어쓴다.
[[ -n "$URL_OVERRIDE" ]] && URL="${URL_OVERRIDE%/}"

PROFILES=()
DUCKDNS_TOKEN="$(env_get DUCKDNS_TOKEN)"
[[ "$MODE" == "domain" ]] && PROFILES+=(--profile tls)
[[ "$MODE" == "domain" && -n "$DUCKDNS_TOKEN" ]] && PROFILES+=(--profile duckdns)
[[ "$MODE" == "tunnel" ]] && PROFILES+=(--profile tunnel)

if [[ -n "$DRY" ]]; then
  echo "mode=$MODE bind=$BIND url=$URL trust_proxy=${TRUST:-0} profiles=${PROFILES[*]:-none}"
  exit 0
fi

if ! command -v docker > /dev/null; then
  echo "docker 를 찾을 수 없습니다. https://docs.docker.com/get-docker/" >&2
  exit 1
fi
COMPOSE=(docker compose)
docker compose version > /dev/null 2>&1 || COMPOSE=(docker-compose)

# ----- 빌드 + 기동. 무엇을 하든 먼저 루프백에서만 띄운다 -----
# 인증은 "첫 키가 만들어지는 순간" 켜진다. 키가 생기기 전에 바깥에 바인딩하면
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
  # `jv --json key list` 는 JSON 불리언을 낸다 ("revoked": false). 활성 키가 하나라도 있으면 재발급하지 않는다.
  if jv --json key list | grep -q '"revoked": false'; then
    echo "▸ API 키가 이미 있습니다 — 그대로 씁니다 (대시보드 연결 탭에서 관리)"
  else
    echo "▸ 외부에 열기 전에 관리자 API 키를 발급합니다"
    KEY=$(jv --json key create admin | sed -n 's/.*"key": *"\([^"]*\)".*/\1/p')
    [[ -n "$KEY" ]] || { echo "키 발급에 실패했습니다" >&2; exit 1; }
  fi
fi

# ----- 선택한 모드로 (재)기동하고 .env 에 남긴다 -----
# 모드를 바꿀 때 이전 모드의 프록시/터널이 남아 있으면 안 된다 (Caddy 가 켜진 채 TRUST_PROXY 만
# 꺼지면 바깥 사용자 전부가 한 IP 로 보여 한 사람의 틀린 키가 모두를 잠근다).
"${COMPOSE[@]}" --profile tls --profile duckdns --profile tunnel stop caddy duckdns cloudflared >/dev/null 2>&1 || true
env_set MYVIKING_MODE "$MODE"
env_set MYVIKING_PORTS "$BIND"
env_set MYVIKING_BIND_IP "$BIND_IP"
env_set MYVIKING_DOMAIN "$([[ "$MODE" == "domain" ]] && echo "$DOMAIN" || echo "")"
env_set MYVIKING_TRUST_PROXY "$TRUST"
[[ "$MODE" == "domain" && -n "$DUCKDNS_TOKEN" ]] && env_set DUCKDNS_SUBDOMAIN "${DOMAIN%%.duckdns.org}"
case "$MODE" in
  public)
    echo "▸ 외부 노출로 재기동 (${BIND}) — 평문 HTTP 입니다."
    [[ -z "$BIND_IP" ]] && echo "  인터넷에서 쓸 거면 --domain 또는 --tunnel 을, VPN(Tailscale) 이면 --bind <VPN IP> 를 권합니다."
    "${COMPOSE[@]}" up -d myviking
    [[ -z "$URL_OVERRIDE" && -z "$BIND_IP" ]] && echo "  ※ 아래 주소는 이 머신의 사설 IP 입니다. 클라우드(EC2 등)라면 퍼블릭 IP 로 바꿔 쓰거나 --url 로 지정하세요."
    ;;
  domain)
    [[ -n "$DUCKDNS_TOKEN" ]] && echo "▸ DuckDNS 갱신 컨테이너 기동 — 집 IP 가 바뀌어도 ${DOMAIN} 이 따라갑니다"
    echo "▸ HTTPS 프록시(Caddy) 기동 — ${DOMAIN} 의 인증서를 자동 발급합니다"
    "${COMPOSE[@]}" "${PROFILES[@]}" up -d
    ;;
  tunnel)
    echo "▸ Cloudflare Tunnel 기동 — 포트를 하나도 열지 않습니다"
    "${COMPOSE[@]}" "${PROFILES[@]}" up -d
    ;;
  proxy)
    echo "▸ 프록시 뒤 업스트림 모드 — 127.0.0.1:${PORT} 로만 열고 X-Forwarded-For 를 믿습니다"
    "${COMPOSE[@]}" up -d myviking
    echo "  당신의 프록시가 <도메인> → http://127.0.0.1:${PORT} 로 넘기고, X-Forwarded-For 를 *덮어쓰도록* 설정하세요."
    echo "  (nginx: proxy_set_header X-Forwarded-For \$remote_addr;  Traefik/Caddy/NPM 은 기본이 안전)"
    ;;
  local)
    "${COMPOSE[@]}" up -d myviking
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
case "$MODE" in
  domain)
    echo "아직 안 했다면 지금:"
    echo "  · 집 서버: 공유기에서 80/443 → 이 머신(LAN IP ${LAN_IP:-?}) 으로 포워딩. 8787 은 열지 마세요."
    echo "  · 클라우드: 도메인의 A 레코드 → 이 인스턴스 퍼블릭 IP, 보안 그룹 인바운드 80/443 만."
    echo "  확인:  curl -I $URL/health      (인증서 발급은 첫 접속 후 수십 초 걸릴 수 있습니다)"
    echo "  실패:  (cd $(pwd) && docker compose logs caddy)   ← DNS 가 아직 이 IP 를 안 가리키면 여기 나옵니다"
    echo "  ※ 집 안에서 도메인으로 접속이 안 되면 공유기의 NAT 루프백(헤어핀) 미지원입니다 — 공유기 DNS 에 도메인→LAN IP 를 넣으세요."
    echo ;;
  tunnel)
    echo "확인:  curl -I $URL/health     실패:  (cd $(pwd) && docker compose logs cloudflared)"
    echo "Cloudflare 대시보드의 Public hostname 이 'HTTP · myviking:8787' 을 가리키는지 확인하세요."
    echo ;;
esac
echo "다음 단계:"
echo "  1) 대시보드 $URL/ 를 열고 오른쪽 위 칸에 관리자 키를 넣습니다"
echo "     (다른 사람·기기용 키는 '연결' 탭에서 따로 발급하세요 — 관리자 키를 나눠주지 마세요)"
echo "  2) 에이전트가 도는 머신의 그 저장소 폴더에서 (자동 기록을 켜는 한 줄):"
echo "       pipx install git+https://github.com/senghan1992/my-viking.git   # 또는 pip install git+…"
echo "       jv agent hooks --install --url $URL --key <그 사람 키>"
echo "     설치 끝에 '✓ 서버 확인' 이 나와야 합니다. 프로젝트는 git remote 로 자동 생성됩니다."
echo "  3) (권장) 백업 — 볼륨을 잃어도 지식은 남습니다: 대시보드 첫 화면의 백업 패널"
# 품질 상태: 기본값은 오프라인 폴백(규칙 기반 증류 + 해시 임베딩)이라 동작은 하지만
# 동의어·다른 표현의 회상이 약합니다. 지금 어느 수준인지 서버에게 물어 그대로 알려 줍니다.
# After a --public --bind <IP> rebind, loopback is no longer bound — ask the final address.
QUALITY=$(curl -s "$URL/health" 2>/dev/null || curl -s "http://127.0.0.1:${PORT}/health" 2>/dev/null || true)
if [[ "$QUALITY" == *'"full_quality": true'* || "$QUALITY" == *'"full_quality":true'* ]]; then
  echo "  4) 품질: LLM + 의미 임베딩 모두 켜져 있습니다 (최상)"
else
  echo "  4) (권장) 품질 올리기 — 지금은 오프라인 폴백입니다:"
  [[ "$QUALITY" == *'"llm_ready": true'* || "$QUALITY" == *'"llm_ready":true'* ]] \
    || echo "       · 요약·증류:  deploy/.env 에  ANTHROPIC_API_KEY=sk-ant-...   (규칙 기반 → LLM 증류)"
  [[ "$QUALITY" == *'"embed_semantic": true'* || "$QUALITY" == *'"embed_semantic":true'* ]] \
    || echo "       · 의미 회상:  deploy/.env 에  JARVIS_EMBED_PROVIDER=openai + OPENAI_API_KEY=sk-...   (또는 =ollama, .env.example 참고)"
  echo "     넣은 뒤  bash deploy/up.sh  를 다시 실행하면 적용되고, 색인은 스스로 다시 만듭니다."
fi
echo
echo "업데이트:  git pull && bash deploy/up.sh      중지:  (cd deploy && docker compose down)"
echo "키를 잃었을 때:  (cd deploy && docker compose exec myviking jv key create admin)"
