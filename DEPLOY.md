# 部署指南（Linux 服务器）

生产环境的完整发布、预检、备份恢复和回滚流程见
[`deploy/OPERATIONS.md`](deploy/OPERATIONS.md)。本页保留首次安装和常见问题速查。

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

# 2. 获取代码并配置（生产环境使用专用模板）
git clone <仓库地址> mentor-agent && cd mentor-agent
cp deploy/production.env.example .env && chmod 600 .env
vim .env

# 3. 检出主仓库固定的知识库版本，然后执行发布门禁
git submodule update --init --recursive
python3 scripts/deploy_preflight.py --mode production --env-file .env --min-docs 100

# 4. 构建并预下载模型（首次拉 CPU 版 torch + 模型，约几分钟）
docker compose build
docker compose run --rm app python scripts/prewarm_models.py

# 5. 先审计知识库（knowledge_base/ 里要先有文档）
docker compose run --rm app python scripts/govern_kb.py
# 只有退出码为 0 且 release_ready=true 才继续

# 6. 建库
docker compose run --rm app python ingest.py

# 7. 启动并检查
docker compose up -d
docker compose ps
```

管理员：在 `.env` 里填 `ADMIN_EMAILS=你的邮箱@zju.edu.cn`（逗号分隔可多个），
该邮箱在页面正常注册后即拥有「用户管理」页。老账号加进名单后，退出重新登录即生效
（只提升不降级，撤销管理员请在管理页操作）。

也可以对已注册账号手动提升：

```bash
docker compose run --rm app python scripts/make_admin.py 你的邮箱@zju.edu.cn
```

首次启动会往 `hf-cache` 卷里下载 embedding 模型和 reranker（共约 1.2GB，走 hf-mirror），
期间界面起不来是正常的，`docker compose logs -f app` 可以看进度。之后重启秒起。

访问：默认只绑了 `127.0.0.1:8501`（Streamlit 无鉴权，不要裸奔公网），对外访问见
下方「反向代理与访问控制」；仅内网用可把 `compose.yaml` 里端口改成 `"8501:8501"`。

### 日常运维

| 场景 | 操作 |
|------|------|
| 数据组更新了文档 | 更新主仓库固定的知识库子模块 commit → 运行 `scripts/govern_kb.py` 审计 → `docker compose run --rm app python ingest.py` → 重启 app |
| 换了 embedding 模型 | 同上，但 ingest 加 `--rebuild` 全量重建 |
| 更新代码 | `git pull && docker compose up -d --build` |
| 看日志 | `docker compose logs -f app` |
| 停止 / 启动 | `docker compose down` / `docker compose up -d` |

### 备份

备份数据前先停止 app，避免 SQLite/Chroma 复制到不一致状态：

```bash
docker compose stop app
python3 scripts/ops_backup.py --root "$PWD" --output-dir /srv/mentor-backups --confirm-app-stopped
docker compose start app
```

备份包默认不包含 `.env`；配置应进入密码管理器或加密异地备份。安全恢复工具只允许恢复到
新的空目录，详见 `deploy/OPERATIONS.md`。`chroma_db/` 丢失后可重建，但用户库和正式知识不可替代。

---

## 方式二：源码部署

### 安装

```bash
# Python 3.10+（建议 3.11/3.12；太新的版本 torch 可能暂无对应 wheel）
git clone <仓库地址> mentor-agent && cd mentor-agent
python3 -m venv .venv && source .venv/bin/activate

# 没有 GPU 的服务器先装锁定的 CPU 版 torch，避免拉入 CUDA 运行库
pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
pip install --require-hashes -r requirements.lock

cp .env.example .env && vim .env
python scripts/govern_kb.py  # 必须 errors=0
python ingest.py
```

试运行：`streamlit run app.py --server.address=127.0.0.1`，能访问后再配 systemd。

### systemd 守护（开机自启 + 崩溃重启）

生产环境优先基于仓库的 `deploy/mentor-agent.service` 修改路径和用户；它还包含回环监听、
只读系统目录和最小可写路径。下面是结构说明用的简化示例。定时一致性备份、本机健康检查、
静态证书到期检查及安装顺序见 `deploy/OPERATIONS.md` 的 6.1 和 7.1 节。

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

日常运维和 Docker 方式对应：更新文档后先运行 `python scripts/govern_kb.py`，`errors=0` 后再执行 `python ingest.py` +
`sudo systemctl restart mentor-agent`；更新代码后 `git pull` + 重启。

---

## 反向代理与访问控制

应用已实现邮箱登录，但 Streamlit 自身不提供 TLS、限流和安全响应头；直接暴露 8501
还会导致 Secure 登录 cookie 无法在 HTTP 下保存。最简单的方案是 Caddy（自动 HTTPS，
测试环境还可叠加 basic auth）：

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

认证相关的安全基线：登录 cookie 已设 Secure 标志，**生产环境必须置于 HTTPS 反代之后**
（否则浏览器不会保存登录态）；cookie 由前端组件写入、无法设 HttpOnly，XSS 防护依赖
反代层配置 CSP 兜底。已知的低风险留档项见 docs/superpowers/specs/2026-07-09-auth-design.md 对应实现。

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

**版本可复现性** —— 生产和 CI 已统一使用 `requirements.lock`；它由 Python 3.12 的
`pip-compile --generate-hashes` 生成。依赖升级必须重新生成 lock、构建测试镜像并跑完回归，
不得在生产服务器临时 `pip install -U`。

**注册收不到验证码** —— `.env` 里 SMTP 未配置时是开发模式，验证码打印在
`docker compose logs -f app` 里；配置了 SMTP 仍收不到，检查授权码和 465 端口连通性。

**AUTH_SECRET 报错退出** —— 认证功能要求 `.env` 里必须配置 `AUTH_SECRET`，
用 `python -c "import secrets; print(secrets.token_hex(32))"` 生成一个随机值填入。
