FROM python:3.11-slim

WORKDIR /app

# 在线更新需要 git（容器内对挂载的宿主机仓库执行 pull）
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

# 复制源码
COPY src/ ./src/
COPY pyproject.toml .
# 机制层库（DCD 裁定 20261002 · MA Q1 = A：vendored wheel 进本仓 + Dockerfile 一行）。
# 不走「六仓共挂一个可写目录当 PYTHONPATH」：那条路把供应链信任面扩成"谁改目录谁影响六仓"。
# sha256 与来源登记在 vendor/README.md；换版本必须同批改那一行与那份登记。
COPY vendor/ ./vendor/

# 安装依赖（固定chromadb版本）
# paho-mqtt：v0.6 起在场推送（ma/presence）依赖，缺省会导致 MQTT 空转不推送
RUN pip install --no-cache-dir "chromadb==0.5.23" httpx bcrypt "python-jose[cryptography]" itsdangerous starlette uvicorn python-dotenv redis "mcp>=2.0.0" "pymysql>=1.1" "paho-mqtt>=1.6"

# homesdk 0.3.1 装进镜像：presence/time 两条腿在**运行面**在场（此前只有测试面看得见它）。
# 时区主路径不在这里换档——追认的门要求家庭时区按 IANA 名显式声明（HOMESDK_TZ），
# 那个键由 DCD 在合并窗写进各仓 compose，装了库不会让时间轴漂移。
RUN pip install --no-cache-dir ./vendor/homesdk-0.3.1-py3-none-any.whl

# 创建数据目录并赋予非 root 用户权限（WO-MA-001 / 审计 P0-10）
RUN mkdir -p /data/exports /data/templates /data/imported /data/skills \
    && chown -R 10001:10001 /data /app

# 非 root 用户运行：root 容器 + rw 宿主挂载是同一问题的两半
USER 10001:10001

EXPOSE 8000

ENV PYTHONPATH=/app/src

CMD ["python", "-m", "uvicorn", "memory_agent.app:combined_app", "--host", "0.0.0.0", "--port", "8000"]
