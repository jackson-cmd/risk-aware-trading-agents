# Build from the repository root: docker build -f docker/Dockerfile -t riskaware .
# Released images: docker run --rm -p 8501:8501 ghcr.io/jackson-cmd/risk-aware-trading-agents dashboard
FROM python:3.12-slim

# RISKAWARE_CONTAINER: the dashboard listens on 0.0.0.0 here, and the host's browser counts as this computer
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RISKAWARE_HOME=/data RISKAWARE_CONTAINER=1
WORKDIR /app
COPY pyproject.toml README.md README_zh.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[dashboard,llm,mcp]"
VOLUME ["/data"]
EXPOSE 8501
# the MCP Registry checks this label to confirm the image belongs to the server it lists
LABEL io.modelcontextprotocol.server.name="io.github.jackson-cmd/risk-aware-trading-agents"
# default: run the daily scheduler; override with any riskaware command
ENTRYPOINT ["riskaware"]
CMD ["schedule"]
