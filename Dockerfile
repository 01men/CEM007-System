# 聚杰电器·能耗与碳排管理系统 — 一体化镜像（含 2025 总表预置数据快照）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DINGTALK_MOCK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ server/
COPY web/ web/
COPY data/ data/

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8080"]
