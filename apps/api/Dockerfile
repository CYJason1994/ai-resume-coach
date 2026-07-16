FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[dev]"

COPY app ./app
COPY scripts ./scripts
COPY data ./data

# 打包 Alembic 配置与迁移脚本（P2-10）：让镜像自带迁移，prod 不再依赖宿主源码树挂载。
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations

EXPOSE 3001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3001"]
