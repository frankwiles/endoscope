FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY endoscope/ endoscope/

RUN pip install --no-cache-dir uv && \
    uv sync --frozen --no-dev

COPY . .

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

CMD ["endoscope", "serve"]
