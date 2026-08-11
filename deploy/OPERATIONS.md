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
- `ops_systemd_backup.py` 会保留应用原有启停状态，并在归档失败或收到终止信号后尝试恢复服务。
- systemd timer 模板会定期执行一致性备份、本机健康检查和静态证书到期检查。

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

若宿主机能访问模型网关、Docker bridge 却持续超时，Linux 服务器可在所有 Compose 命令中
叠加 `-f compose.yaml -f deploy/compose.host-network.yaml`。该 override 复用宿主机路由，
同时强制 Streamlit 继续只监听 `127.0.0.1:${APP_PORT}`；应用仍只能经 Caddy 访问。
启用前后都应运行 `docker compose ... config --quiet` 并用 `ss -ltn` 确认 8501/8502
没有监听在 `0.0.0.0` 或 `[::]`。这只用于明确确认 bridge 路由异常的服务器。

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

### 6.1 源码 + systemd 部署的自动备份

当前生产机使用 `mentor-agent.service` 而不是 Compose 时，不能照抄上面的
`docker compose stop/start`。仓库提供以下模板：

- `deploy/mentor-agent-backup.service` / `.timer`：每天上海时间 03:20 执行；
- `scripts/ops_systemd_backup.py`：持有非阻塞文件锁，确认 unit 状态后才停服；
- `deploy/mentor-agent-ops.env.example`：非敏感路径、unit 名和阈值配置。

runner 会记录任务开始前应用是否为 `active`。只有原本正在运行时才会停服，并且在
归档成功、失败或收到 `SIGTERM` 后都尝试重新启动；原本已停止的服务不会被意外拉起。
root runner 和其调用的工具固定安装在 root-owned 的 `/usr/local/libexec/mentor-agent`，
不能从应用用户可写的 release 目录执行。实际归档进程仍以 `MENTOR_RUN_AS_USER` 降权运行。
若无法确认 unit 已停止、存在并发任务或服务恢复失败，任务会以非零状态结束。
`--confirm-app-stopped` 只由 runner 在实际停服后传入，不应在 timer 中直接调用。

安装前先逐项检查路径和系统用户，再执行：

```bash
sudo install -d -o root -g root -m 0755 /etc/mentor-agent
sudo install -d -o root -g root -m 0755 /usr/local/libexec/mentor-agent
sudo install -d -o txc -g txc -m 0700 /srv/mentor-backups
sudo install -o root -g root -m 0644 \
  deploy/mentor-agent-ops.env.example /etc/mentor-agent/ops.env
sudo install -o root -g root -m 0755 \
  scripts/ops_systemd_backup.py scripts/ops_backup.py \
  scripts/ops_health_check.py scripts/ops_cert_check.py \
  /usr/local/libexec/mentor-agent/
sudo install -o root -g root -m 0644 \
  deploy/mentor-agent-backup.service deploy/mentor-agent-backup.timer \
  deploy/mentor-agent-health.service deploy/mentor-agent-health.timer \
  deploy/mentor-agent-cert-check.service deploy/mentor-agent-cert-check.timer \
  /etc/systemd/system/

sudo systemd-analyze verify \
  /etc/systemd/system/mentor-agent-{backup,health,cert-check}.{service,timer}
sudo systemctl daemon-reload
```

先手工执行并查看日志。备份检查会短暂停止应用，应安排在低峰期：

```bash
sudo systemctl start mentor-agent-health.service
sudo systemctl start mentor-agent-cert-check.service
sudo systemctl start mentor-agent-backup.service
sudo journalctl -u mentor-agent-health.service \
  -u mentor-agent-cert-check.service -u mentor-agent-backup.service --since today
```

全部成功后才启用 timer：

```bash
sudo systemctl enable --now \
  mentor-agent-health.timer mentor-agent-cert-check.timer mentor-agent-backup.timer
systemctl list-timers 'mentor-agent-*'
```

timer 只创建本机备份，不实现删除或异机同步。启用前必须制定保留策略；建议至少保留
14 个每日快照和 8 个每周快照，并使用 restic 等工具加密同步到另一台主机或对象存储。
删除任务只能匹配 `mentor-agent-*.tar.gz`，且始终保留最新一份，禁止对宽泛目录执行递归
删除。每周恢复到一个全新的空目录验证 manifest，每月至少做一次应用启动恢复演练。
若修改 `MENTOR_BACKUP_DIR` 或 `MENTOR_BACKUP_LOCK`，还必须同步修改 backup service 的
`ReadWritePaths`，否则 `ProtectSystem=strict` 会按预期拒绝写入。

例行包默认不含 `.env`。服务器上临时保存的 `.env` 副本仍是明文，权限 `0600` 不能替代
加密异机备份或密码管理器。

## 7. 监控与告警

服务器到位后至少配置：

- HTTPS 存活和证书到期监控；
- 容器 unhealthy/重启告警；
- CPU、内存、磁盘、备份失败告警；
- LLM API 超时、403/429、调用量和费用告警；
- SMTP 发送失败告警；
- 2号更新任务失败、知识长期未更新告警。

Compose 已将单容器日志限制为 10MB × 5 个文件；集中日志和外部告警仍需服务器到位后配置。

### 7.1 systemd 健康和证书 timer

`mentor-agent-health.timer` 每分钟调用回环地址的 Streamlit 健康端点。脚本拒绝非
`localhost`/`127.0.0.1`/`::1` 地址、认证信息和重定向，默认要求 HTTP 200 且响应包含
`ok`。检查失败只让 unit 进入 failed 并写入 journal，不会因一次瞬时失败自动重启应用。
它能发现应用进程卡死，但不能发现整机、校园网 DNS、IPv4/IPv6 路由或外部 Caddy 故障。

`mentor-agent-cert-check.timer` 每天读取 Caddy 的 fullchain，通过
`openssl x509 -checkend` 检查未来 30 天仍有效。模板以 `caddy:caddy` 运行，只需要读取
公有证书，不读取私钥。若发行版使用不同的 Caddy 账户，必须先修改 unit。检查只负责
提前失败告警，**不会续期或同步证书**。

当前 `Caddyfile.production` 显式指定证书和私钥，因此 Caddy 自动 ACME 已关闭。应在真实
签发主机的 acme.sh 续期成功 hook 中，将新证书上传到服务器的受限 incoming 目录，再由
root-owned 安装器完成 SAN、有效期、cert/key 匹配校验、同目录原子替换、
`caddy validate` 和 reload。不要复制 ACME 账户私钥，不要授予通用免密 sudo，也不能把
一次性 SSH 反向隧道当作续期机制。

以上三个检查的非零退出本身不是通知渠道。上线时必须将 systemd failed unit 接入独立
告警，并从另一台位于校园网或 RVPN 内的主机每 2～5 分钟分别验证 IPv4、IPv6 的
`https://mentor.zjuxlab.com/_stcore/health`。同机 timer 和异机探测缺一不可。还应监控：

- 最新成功备份是否超过 26 小时、异机同步和恢复演练是否失败；
- TLS 剩余天数、磁盘和 inode 是否超过 80%、systemd 重启次数；
- LLM 的 401/429/超时、SMTP 失败和知识库最后成功更新时间。

源码 + systemd 部署的日志由 journald 管理，Compose 的 `10MB × 5` 限制对它无效；应另设
journald 容量和保留期限，并避免把验证码、邮箱、用户问题或模型密钥写入日志。

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
