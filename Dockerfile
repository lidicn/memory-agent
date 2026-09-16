FROM python:3.11-slim

WORKDIR /app

# 在线更新需要 git（容器内对挂载的宿主机仓库执行 pull）
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# 复制源码
COPY src/ ./src/
COPY pyproject.toml .

# 安装依赖（固定chromadb版本）
# paho-mqtt：v0.6 起在场推送（ma/presence）依赖，缺省会导致 MQTT 空转不推送
RUN pip install --no-cache-dir "chromadb==0.5.23" httpx bcrypt "python-jose[cryptography]" itsdangerous starlette uvicorn python-dotenv redis "mcp>=2.0.0" "pymysql>=1.1" "paho-mqtt>=1.6"

# 预下载chromadb ONNX嵌入模型（避免首次运行时超时）
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction; ef = DefaultEmbeddingFunction(); ef(['warmup'])" || true

# 创建数据目录
RUN mkdir -p /data/exports /data/templates /data/imported /data/skills

EXPOSE 8000

ENV PYTHONPATH=/app/src

CMD ["python", "-m", "uvicorn", "memory_agent.app:combined_app", "--host", "0.0.0.0", "--port", "8000"]
