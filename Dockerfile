# Local Agent Server: no LangSmith account or production server license required.
FROM node:22-bookworm-slim AS flyai
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LANGSMITH_TRACING=false
WORKDIR /app
COPY --from=flyai /usr/local/bin/node /usr/local/bin/node
COPY --from=flyai /app/node_modules ./node_modules
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev --no-install-project
COPY agent.py daily_planner.py itinerary.py dev_server.py ./
COPY system_prompt.md daily_planner_prompt.md ./
COPY skills ./skills
COPY langgraph.docker.json ./langgraph.docker.json
EXPOSE 8123
CMD [".venv/bin/langgraph", "dev", "--config", "langgraph.docker.json", "--host", "0.0.0.0", "--port", "8123", "--no-browser", "--no-reload"]
