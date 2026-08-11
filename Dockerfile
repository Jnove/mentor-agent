ARG PYTHON_IMAGE=python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b
FROM ${PYTHON_IMAGE}

WORKDIR /app

# 先装锁定版本的 CPU-only torch：PyPI 默认构建可能拉入 CUDA 运行库。
# requirements.lock 中同样锁定 torch==2.13.0；这里预装后，哈希安装阶段会直接复用。
RUN python -m pip install --no-cache-dir \
    torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock \
    && python -m pip check

# 把版本标签放在依赖层之后；仅 Git SHA 变化时可以继续复用耗时的依赖安装缓存。
ARG APP_VERSION=dev
LABEL org.opencontainers.image.title="mentor-agent" \
      org.opencontainers.image.version="${APP_VERSION}"

COPY core/ core/
COPY scripts/ scripts/
COPY ui/ ui/
COPY .streamlit/ .streamlit/
COPY app.py ingest.py KB_FORMAT.md ./

# 模型缓存固定到 /root/.cache/huggingface，由 compose 挂成卷，换镜像不用重新下载
ENV HF_HOME=/root/.cache/huggingface \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 8501

# start-period 给足：首次启动要下载 reranker（约 1.1GB）
HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
