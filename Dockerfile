FROM python:3.11-slim

WORKDIR /app

# 安装 uv
RUN pip install --no-cache-dir uv

# 先复制依赖文件，利用 Docker 层缓存
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -e .

# 复制源码
COPY src/ ./src/

# 默认使用内存数据库（单体运行无需挂载外部文件）
ENV RECAP_DB_PATH=:memory:
ENV RECAP_LOG_LEVEL=INFO

EXPOSE 8000

CMD ["python", "-m", "stock_recap", "--serve", "--host", "0.0.0.0", "--port", "8000"]
