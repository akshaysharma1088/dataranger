# Runs DataRanger as an MCP server over stdio (used by Glama and other MCP hosts).
FROM python:3.12-slim
RUN pip install --no-cache-dir "dataranger>=0.4.1"
WORKDIR /data
ENTRYPOINT ["dataranger", "mcp"]
