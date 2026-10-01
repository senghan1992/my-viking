"""scribe — 에이전트 머신용 scribe 클라이언트.

- hooks: Claude Code 훅 자동 설치/점검 + 이벤트 처리 (질문→답을 '관찰'로 전송)
- remote: brief / search / note / observe / inbox / ack / remember / score (셸, 어떤 에이전트든)
- secretary: 서기 agent — 별도 pi 세션으로 관찰함을 읽고 지식으로 정리
- pi/omp: 허브 확장 설치·폴더 연결 관리 (scribe.ts)
- mcp: MCP stdio 서버 (Cursor·Codex 등 MCP 지원 에이전트용)
"""
__version__ = "2.0.0"