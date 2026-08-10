"""部署前配置与数据预检（仅标准库，不会发起网络请求）。

示例：
    python scripts/deploy_preflight.py --mode production --env-file .env --min-docs 100

退出码 0 表示没有阻断项；退出码 1 表示存在 ERROR，不应继续发布。
"""
from __future__ import annotations

import argparse
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Check:
    level: str
    name: str
    message: str


_PLACEHOLDER_PARTS = (
    "xxxxxxxx",
    "changeme",
    "change-me",
    "replace-me",
    "example.com",
    "你的",
    "实际接口",
    "<",
    ">",
)


def load_dotenv_file(path: Path) -> dict[str, str]:
    """读取本项目使用的简单 KEY=VALUE dotenv 格式，不修改 os.environ。"""
    values: dict[str, str] = {}
    text = path.read_text(encoding="utf-8-sig")
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"{path}:{lineno}: 不是 KEY=VALUE 格式")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"{path}:{lineno}: 非法变量名 {key!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def _placeholder(value: str) -> bool:
    lower = value.strip().lower()
    return not lower or any(part in lower for part in _PLACEHOLDER_PARTS)


def _host_is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


def validate(env: dict[str, str], root: Path, mode: str,
             min_docs: int = 1) -> list[Check]:
    checks: list[Check] = []
    server_mode = mode in {"staging", "production"}
    production = mode == "production"

    def result(ok: bool, name: str, good: str, bad: str,
               warn: bool = False) -> None:
        checks.append(Check("OK" if ok else ("WARN" if warn else "ERROR"),
                            name, good if ok else bad))

    llm_key = env.get("LLM_API_KEY", "")
    result(not _placeholder(llm_key), "LLM_API_KEY",
           "已配置模型密钥", "缺失或仍是示例值")
    result(not _placeholder(env.get("LLM_MODEL", "")), "LLM_MODEL",
           "已配置主模型", "未配置主模型")
    light_model = env.get("LLM_MODEL_LIGHT", "").strip()
    if light_model:
        result(not _placeholder(light_model), "LLM_MODEL_LIGHT",
               "轻量模型已配置", "轻量模型仍是占位值")

    llm_url = env.get("LLM_BASE_URL", "").strip()
    if llm_url:
        valid_url = urlparse(llm_url).scheme in {"http", "https"}
        result(valid_url, "LLM_BASE_URL", "URL 格式有效", "必须是 http(s) URL")
        if server_mode:
            result(not _host_is_local(llm_url), "LLM_BASE_URL host",
                   "未指向服务器自身", "服务器模式不能使用 localhost/127.0.0.1")
    else:
        checks.append(Check("WARN", "LLM_BASE_URL", "留空将使用 OpenAI 默认地址"))

    secret = env.get("AUTH_SECRET", "")
    result(not _placeholder(secret) and len(secret) >= 32, "AUTH_SECRET",
           "已配置长度足够的随机密钥", "必须是至少 32 字符且非示例值的随机密钥")

    domains = [x.strip().lstrip("@").lower()
               for x in env.get("ALLOWED_EMAIL_DOMAINS", "").split(",") if x.strip()]
    result(bool(domains) and "*" not in domains, "ALLOWED_EMAIL_DOMAINS",
           "注册邮箱后缀已限制", "至少配置一个明确后缀，不能使用通配符")

    admins = [x.strip().lower() for x in env.get("ADMIN_EMAILS", "").split(",") if x.strip()]
    admins_valid = bool(admins) and all(
        not _placeholder(x) and "@" in x and x.rsplit("@", 1)[1] in domains
        for x in admins
    )
    result(admins_valid or not production, "ADMIN_EMAILS",
           "管理员邮箱已配置", "生产环境至少需要一个符合白名单的管理员邮箱",
           warn=not production)

    try:
        days = int(env.get("SESSION_DAYS", "7"))
        session_ok = 1 <= days <= 30
    except ValueError:
        session_ok = False
    result(session_ok, "SESSION_DAYS", "会话期限有效", "必须是 1～30 的整数")

    backend = env.get("EMBED_BACKEND", "local").strip().lower()
    result(backend in {"local", "api", "hash"}, "EMBED_BACKEND",
           f"使用 {backend} 后端", "只允许 local/api/hash")
    if production and backend == "hash":
        checks.append(Check("ERROR", "EMBED_BACKEND", "生产环境禁止 hash 应急向量"))
    if backend == "local":
        model = env.get("EMBED_MODEL", "BAAI/bge-small-zh-v1.5").strip()
        result(not _placeholder(model), "EMBED_MODEL",
               "本地 embedding 模型已配置", "模型名不能为空或占位值")
    elif backend == "api":
        embed_url = env.get("EMBED_BASE_URL", "").strip()
        result(not _placeholder(env.get("EMBED_MODEL", "")), "EMBED_MODEL",
               "API embedding 模型已配置", "API 模式必须配置模型")
        result(bool(embed_url) and urlparse(embed_url).scheme in {"http", "https"},
               "EMBED_BASE_URL", "API URL 格式有效", "API 模式必须配置 http(s) URL")
        if server_mode and embed_url:
            result(not _host_is_local(embed_url), "EMBED_BASE_URL host",
                   "未指向服务器自身", "服务器模式不能使用 localhost/127.0.0.1")
        result(not _placeholder(env.get("EMBED_API_KEY", "") or llm_key),
               "EMBED_API_KEY", "embedding 密钥可用", "API 模式缺少 embedding 密钥")

    smtp_host = env.get("SMTP_HOST", "").strip()
    if production:
        result(not _placeholder(smtp_host), "SMTP_HOST", "SMTP 已启用",
               "生产环境禁止验证码控制台模式或占位配置")
    elif not smtp_host:
        checks.append(Check("WARN", "SMTP_HOST", "未配置，验证码会写入日志"))
    if smtp_host:
        try:
            port_ok = 1 <= int(env.get("SMTP_PORT", "465")) <= 65535
        except ValueError:
            port_ok = False
        result(port_ok, "SMTP_PORT", "SMTP 端口有效", "SMTP 端口非法")
        sender = env.get("SMTP_FROM", "").strip() or env.get("SMTP_USER", "").strip()
        result(not _placeholder(sender), "SMTP_FROM", "发件地址可用",
               "必须配置非占位的 SMTP_FROM 或 SMTP_USER")
        if env.get("SMTP_USER", "").strip():
            result(not _placeholder(env.get("SMTP_PASSWORD", "")), "SMTP_PASSWORD",
                   "SMTP 授权码已配置", "配置 SMTP_USER 时必须配置授权码")

    if server_mode:
        image_tag = env.get("IMAGE_TAG", "").strip()
        result(not _placeholder(image_tag), "IMAGE_TAG",
               "镜像版本标签已配置", "必须使用发布 commit SHA 或明确版本号")
        project_name = env.get("COMPOSE_PROJECT_NAME", "").strip()
        result(not _placeholder(project_name), "COMPOSE_PROJECT_NAME",
               "Compose 项目名已配置", "必须配置独立项目名")
        try:
            port_ok = 1 <= int(env.get("APP_PORT", "")) <= 65535
        except ValueError:
            port_ok = False
        result(port_ok, "APP_PORT", "监听端口有效", "必须配置合法的本机监听端口")

    kb_dir = root / "knowledge_base"
    docs = sorted(kb_dir.rglob("*.md")) if kb_dir.is_dir() else []
    real_docs = [p for p in docs if "示例" not in p.name]
    result(len(real_docs) >= min_docs, "knowledge_base",
           f"发现 {len(real_docs)} 篇非示例文档",
           f"只有 {len(real_docs)} 篇非示例文档，发布门槛为 {min_docs}")

    lock_path = root / "requirements.lock"
    lock_text = lock_path.read_text(encoding="utf-8") if lock_path.is_file() else ""
    lock_ok = "autogenerated by pip-compile" in lock_text and "# WARNING:" not in lock_text
    result(lock_ok, "requirements.lock", "依赖锁文件存在且无生成警告",
           "缺少有效锁文件，或锁文件仍包含生成警告")
    return checks


def validate_env_permissions(path: Path, mode: str) -> Check:
    permissions = stat.S_IMODE(path.stat().st_mode)
    private = permissions & 0o077 == 0
    if private:
        return Check("OK", ".env permissions", f"权限为 {permissions:o}")
    level = "ERROR" if mode == "production" else "WARN"
    return Check(level, ".env permissions",
                 f"当前权限 {permissions:o}；服务器上应执行 chmod 600 {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local", "staging", "production"),
                        default="production")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--min-docs", type=int, default=1)
    args = parser.parse_args()

    if not args.env_file.is_file():
        print(f"[ERROR] env-file: 文件不存在：{args.env_file}")
        return 1
    try:
        env = load_dotenv_file(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"[ERROR] env-file: {exc}")
        return 1

    checks = validate(env, args.root.resolve(), args.mode, max(0, args.min_docs))
    checks.append(validate_env_permissions(args.env_file, args.mode))
    for check in checks:
        print(f"[{check.level}] {check.name}: {check.message}")
    errors = sum(c.level == "ERROR" for c in checks)
    warnings = sum(c.level == "WARN" for c in checks)
    print(f"\n预检完成：{errors} 个阻断项，{warnings} 个警告")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
