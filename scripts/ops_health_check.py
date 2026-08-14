"""检查本机 Streamlit 健康端点；失败时返回非零供 systemd 记录和告警。"""
from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence


class HealthCheckError(RuntimeError):
    """健康端点不可用或返回内容不符合预期。"""


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def validate_health_url(value: str) -> str:
    value = value.strip()
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("健康检查 URL 必须是完整的 http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("健康检查 URL 禁止包含认证信息")
    if (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
        raise ValueError("同机健康检查只允许 localhost/127.0.0.1/::1")
    if parsed.fragment:
        raise ValueError("健康检查 URL 禁止包含 fragment")
    return value


def check_health(
    url: str,
    *,
    timeout: float = 5.0,
    expected: str = "ok",
    opener: urllib.request.OpenerDirector | None = None,
) -> tuple[int, float]:
    """返回 ``(status, elapsed_seconds)``；重定向、非 200 或内容异常会失败。"""
    url = validate_health_url(url)
    if not 0 < timeout <= 60:
        raise ValueError("timeout 必须大于 0 且不超过 60 秒")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "mentor-agent-local-health/1.0"},
    )
    # 同机探测不得继承 HTTP(S)_PROXY；否则代理可掩盖本机故障或把请求送出服务器。
    client = opener or urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
    )
    started = time.monotonic()
    try:
        with client.open(request, timeout=timeout) as response:
            status = response.status
            body = response.read(4096).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise HealthCheckError(f"请求失败：{exc}") from exc
    elapsed = time.monotonic() - started
    if status != 200:
        raise HealthCheckError(f"健康端点返回 HTTP {status}")
    if expected and expected.casefold() not in body.casefold():
        raise HealthCheckError(f"响应中未发现预期文本 {expected!r}")
    return status, elapsed


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=_env(
            "MENTOR_HEALTH_URL",
            "http://127.0.0.1:8501/_stcore/health",
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_env("MENTOR_HEALTH_TIMEOUT", "5"),
    )
    parser.add_argument(
        "--expected",
        default=_env("MENTOR_HEALTH_EXPECT", "ok"),
        help="留空表示只检查 HTTP 200",
    )
    args = parser.parse_args(argv)
    try:
        status, elapsed = check_health(
            args.url,
            timeout=args.timeout,
            expected=args.expected,
        )
    except (OSError, ValueError, HealthCheckError) as exc:
        print(f"[ERROR] local-health: {exc}", file=sys.stderr)
        return 1
    print(f"[OK] local-health: HTTP {status} elapsed={elapsed:.3f}s url={args.url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
