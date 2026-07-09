# 部署指南（Linux 服务器）

两种方式任选：

| 方式 | 适合场景 | 优点 | 缺点 |
|------|---------|------|------|
| **Docker（推荐）** | 长期跑在服务器上 | 环境一致、自带崩溃重启、升级回滚方便 | 需要装 Docker |
| **源码部署** | 没有 Docker 的机器、临时试用 | 无额外依赖、贴近本地开发 | 需自己管 Python 版本和进程守护 |

两种方式共用的前置准备：

1. 服务器内存建议 ≥ 4GB（reranker + torch 常驻约 2GB；内存紧张可在 `.env` 里设
   `RERANK_MODEL=off`，检索降级为混合召回排序，2GB 内存也能跑）。
2. 复制 `.env.example` 为 `.env` 填好配置。**注意两点**：
   - `LLM_BASE_URL` / `EMBED_BASE_URL` 不能指向 `localhost`（本地 LM Studio 那套配置
     只适用于开发机）。服务器上用云端 API，例如 `EMBED_BACKEND=local` + 云端 LLM。
   - Docker 部署时 `localhost` 指容器自己，更加连不上宿主机服务。

---

## 方式一：Docker 部署

### 首次部署

```bash
# 1. 装 Docker（已装可跳过）
curl -fsSL https://get.docker.com | sh

# 2. 获取代码并配置
git clone <仓库地址> mentor-agent && cd mentor-agent
cp .env.example .env && vim .env      # 填 LLM_API_KEY 等，注意上面的 localhost 问题

# 3. 构建并启动（首次构建拉 CPU 版 torch，约几分钟）
docker compose up -d --build

# 4. 建库（knowledge_base/ 里要先有文档）
docker compose run --rm app python ingest.py

# 5. 重启 app 让 BM25 索引看到新文档（索引建在内存里，ingest 不会自动生效）
docker compose restart app
```

首次启动会往 `hf-cache` 卷里下载 embedding 模型和 reranker（共约 1.2GB，走 hf-mirror），
期间界面起不来是正常的，`docker compose logs -f app` 可以看进度。之后重启秒起。

访问：默认只绑了 `127.0.0.1:8501`（Streamlit 无鉴权，不要裸奔公网），对外访问见
下方「反向代理与访问控制」；仅内网用可把 `compose.yaml` 里端口改成 `"8501:8501"`。

### 日常运维

| 场景 | 操作 |
|------|------|
| 数据组更新了文档 | 更新宿主机 `knowledge_base/` → `docker compose run --rm app python ingest.py` → `docker compose restart app` |
| 换了 embedding 模型 | 同上，但 ingest 加 `--rebuild` 全量重建 |
| 更新代码 | `git pull && docker compose up -d --build` |
| 看日志 | `docker compose logs -f app` |
| 停止 / 启动 | `docker compose down` / `docker compose up -d` |

### 备份

备份这三样即可完整恢复：`knowledge_base/`、`chroma_db/`、`.env`。
`chroma_db/` 丢了也能靠 `ingest.py --rebuild` 重建，只是要重新算一遍 embedding。

---

## 方式二：源码部署

### 安装

```bash
# Python 3.10+（建议 3.11/3.12；太新的版本 torch 可能暂无对应 wheel）
git clone <仓库地址> mentor-agent && cd mentor-agent
python3 -m venv .venv && source .venv/bin/activate

# 没有 GPU 的服务器先装 CPU 版 torch，省 3GB+ 磁盘
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

cp .env.example .env && vim .env
python ingest.py
```

试运行：`streamlit run app.py --server.address=127.0.0.1`，能访问后再配 systemd。

### systemd 守护（开机自启 + 崩溃重启）

`/etc/systemd/system/mentor-agent.service`（路径按实际部署位置改）：

```ini
[Unit]
Description=Mentor Agent (Streamlit)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/opt/mentor-agent
ExecStart=/opt/mentor-agent/.venv/bin/streamlit run app.py --server.address=127.0.0.1 --server.port=8501
Restart=on-failure
RestartSec=5
# 用普通用户跑，不要 root
User=www-data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mentor-agent
sudo systemctl status mentor-agent      # 看状态；日志：journalctl -u mentor-agent -f
```

日常运维和 Docker 方式对应：更新文档后 `python ingest.py` +
`sudo systemctl restart mentor-agent`；更新代码后 `git pull` + 重启。

---

## 反向代理与访问控制

Streamlit 没有任何鉴权，直接暴露公网意味着任何人都能消耗你的 LLM API 额度。
最简单的方案是 Caddy（自动 HTTPS + basic auth）：

```
# /etc/caddy/Caddyfile
mentor.example.com {
    basic_auth {
        # 密码哈希用 caddy hash-password 生成
        xlab $2a$14$xxxxxxxxxxxxxxxxxxxxxx
    }
    reverse_proxy 127.0.0.1:8501
}
```

用 Nginx 的话注意 Streamlit 走 WebSocket，`location /` 里要加：

```nginx
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";
```

---

## 常见问题

**首次启动很慢 / 界面打不开** —— 在下载模型（约 1.2GB）。Docker 看
`docker compose logs -f app`；下载完成后重启就是秒级。

**模型下载失败** —— 确认容器/服务器能访问 `hf-mirror.com`。代码在加载 reranker 时会
自动设置 `HF_ENDPOINT=https://hf-mirror.com` 并清掉代理环境变量（见
`core/retrieval.py` 的 `load_reranker`）；embedding 模型（`EMBED_BACKEND=local`）则
依赖 `.env` 或 compose 里的 `HF_ENDPOINT`。

**回答报错 / 连不上 LLM** —— 检查 `.env` 里 `LLM_BASE_URL` 是不是还指着
`localhost`（开发机的 LM Studio 配置），换成云端 API 地址。

**内存不够（OOM / 容器被杀）** —— `.env` 里设 `RERANK_MODEL=off`，省约 1.5GB 常驻内存。

**换了 embedding 模型后检索结果异常** —— 向量维度变了，必须
`python ingest.py --rebuild` 全量重建（Docker：`docker compose run --rm app python ingest.py --rebuild`）。

**版本可复现性** —— `requirements.txt` 只有下限约束，不同时间部署装出来的版本可能不同
（chromadb 大版本间数据格式变过）。要严格锁定的话，在一次可用的部署里
`pip freeze > requirements.lock`，之后统一用 `pip install -r requirements.lock` 安装。
