# ---- Stage 1: 前端构建 ----
FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /frontend
# 先 COPY manifest + 装依赖, 再 COPY 源码. 源码 COPY 必须**显式
# 列举** src / public / 配置文件, 不得用 `COPY frontend/ ./` —
# 那样会把主机 frontend/node_modules (含 pnpm symlink 链到
# ../.pnpm/...) 一起拷进来, 覆盖刚 `npm ci` 装好的 node_modules,
# 触发 overlay2 "cannot replace to directory ... with file" 错误.
# .dockerignore 也已屏蔽 node_modules / dist / pnpm-* 残留.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/src ./src
COPY frontend/public ./public
COPY frontend/index.html frontend/vite.config.ts ./
COPY frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json ./
COPY frontend/eslint.config.js frontend/components.json ./
# 容器内前端永远使用真实后端 API（同源 /api/v1）
RUN VITE_API_MODE=real npm run build

# ---- Stage 2: Python 后端 + 静态产物 ----
FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN groupadd --gid 1000 media-pilot \
    && useradd --uid 1000 --gid media-pilot --home-dir /app --shell /usr/sbin/nologin media-pilot \
    && pip install --no-cache-dir uv==0.11.8 \
    && mkdir -p /data/downloads /data/watch /data/workspace /data/library/movies /data/library/shows /data/library/adult /data/db /data/trash \
    && chown -R media-pilot:media-pilot /app /data

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked --no-dev

# 复制前端构建产物
COPY --from=frontend-builder /frontend/dist ./frontend-dist
RUN chown -R media-pilot:media-pilot /app/frontend-dist

USER media-pilot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"

CMD ["python", "-m", "media_pilot"]
