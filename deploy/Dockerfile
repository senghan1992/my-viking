# 2단계 빌드: 빌드 도구는 최종 이미지에 남기지 않습니다.
FROM python:3.12-slim AS build

WORKDIR /build
RUN pip install --no-cache-dir build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m build --wheel --outdir /wheels


FROM python:3.12-slim

# [server] extra 가 대시보드·HTTP API·원격 MCP 를 포함합니다. 이것을 빼면
# jv serve 가 fastapi 를 찾지 못합니다.
# numpy 는 필수가 아니지만 검색 지연을 3배 낮춥니다. 결과는 동일합니다.
COPY --from=build /wheels/*.whl /tmp/
RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[server]" numpy \
 && rm -rf /tmp/*.whl

# 루트 파일시스템을 읽기 전용으로 굴려도 조용히 동작하도록.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 컨텍스트 DB 를 root 로 굴릴 이유가 없습니다. 이름 있는 볼륨은 첫 마운트 때
# 이미지 안 디렉터리의 소유권을 물려받으므로, 여기서 미리 잡아둡니다.
ENV JARVIS_HOME=/data
RUN useradd --create-home --uid 10001 viking \
 && mkdir -p /data \
 && chown -R viking:viking /data
USER viking
VOLUME /data

EXPOSE 8787

# 컨테이너 안에서는 반드시 0.0.0.0 에 바인딩해야 외부에서 닿습니다.
# 노출 여부는 compose 의 포트 매핑과 API 키로 통제하세요.
CMD ["jv", "serve", "--host", "0.0.0.0", "--port", "8787"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8787/health').read()"
