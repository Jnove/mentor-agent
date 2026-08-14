"""部署后无需第三方依赖的 HTTP 冒烟测试。"""
from __future__ import annotations

import argparse
import ssl
import urllib.error
import urllib.parse
import urllib.request


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL 必须是完整的 http(s) URL")
    return value


def fetch(url: str, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "mentor-agent-smoke/1.0"})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        body = response.read(4096).decode("utf-8", errors="replace")
        return response.status, body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--require-https", action="store_true")
    args = parser.parse_args()
    try:
        base = normalize_base_url(args.base_url)
    except ValueError as exc:
        print(f"[ERROR] URL: {exc}")
        return 1
    if args.require_https and not base.startswith("https://"):
        print("[ERROR] 生产冒烟测试必须使用 HTTPS")
        return 1

    targets = (("health", f"{base}/_stcore/health"), ("root", f"{base}/"))
    failed = False
    for name, url in targets:
        try:
            status, body = fetch(url, args.timeout)
            ok = status == 200 and (name != "health" or "ok" in body.lower())
            print(f"[{'OK' if ok else 'ERROR'}] {name}: HTTP {status} {url}")
            failed = failed or not ok
        except (OSError, urllib.error.URLError) as exc:
            print(f"[ERROR] {name}: {url}: {exc}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
