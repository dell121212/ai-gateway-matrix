# AI Gateway Matrix — 仪表盘容器
# 单独的 Dockerfile，而不是在 docker-compose 的 command 里现场 pip install：
# 那样每次重启容器都要重新联网下载依赖，容器启动慢，而且网络不通时直接起不来。
FROM python:3.12-slim

WORKDIR /app

COPY dashboard/requirements.txt /app/dashboard/requirements.txt
RUN pip install --no-cache-dir -r /app/dashboard/requirements.txt

# config.yaml / gateway/ / .env / dashboard 目录本身在 docker-compose.yml
# 里用 volume 挂载进来，这里不 COPY，保证你改代码/改配置不用重新 build 镜像。

EXPOSE 8080
CMD ["uvicorn", "dashboard.backend:app", "--host", "0.0.0.0", "--port", "8080"]
