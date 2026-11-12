FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RISKAWARE_HOME=/data
WORKDIR /app
COPY pyproject.toml README.md README_zh.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[dashboard,llm]"
VOLUME ["/data"]
EXPOSE 8501
# default: run the daily scheduler; override with any riskaware command
ENTRYPOINT ["riskaware"]
CMD ["schedule"]
