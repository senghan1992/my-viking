# myviking — 단일 컨테이너 서버 (대시보드 + Agent API + 지식 저장소)
FROM python:3.12-slim

WORKDIR /srv
COPY . .

RUN pip install --no-cache-dir . && \
    rm -rf tests docs examples tools src/jarvis

ENV VIKING_DATA=/data \
    MYVIKING_IN_CONTAINER=1 \
    PYTHONUNBUFFERED=1

VOLUME /data
EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/v1/health', timeout=4)"

CMD ["python", "-c", "from app.main import run; run()"]