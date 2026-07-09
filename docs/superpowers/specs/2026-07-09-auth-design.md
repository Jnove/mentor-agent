# 用户注册登录（邮箱验证码 + 后缀白名单）设计

日期：2026-07-09 ｜ 分支：`feature/auth` ｜ 状态：已与用户确认

## 需求

- 注册：邮箱 + 6 位验证码邮件（真验证所有权）+ 密码；邮箱后缀白名单，当前仅 `@zju.edu.cn`
- 登录 / 登出；登录状态跨浏览器刷新保持（cookie，7 天）
- 管理员角色 + 简单管理页：用户列表、禁用/启用、授予/撤销管理员
- SMTP 账号暂未落实：配置留 `.env`，未配置时进 dev 模式（验证码打印到服务器控制台）

## 方案选型

自研轻量认证模块（已确认，弃 streamlit-authenticator 与外置 IdP 两案：前者不含
邮箱验证码流程、后缀限制和管理页均需绕过其抽象；后者对社团项目运维过重）。

## 架构

```
core/auth.py           # 用户/验证码/token 全部逻辑，纯 Python + SQLite，不 import streamlit
core/mailer.py         # smtplib 发验证码；SMTP 未配置 → dev 模式打印验证码
ui/chat_page.py        # 现有问答界面整体迁入（逻辑不动）
ui/auth_pages.py       # 登录/注册表单
ui/admin_page.py       # 管理页（role=admin 可见）
scripts/make_admin.py  # 提升第一个管理员：python scripts/make_admin.py xxx@zju.edu.cn
app.py                 # 入口：认证门禁 + st.navigation（普通用户：问答；管理员：+用户管理）
```

唯一新增第三方依赖：`streamlit-cookies-controller`（写 cookie）。
哈希/发信/签名分别用标准库 `hashlib.scrypt` / `smtplib` / `hmac`。

## 数据模型

SQLite 文件 `data/auth.db`（git 忽略，Docker 部署时挂 `data/` 卷）：

- `users`: id, email UNIQUE, password_hash（scrypt + 每用户 16B 随机盐）, role
  ('user'|'admin'), status ('active'|'disabled'), failed_logins, locked_until,
  created_at, last_login_at
- `email_codes`: email, code_hash, expires_at, attempts, last_sent_at

会话无表：`AUTH_SECRET` 对 `user_id + 过期时间戳` HMAC 签名，token 存 cookie。
每次页面加载验签 + 查 `users.status`，禁用立即生效。

## 配置（.env 新增）

```
AUTH_SECRET=            # 必填，未配置启动即报错退出
ALLOWED_EMAIL_DOMAINS=zju.edu.cn   # 逗号分隔
SESSION_DAYS=7
SMTP_HOST= / SMTP_PORT= / SMTP_USER= / SMTP_PASSWORD= / SMTP_FROM=   # 全空 → dev 模式
```

## 流程

**注册**（单页三步，步骤状态存 session_state）：
1. 输邮箱 → 后缀在白名单、未被注册 → 发验证码（10 分钟有效；同邮箱 60s 内不重发）
2. 输验证码 → `hmac.compare_digest` 比对哈希；错 5 次作废需重发
3. 设密码（≥8 位）→ 建用户 → 自动登录

**登录**：scrypt（n=2^14, r=8, p=1）校验；连错 5 次锁 15 分钟；错误提示不区分
"邮箱不存在/密码错"（防注册探测）→ 签 token 写 cookie → rerun。

**门禁**（每次加载）：读 cookie → 验签/验过期 → 查库 status=active →
挂 `st.session_state.user`；任一步失败清 cookie 回登录页。

**登出**：删 cookie + 清空 session_state（含聊天记录与笔记）。

**管理页**：用户表格（邮箱/角色/状态/注册时间/最后登录），行内禁用/启用、
授予/撤销管理员；管理员不能操作自己。

## 错误处理

- SMTP 发送失败：页面提示稍后重试，日志记录异常
- dev 模式：页面显示黄条警告，防止误上生产
- `AUTH_SECRET` 缺失：启动即报错退出（不默默用弱密钥）
- SQLite：短连接 + WAL；Streamlit 并发量级足够

## 测试

`core/auth.py` 纯逻辑单测（沿用 `python tests/test_core.py` 风格）：
后缀校验、scrypt 往返、token 签验/过期/篡改、验证码过期与次数上限、登录锁定。

## 范围外（YAGNI）

忘记密码 / 改密码、退出所有设备、邮箱变更、验证码图形防刷、
对话与笔记落盘（另有 TODO）。

## 收尾同步

`.env.example`、`compose.yaml`（挂 `data/` 卷）、`DEPLOY.md`、README、
`requirements.txt`（+streamlit-cookies-controller）。
