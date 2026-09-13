# syntax=docker/dockerfile:1

# ---- 构建阶段：用 uv 按 uv.lock 安装依赖 ----
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# asyncmy 在 linux/arm64 上没有预编译 wheel，uv 会退回源码构建（Cython -> C），
# 需要 C 工具链。python:3.12-slim 自带 Python 头文件但不带 gcc，缺了会报
# "error: [Errno 2] No such file or directory: 'gcc'"。
# 只装在 builder 阶段，运行阶段镜像仍是纯 slim。
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用层缓存），再拷贝源码
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY main.py ./
COPY app ./app
COPY conf ./conf
COPY prompts ./prompts
COPY tools/audio_data/sql/audio.sql ./tools/audio_data/sql/audio.sql

# ---- 运行阶段 ----
FROM python:3.12-slim

RUN groupadd --system appgroup && useradd --system --gid appgroup appuser

WORKDIR /app
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/main.py ./main.py
COPY --from=builder /app/app ./app
COPY --from=builder /app/conf ./conf
COPY --from=builder /app/prompts ./prompts
COPY --from=builder /app/tools ./tools

RUN mkdir -p /app/logs && chown -R appuser:appgroup /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER appuser

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
