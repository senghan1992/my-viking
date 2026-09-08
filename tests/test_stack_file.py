"""배포 파일 계약: docker-compose.yml(up.sh 용) 과 stack.yml(직접 관리·Portainer 용).

같은 서비스가 두 파일에 나뉘어 정의되므로, 한쪽에만 보안/운영 기본값이 빠지는
갈림을 방지한다. Portainer 는 환경변수 치환을 지원하지만 스택을 복붙해서 쓰는
사람은 ${VAR} 을 보면 당황하므로, stack.yml 은 치환 없이 자기 완결이어야 한다.
"""

from __future__ import annotations

import pathlib
import re

import yaml

DEPLOY = pathlib.Path(__file__).resolve().parents[1] / "deploy"

SHARED = {
    "restart": "unless-stopped",
    "read_only": True,
    "security_opt": ["no-new-privileges:true"],
    "tmpfs": ["/tmp"],
}


def _myviking(name: str):
    data = yaml.safe_load((DEPLOY / name).read_text(encoding="utf-8"))
    svc = data["services"]["myviking"]
    # short form ("myviking-data:/data") 을 long form 과 같은 모양으로
    if isinstance(svc.get("volumes"), list):
        svc["volumes"] = {
            v.split(":")[0]: {"type": "volume", "target": v.split(":", 1)[1]}
            for v in svc["volumes"]
        }
    return data, svc


def _healthcheck(svc):
    parts = svc["healthcheck"]["test"]
    joined = " ".join(parts) if isinstance(parts, list) else str(parts)
    assert "/health" in joined, "헬스체크가 /health 를 봐야 한다"


def test_compose_and_stack_share_the_service_contract():
    for name in ("docker-compose.yml", "stack.yml"):
        _, svc = _myviking(name)
        assert svc["image"] == "myviking:latest", name
        for key in ("restart", "read_only", "security_opt", "tmpfs"):
            assert svc.get(key) == SHARED[key], f"{name}: {key} 기본값이 다름"
        assert svc["volumes"]["myviking-data"]["target"] == "/data", name
        assert svc["environment"]["JARVIS_HOME"] == "/data", name
        _healthcheck(svc)
        # 실제 사용하는 포트는 컨테이너 안 8787 (호스트 바인딩은 운영자가 정한다)
        ports = svc["ports"] if isinstance(svc["ports"], list) else [svc["ports"]]
        assert any(":8787" in p or p.endswith("8787") for p in ports), name


def test_stack_file_is_self_contained_no_env_indirection():
    """Portainer 에 복붙하는 사람은 ${VAR} 을 못 본다 — 치환 없이 열려야 한다."""
    text = (DEPLOY / "stack.yml").read_text(encoding="utf-8")
    assert "${" not in text, "stack.yml 은 환경변수 치환이 없어야 한다"
    assert 'container_name: myviking' in text
    data, svc = _myviking("stack.yml")
    assert "build" not in svc, "stack.yml 은 이미지 사용 (빌드는 서버에서 한 번)"
    assert data["name"] == "myviking", "up.sh 와 오가도 볼륨이 이어지도록 같은 프로젝트 이름"
    ports = svc["ports"] if isinstance(svc["ports"], list) else [svc["ports"]]
    assert "8787:8787" in ports[0], "stack.yml 기본도 모든 인터페이스 (운영자가 바꿈)"


def _render(line: str, env: dict[str, str]) -> str:
    """compose 의 ${VAR:-기본값} 치환을 흉내 낸다 — .env 값이 ports 에 어떻게
    반영되는지를 docker 없이 고정하기 위함."""

    def sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        val = env.get(name, "")
        return val if val else default

    return re.sub(r"\$\{([A-Z0-9_]+):-([^}]*)\}", sub, line)


def test_default_port_is_open_on_all_interfaces_for_lan_port_forwarding():
    """기본은 'compose up -d 만으로 사내 LAN/포트포워딩' — 모든 인터페이스 8787.
    .env 의 MYVIKING_PORT/MYVIKING_BIND 가 그대로 반영돼야 한다.
    키가 없으면 인증 없이 열려 있으므로, 열기 전 키 생성은 문서·주석의 몫이다."""
    _, svc = _myviking("docker-compose.yml")
    ports = svc["ports"]
    assert len(ports) == 1
    port_line = ports[0]
    assert "MYVIKING_BIND" in port_line and "MYVIKING_PORT" in port_line
    # 기본 (env 없음): 모든 인터페이스 8787
    assert _render(port_line, {}) == "0.0.0.0:8787:8787"
    # .env 로 바꾼 값이 그대로 반영
    assert (
        _render(port_line, {"MYVIKING_BIND": "127.0.0.1", "MYVIKING_PORT": "9000"})
        == "127.0.0.1:9000:8787"
    )


def test_env_example_documents_port_and_bind_keys():
    """.env.example 이 compose 가 읽는 키를 안내해야 한다 — 여기서 갈라지면
    사용자가 어느 키를 고쳐야 할지 모른다."""
    text = (DEPLOY / ".env.example").read_text(encoding="utf-8")
    assert "MYVIKING_PORT=" in text and "MYVIKING_BIND=" in text