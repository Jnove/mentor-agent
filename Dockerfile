FROM python:3.12-slim

WORKDIR /app

# 先装 CPU 版 torch：默认源的 torch 带 CUDA（大 3GB+，服务器多半没 GPU）。
# 先装好后，下面 requirements 里 sentence-transformers 检测到 torch 已满足就不会再拉。
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/ core/
COPY scripts/ scripts/
COPY app.py ingest.py KB_FORMAT.md ./

# 模型缓存固定到 /root/.cache/huggingface，由 compose 挂成卷，换镜像不用重新下载
ENV HF_HOME=/root/.cache/huggingface \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 8501

# start-period 给足：首次启动要下载 reranker（约 1.1GB）
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
