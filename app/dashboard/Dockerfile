# AI Gateway Matrix — 仪表盘 + 专业后台
# 多阶段：可选构建 React 控制台，再装 Python 依赖。

FROM node:24-bookworm-slim AS frontend
WORKDIR /frontend
COPY dashboard/frontend/package.json dashboard/frontend/package-lock.json* ./
RUN npm install --ignore-scripts
COPY dashboard/frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

COPY dashboard/requirements.txt /app/dashboard/requirements.txt
RUN pip install --no-cache-dir -r /app/dashboard/requirements.txt

# 预置 React 构建产物（运行时 volume 挂载 dashboard 时仍可用镜像内 dist 兜底）
COPY --from=frontend /frontend/dist /app/dashboard/frontend/dist

EXPOSE 8080
CMD ["uvicorn", "dashboard.backend:app", "--host", "0.0.0.0", "--port", "8080"]
