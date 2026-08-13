"""检查 Caddy 静态证书是否可解析且在告警窗口后仍然有效。"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence


class CertificateCheckError(RuntimeError):
    """证书不可读、不可解析、已过期或即将过期。"""


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _run_openssl(
    args: Sequence[str],
    *,
    runner: CommandRunner,
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(args),
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        raise CertificateCheckError(f"无法执行 {args[0]}：{exc}") from exc


def check_certificate(
    cert_file: Path,
    *,
    warn_days: int = 30,
    openssl: str = "/usr/bin/openssl",
    runner: CommandRunner = subprocess.run,
) -> str:
    """返回证书摘要；不足 ``warn_days`` 或解析失败时抛出异常。"""
    cert_file = cert_file.expanduser().resolve()
    if not cert_file.is_file():
        raise FileNotFoundError(f"证书文件不存在：{cert_file}")
    if not 1 <= warn_days <= 3650:
        raise ValueError("warn-days 必须在 1～3650 之间")
    if not Path(openssl).is_absolute():
        raise ValueError("openssl 必须使用绝对路径")

    details = _run_openssl(
        [
            openssl,
            "x509",
            "-noout",
            "-subject",
            "-issuer",
            "-enddate",
            "-in",
            str(cert_file),
        ],
        runner=runner,
    )
    if details.returncode != 0:
        error = (details.stderr or details.stdout or "未知错误").strip()
        raise CertificateCheckError(f"证书解析失败：{error[-1000:]}")

    check = _run_openssl(
        [
            openssl,
            "x509",
            "-checkend",
            str(warn_days * 24 * 60 * 60),
            "-noout",
            "-in",
            str(cert_file),
        ],
        runner=runner,
    )
    summary = " ".join((details.stdout or "").split())
    if check.returncode != 0:
        raise CertificateCheckError(
            f"证书已过期或将在 {warn_days} 天内过期；{summary or '无证书摘要'}"
        )
    return summary


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cert-file",
        type=Path,
        default=Path(
            _env(
                "MENTOR_CERT_FILE",
                "/etc/caddy/certs/mentor.zjuxlab.com.fullchain.pem",
            )
        ),
    )
    parser.add_argument(
        "--warn-days",
        type=int,
        default=_env("MENTOR_CERT_WARN_DAYS", "30"),
    )
    parser.add_argument(
        "--openssl",
        default=_env("MENTOR_OPENSSL", "/usr/bin/openssl"),
    )
    args = parser.parse_args(argv)
    try:
        summary = check_certificate(
            args.cert_file,
            warn_days=args.warn_days,
            openssl=args.openssl,
        )
    except (OSError, ValueError, CertificateCheckError) as exc:
        print(f"[ERROR] certificate: {exc}", file=sys.stderr)
        return 1
    print(f"[OK] certificate: valid>{args.warn_days}d {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
