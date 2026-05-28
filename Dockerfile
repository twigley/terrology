FROM python:3.12-slim

RUN pip install uv --no-cache-dir

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen --extra web

COPY . .

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
