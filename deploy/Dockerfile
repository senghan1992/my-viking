FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# numpy 는 필수가 아니지만 검색 지연을 한 자릿수 ms 대로 낮춥니다.
RUN pip install --no-cache-dir ".[server]" numpy

ENV JARVIS_HOME=/var/lib/myviking
VOLUME /var/lib/myviking
EXPOSE 8787

CMD ["jv", "serve", "--host", "0.0.0.0", "--port", "8787"]
