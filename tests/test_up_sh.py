"""deploy/up.sh 의 모드 해석을 docker 없이 검증한다 (--dry-run).

배포 스크립트는 사람이 가장 먼저 만나는 코드인데 테스트가 없어서, 키 존재 판정이
항상 거짓인 버그("revoked": 0 vs false)가 리뷰에서야 발견됐다. 적어도 모드·바인딩·
프록시 신뢰·프로필 선택은 여기서 고정한다.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

DEPLOY = pathlib.Path(__file__).resolve().parents[1] / "deploy"
if shutil.which("bash") is None:
    pytest.skip("bash 없음", allow_module_level=True)


@pytest.fixture()
def deploy(tmp_path):
    d = tmp_path / "deploy"
    shutil.copytree(DEPLOY, d)
    (d / ".env").unlink(missing_ok=True)
    return d


def dry(deploy, *args, env_lines=()):
    if env_lines:
        (deploy / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    p = subprocess.run(["bash", str(deploy / "up.sh"), "--dry-run", *args],
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip().splitlines()[-1] if (p.stdout + p.stderr).strip() else ""


def test_local_is_loopback_without_proxy_trust(deploy):
    code, line = dry(deploy)
    assert code == 0
    assert "mode=local bind=127.0.0.1:8787:8787" in line and "trust_proxy=0" in line


def test_domain_keeps_8787_on_loopback_and_trusts_only_caddy(deploy):
    """포트포워딩 시나리오: 8787 은 절대 밖으로 나가지 않고 Caddy 만 80/443 을 받는다."""
    code, line = dry(deploy, "--domain", "viking.duckdns.org")
    assert code == 0
    assert "bind=127.0.0.1:8787:8787" in line
    assert "url=https://viking.duckdns.org" in line
    assert "trust_proxy=1" in line and "--profile tls" in line and "--profile duckdns" not in line


def test_duckdns_token_adds_the_renewal_container(deploy):
    _, line = dry(deploy, "--domain", "viking.duckdns.org", env_lines=["DUCKDNS_TOKEN=abc"])
    assert "--profile tls --profile duckdns" in line


def test_public_binds_everywhere_but_bind_restricts_to_one_ip(deploy):
    """Tailscale/VPN 시나리오: --bind 100.64.0.5 면 그 인터페이스에만 열린다 (LAN 에는 안 보임)."""
    _, line = dry(deploy, "--public")
    assert "bind=8787:8787" in line and "trust_proxy=0" in line
    _, line = dry(deploy, "--public", "--bind", "100.64.0.5")
    assert "bind=100.64.0.5:8787:8787" in line and "url=http://100.64.0.5:8787" in line


def test_url_override_replaces_the_private_ip_in_the_instructions(deploy):
    """EC2: hostname -I 는 사설 IP 라 --url 로 안내 주소를 바꿀 수 있어야 한다."""
    _, line = dry(deploy, "--public", "--url", "http://203.0.113.5:8787/")
    assert "url=http://203.0.113.5:8787 " in line + " "


def test_tunnel_requires_a_token_and_opens_no_port(deploy):
    code, line = dry(deploy, "--tunnel")
    assert code == 2 and "CLOUDFLARE_TUNNEL_TOKEN" in line or "토큰" in line
    code, line = dry(deploy, "--tunnel", env_lines=["CLOUDFLARE_TUNNEL_TOKEN=tok", "CLOUDFLARE_DOMAIN=mv.example.org"])
    assert code == 0
    assert "bind=127.0.0.1:8787:8787" in line and "url=https://mv.example.org" in line
    assert "trust_proxy=1" in line and "--profile tunnel" in line


def test_behind_proxy_is_loopback_upstream_with_trust(deploy):
    _, line = dry(deploy, "--behind-proxy", "--url", "https://mv.example.com")
    assert "mode=proxy bind=127.0.0.1:8787:8787" in line and "trust_proxy=1" in line


def test_mode_is_remembered_in_env_for_the_next_update(deploy):
    """`git pull && bash deploy/up.sh` 가 이전 모드를 유지해야 한다 — .env 의 MYVIKING_MODE."""
    _, line = dry(deploy, env_lines=["MYVIKING_MODE=domain", "MYVIKING_DOMAIN=viking.duckdns.org"])
    assert "mode=domain" in line and "viking.duckdns.org" in line
    _, line = dry(deploy, env_lines=["MYVIKING_MODE=public", "MYVIKING_BIND_IP=100.64.0.5"])
    assert "bind=100.64.0.5:8787:8787" in line
    # 이전 버전 .env (MODE 없음, 도메인만) 도 읽는다
    _, line = dry(deploy, env_lines=["MYVIKING_DOMAIN=old.duckdns.org", "MYVIKING_PORTS=127.0.0.1:8787:8787"])
    assert "mode=domain" in line and "old.duckdns.org" in line


def test_unknown_option_fails_loudly(deploy):
    code, line = dry(deploy, "--pubic")
    assert code == 2 and "알 수 없는 옵션" in line
