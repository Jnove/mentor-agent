"""探索浙大/学院站点结构的一次性脚本（connect-zju-source skill 配套）。

用法：
    python .claude/skills/connect-zju-source/references/explore-site.py \
        --url http://office.ckc.zju.edu.cn/ \
        --columns 34975 54293 35002
    # --columns：可选，对给定栏目号输出详情 URL 样本、分页、PDF iframe 检测
    # 此url仅为举例，具体使用请根据实际更换

产出（stdout）：
    1. 站点导航 list.htm / list.psp 栏目清单
    2. 每个栏目的：文章链接数、详情 URL 模式、是否分页、正文容器、PDF 嵌入情况

前提：venv 已激活（如果本地有虚拟环境）。
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from urllib.parse import urljoin

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from playwright.sync_api import sync_playwright

NAV_RE = re.compile(r"list\.(htm|psp)")
DETAIL_RE = re.compile(r"/\d{4}/\d{4}/c\d+a\d+/page\.htm$")
FILE_RE = re.compile(r"\.(docx?|pdf|xlsx?|pptx?|zip|rar)(\?|#|$)", re.I)


def explore(page, url: str) -> None:
    page.goto(url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    print(f"\n########## {url}")
    print("标题:", page.title())
    links = page.eval_on_selector_all(
        "a",
        "els => els.map(e => ({t:(e.innerText||'').trim().replace(/\\s+/g,' '), h:e.href}))"
        ".filter(x => x.t)",
    )
    seen, nav = set(), []
    for l in links:
        if NAV_RE.search(l["h"]) and l["h"] not in seen:
            seen.add(l["h"])
            nav.append(l)
    print(f"导航栏目 {len(nav)} 个：")
    for l in nav[:50]:
        print("  ", l["t"], "|", l["h"])


def inspect_column(page, base: str, colid: str) -> None:
    url = urljoin(base, f"{colid}/list.htm")
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
    except Exception as exc:
        print(f"  栏目 {colid} 打开失败：{exc}")
        return
    page.wait_for_timeout(1800)
    links = page.eval_on_selector_all("a", "els => els.map(e => e.href || '')")
    details = [h for h in links if DETAIL_RE.search(h)]
    print(f"\n=== 栏目 {colid}：详情链接 {len(details)} 个")
    for h in list(dict.fromkeys(details))[:6]:
        print("     ", h)

    # 分页
    pg = page.eval_on_selector_all("a", "els => els.map(e => e.href || '')")
    has_pg = any("list2" in h or "list.htm?page=2" in h for h in pg)
    print("   分页(list2.htm):", has_pg)

    # 抽一篇详情检查正文容器 + PDF iframe
    if details:
        sample = next(h for h in details if "office" in h or "www" in h or base in h)
        try:
            page.goto(sample, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
        except Exception as exc:
            print(f"   详情页打开失败：{exc}")
            return
        sel_ok = page.query_selector(".wp_articlecontent") is not None
        frames = page.eval_on_selector_all("iframe", "els => els.map(e => e.src)")
        pdf_frames = [f for f in frames if "pdf" in f.lower()]
        files = page.eval_on_selector_all("a", "els => els.map(e => e.href || '')")
        file_links = [h for h in files if FILE_RE.search(h)]
        print(f"   .wp_articlecontent: {sel_ok} | PDF iframe: {len(pdf_frames)} | 附件链接: {len(file_links)}")
        if pdf_frames:
            print("   样例 PDF:", pdf_frames[0][:120])
        if not sel_ok and not pdf_frames:
            print("   ⚠ 非 WebPlus 容器，需手动找正文选择器（详情页 inspect DOM）")


def main() -> None:
    ap = argparse.ArgumentParser(description="探索站点结构")
    ap.add_argument("--url", required=True, help="站点首页 URL")
    ap.add_argument("--columns", nargs="*", default=[], help="要细查的栏目号列表")
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        explore(page, args.url)
        for col in args.columns:
            inspect_column(page, args.url, col)
        browser.close()


if __name__ == "__main__":
    main()
