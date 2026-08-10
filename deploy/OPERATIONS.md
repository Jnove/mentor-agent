# 部署与运维手册（5号交付）

本文只描述可重复执行的生产流程。服务器基础安装和真实密钥不得写入 Git。

## 1. 已在租服务器前准备好的内容

- Python 3.12 依赖已通过 `requirements.lock` 固定版本和 SHA-256 哈希。
- Dockerfile 使用固定 Python 补丁版本和多架构镜像 digest、固定 CPU-only Torch，并执行 `pip check`。
- Compose 默认只监听回环地址，支持独立项目名、镜像标签和端口，限制 Linux capabilities、PID 和日志大小。
- `deploy_preflight.py` 会阻断占位密钥、localhost 模型地址、无 SMTP、hash embedding、示例知识库和缺失锁文件。
- `prewarm_models.py` 会真实加载 embedding/reranker，避免首次线上请求冷启动。
- `smoke_test.py` 会检查 HTTPS 根页面和 Streamlit 健康端点。
- `ops_backup.py`/`ops_restore.py` 提供停止状态备份和“只恢复到空目录”的安全恢复流程。

## 2. 环境隔离

建议使用两台服务器；资源不足时可先使用同一台服务器的两个独立目录：

```text
/opt/mentor-agent-staging/     # APP_PORT=8502
/opt/mentor-agent-production/  # APP_PORT=8501
```

两套环境必须使用独立的 `.env`、`knowledge_base/`、`chroma_db/`、`data/` 和
`COMPOSE_PROJECT_NAME`。不要把测试用户库挂到生产环境。

## 3. 首次部署

```bash
git clone --recurse-submodules https://github.com/Jnove/mentor-agent.git /opt/mentor-agent-production
cd /opt/mentor-agent-production
cp deploy/production.env.example .env
chmod 600 .env
python3 scripts/deploy_preflight.py --mode production --env-file .env --min-docs 100

docker compose build
docker compose run --rm app python scripts/prewarm_models.py
docker compose run --rm app python ingest.py
docker compose up -d
docker compose ps
```

预检和治理门禁通过前禁止执行生产发布。知识库由独立子模块固定版本；生产只接纳
1号/2号审核为 `verified` 的正式目录文档。

## 4. 域名与 HTTPS

1. 将生产和测试域名的 A/AAAA 记录指向服务器。
2. 防火墙只开放 22、80、443；8501/8502 只能绑定 `127.0.0.1`。
3. 复制 `deploy/Caddyfile.example` 到 `/etc/caddy/Caddyfile` 并替换域名。
4. 执行 `caddy validate --config /etc/caddy/Caddyfile` 后 reload。
5. 生产冒烟：

```bash
python3 scripts/smoke_test.py --base-url https://mentor.example.com --require-https
```

还需人工验证注册、验证码、登录保持、退出、管理员、正常问答、引用、拒答和容器重启恢复。

## 5. 发布流程

1. 使用3号定义的质量门槛执行全部单测和检索评测。
2. 使用 Git commit SHA 作为 `IMAGE_TAG`，构建测试环境镜像。
3. 在测试环境执行预检、预热、建库和冒烟测试。
4. 停止生产 app，创建发布前备份，然后重新启动。
5. 部署相同镜像到生产，执行冒烟与人工检查。
6. 观察错误率、内存、磁盘、模型 API 和 SMTP 至少 30 分钟。

禁止直接在生产目录执行未经测试的 `git pull && docker compose up --build`。

## 6. 备份、恢复与回滚

一致性备份要求应用停止：

```bash
docker compose stop app
python3 scripts/ops_backup.py \
  --root /opt/mentor-agent-production \
  --output-dir /srv/mentor-backups \
  --confirm-app-stopped
docker compose start app
```

备份包默认不包含 `.env`。生产配置应存放在密码管理器或加密备份中；仅当外层存储已经
加密时才能添加 `--include-env`。备份目录应再通过 restic/rclone 等同步到服务器外部。

恢复演练只恢复到新空目录：

```bash
python3 scripts/ops_restore.py /srv/mentor-backups/mentor-agent-时间.tar.gz \
  --destination /srv/mentor-restore-test
```

验证知识文件、Chroma、SQLite 和应用启动后，再人工切换目录。恢复工具不会覆盖现有生产数据。

代码回滚使用上一 Git SHA 对应的镜像；知识或 embedding 变更则同时恢复发布前数据快照。

## 7. 监控与告警

服务器到位后至少配置：

- HTTPS 存活和证书到期监控；
- 容器 unhealthy/重启告警；
- CPU、内存、磁盘、备份失败告警；
- LLM API 超时、403/429、调用量和费用告警；
- SMTP 发送失败告警；
- 2号更新任务失败、知识长期未更新告警。

Compose 已将单容器日志限制为 10MB × 5 个文件；集中日志和外部告警仍需服务器到位后配置。

## 8. 依赖升级

只在独立升级 PR 中修改 `requirements.txt`，然后用 Python 3.12 重新生成锁文件：

```bash
python3.12 -m pip install pip-tools
python3.12 -m piptools compile --resolver=backtracking --generate-hashes \
  --strip-extras --allow-unsafe --output-file=requirements.lock requirements.txt
```

重新构建镜像、运行全部测试和检索回归后才能合并。

## 9. 5号真实问题收集

使用 `deploy/real_questions_template.csv`，从至少 5～8 名真实同学收集 30 条校园网、邮箱、
校园卡、统一身份认证、VPN、系统登录等问题。不得记录姓名、学号、手机号和具体成绩；
第一周完成 10 条，第二周累计 30 条，交1号复核后由3号纳入评测。
