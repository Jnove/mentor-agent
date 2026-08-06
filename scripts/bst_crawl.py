"""百事通离线快照采集：全量枚举常见问题/办事指南/规范性文件 → KB 暂存区。

s.zju.edu.cn 的搜索接口无登录可访问（服务端渲染 HTML），空关键词 + 分页即可
枚举全量；本脚本与 scripts/kb_crawl.py 共用暂存区约定（data/kb_staging/、
_manifest.json）和 schema v2，审核/发布/入库链路完全一致。
来源条目登记在 scripts/sources.yaml（crawler: bst 标记，kb_crawl --all 跳过）。

用法:
    python scripts/bst_crawl.py --all --dry-run     # 预演，不写暂存区
    python scripts/bst_crawl.py --source bst-faq    # 只抓常见问题
    python scripts/bst_crawl.py --all --limit 5     # 每类限抓 5 条（调试）
    python scripts/bst_crawl.py --all --skip-pdf    # 规范性文件不抽 PDF 正文
    python scripts/bst_crawl.py --all --workers 16  # 提高并发（默认 8）

输出:
    data/kb_staging/<source_name>/<target_dir>/<title>_浙江大学_<年>.md
    data/kb_staging/<source_name>/_manifest.json

原则与 kb_crawl.py 一致：只做机械性抓取与元数据，无法确定的字段一律 unknown /
未明确；正文保留原文结构。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import frontmatter
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import bst
from core.kb_schema import make_doc_id, validate_metadata
from kb_crawl import STAGING_DIR, build_body, build_filename, extract_pdf_text, make_meta

ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = Path(__file__).resolve().parent / "sources.yaml"

# 展示字段（办事指南）→ 正文键值对保留顺序
_WG_FIELD_ORDER = ("受理时间", "咨询电话", "监督电话", "受理机构", "受理地方", "办理时限", "办理方式")


def load_bst_sources() -> list[dict]:
    with open(SOURCES_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return [s for s in data.get("sources", []) if s.get("enabled") and s.get("crawler") == "bst"]


def _short_title(text: str, limit: int = 120) -> str:
    """标题过长时截断（文件名长度保护），保留开头。"""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _faq_body(item: dict) -> str:
    parts = [item["answer"] or "（答案为空）"]
    meta_lines = []
    if item.get("phone"):
        meta_lines.append(f"咨询电话：{item['phone']}")
    if item.get("dept"):
        meta_lines.append(f"受理部门：{item['dept']}")
    if item.get("office"):
        meta_lines.append(f"科室：{item['office']}")
    if meta_lines:
        parts.append("\n".join(meta_lines))
    return "\n\n".join(parts)


def _work_guide_body(item: dict) -> str:
    fields = item.get("fields", {})
    rows = [(k, fields[k]) for k in _WG_FIELD_ORDER if fields.get(k)]
    for k, v in sorted(fields.items()):
        if k not in _WG_FIELD_ORDER and v:
            rows.append((k, v))
    if not rows:
        return item.get("phone") or ""
    table = "| 项目 | 内容 |\n| --- | --- |\n" + "\n".join(
        f"| {k} | {v.replace('|', '\\|')} |" for k, v in rows)
    return table


def _norm_file_body(item: dict, pdf_text: str | None, meta: dict) -> str:
    head = []
    if meta.get("拟文部门"):
        head.append(f"拟文部门：{meta['拟文部门']}")
    if item.get("issue_no"):
        head.append(f"发文号：{item['issue_no']}")
    if item.get("effective_date"):
        head.append(f"施行日期：{item['effective_date']}")
    if meta.get("时效性"):
        head.append(f"时效性：{meta['时效性']}")
    lines = []
    if head:
        lines.append("> " + "；".join(head))
    if pdf_text:
        lines.append(pdf_text)
    else:
        lines.append(f"[附件：{_short_title(item['title'])}（PDF）]({item['download_url']})")
        lines.append("> 本文件正文为 PDF 附件，自动抽取失败，请打开原文查看。")
    return "\n\n".join(lines)


def _faq_hit(item: dict, source: dict) -> dict:
    url = f"{bst.BASE_URL}/search/faq.do?words=" + urllib.parse.quote(item["question"])
    title = _short_title(item["question"])
    return {
        "url": url, "title": title,
        "body": _faq_body(item), "date": "unknown",
        "tags": list(source.get("defaults", {}).get("tags", [])),
    }


def _wg_hit(item: dict, source: dict) -> dict:
    url = f"{bst.BASE_URL}/search/workGuide.do?words=" + urllib.parse.quote(item["title"])
    return {
        "url": url, "title": _short_title(item["title"]),
        "body": _work_guide_body(item), "date": "unknown",
        "tags": list(source.get("defaults", {}).get("tags", [])),
    }


def _nf_hit(item: dict, source: dict) -> dict:
    return {
        "url": item["view_url"], "title": _short_title(item["title"]),
        "body": None, "date": "unknown",
        "tags": list(source.get("defaults", {}).get("tags", [])),
        "item": item,
    }


def _hit_richness(h: dict) -> int:
    """同名条目内容完整度（字段越多越全），合并时取最全者。"""
    if h.get("item"):
        return len(h["item"].get("fields", {})) if h["item"].get("fields") is not None else 1
    return len(str(h.get("body") or ""))


def _parse_page(kind: str, html: bytes, source: dict) -> list[dict]:
    """一页 HTML → 该来源的条目列表（按来源类型分派）。"""
    text = html.decode("utf-8", errors="replace")
    if kind == "faq":
        return [_faq_hit(it, source) for it in bst.parse_faq_page(text)]
    if kind == "workGuide":
        return [_wg_hit(it, source) for it in bst.parse_work_guide_page(text)]
    return [_nf_hit(it, source) for it in bst.parse_norm_file_page(text)]


def _enumerate(source: dict, limit: int, workers: int) -> list[dict]:
    """空关键词 + 分页并发枚举一个来源，返回 [{url, title, body, date, tags, item?}]。

    实测学校站点对密集/并发请求无感（40 并发 2.1s 全过），并发翻页即可；
    单页失败重试 3 次（代理链路 Clash 偶发挂起是真实风险，非站点限流）。
    站点缓存分层导致同一事项在不同请求下带/不带「点击详情」链接（标题噪声），
    同名条目合并取内容最全者——枚举两轮取并集，结果不受单轮运气影响。
    """
    kind = {"bst-faq": "faq", "bst-work-guide": "workGuide", "bst-norm-file": "normFile"}[source["name"]]
    first = bst._page(kind, "", page=1, size=bst.PAGE_SIZE)
    if first is None:
        print(f"  [错误] 第 1 页请求失败，中止")
        return []
    total = bst.parse_total(first.decode("utf-8", errors="replace"))
    pages = (total + bst.PAGE_SIZE - 1) // bst.PAGE_SIZE if total else 1
    if limit:
        pages = min(pages, max(1, (limit + bst.PAGE_SIZE - 1) // bst.PAGE_SIZE))

    def fetch_page(p: int) -> tuple[int, bytes | None]:
        for attempt in range(3):
            html = bst._page(kind, "", page=p, size=bst.PAGE_SIZE)
            if html is not None:
                return p, html
            print(f"  [重试 {attempt + 1}/3] 第 {p} 页请求失败")
            time.sleep(5)
        return p, None

    merged: dict[str, dict] = {}  # title -> 最全的条目
    for round_no in range(2):
        by_page: dict[int, bytes] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(fetch_page, p): p for p in range(1, pages + 1)}
            for fut in as_completed(futs):
                p, html = fut.result()
                if html is not None:
                    by_page[p] = html
        missing = [p for p in range(1, pages + 1) if p not in by_page]
        if missing:
            print(f"  [警告] 第 {round_no + 1} 轮 {len(missing)} 页失败：{sorted(missing)}")
        for p in sorted(by_page):
            for h in _parse_page(kind, by_page[p], source):
                # 合并键：规范性文件按 doc_no（url 唯一，同名不同文件各自保留）；
                # FAQ/办事指南按标题（同事项不同缓存版本合并取最全）
                key = h["url"] if h.get("item") else h["title"]
                old = merged.get(key)
                if old is None:
                    merged[key] = h
                elif _hit_richness(h) > _hit_richness(old):
                    merged[key] = h

    hits = list(merged.values())
    if limit:
        hits = hits[:limit]
    print(f"  枚举 {len(hits)} 条（共 {pages} 页 × 2 轮，同名已合并）")
    return hits


def _norm_pdf_text(item: dict) -> tuple[str | None, dict]:
    """下载规范性文件 PDF 抽正文，返回 (正文, 信息页元数据)。

    实测并发下载偶发瞬时失败（~30%），下载环节重试 3 次；扫描件抽取为空时
    返回 None（调用方落占位 + 下载链接，不阻塞整条）。
    """
    detail = bst.parse_norm_file_detail(
        (bst.fetch(item["view_url"]) or b"").decode("utf-8", errors="replace"))
    raw = None
    for attempt in range(3):
        raw = bst.fetch(bst.norm_pdf_url(item["doc_no"]), timeout=60)
        if raw is not None:
            break
        time.sleep(2)
    if not raw:
        return None, detail
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        return (text.strip() or None), detail
    except Exception:
        return None, detail


def _load_staging_index(staging: Path) -> dict[str, dict]:
    """暂存区已有文档的 doc_id -> {rel, body}（断点续传：中断后重跑不重复下载）。"""
    index: dict[str, dict] = {}
    for path in staging.rglob("*.md"):
        try:
            post = frontmatter.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        did = str(post.get("doc_id", ""))
        if did:
            index[did] = {"rel": path.relative_to(staging).as_posix(),
                          "body": post.content}
    return index


def crawl_source(source: dict, limit: int, dry_run: bool, skip_pdf: bool, workers: int) -> dict:
    staging = STAGING_DIR / source["name"]
    staging.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    errors: list[str] = []
    existing = _load_staging_index(staging)  # doc_id -> {rel, body}
    hits = _enumerate(source, limit, workers)
    print(f"  枚举 {len(hits)} 条")

    if source["name"] == "bst-norm-file" and not skip_pdf and not dry_run:
        # 预演（--dry-run）不下载 PDF：只验证枚举与文档生成，不烧 700+ 次 PDF 请求。
        # 断点续传：已生成的条目（含 PDF 正文，非占位）跳过下载，直接沿用现有正文。
        done = skipped = 0
        todo = []
        for h in hits:
            old = existing.get(make_doc_id(h["title"], h["url"]))
            if old and "[附件：" not in old["body"] and "自动抽取失败" not in old["body"]:
                h["body"] = old["body"]
                skipped += 1
            else:
                todo.append(h)
        print(f"  复用已有正文 {skipped} 条，需下载 {len(todo)} 条")
        with ThreadPoolExecutor(max_workers=max(2, workers)) as pool:
            futs = {pool.submit(_norm_pdf_text, h["item"]): h for h in todo}
            for fut in as_completed(futs):
                h = futs[fut]
                done += 1
                try:
                    pdf_text, meta = fut.result()
                except Exception as exc:
                    pdf_text, meta, errors = None, {}, errors + [f"{h['title']}: PDF 失败 {exc}"]
                if done % 100 == 0 or done == len(todo):
                    print(f"  PDF {done}/{len(todo)}")
                h["body"] = _norm_file_body(h["item"], pdf_text, meta)
    elif source["name"] == "bst-norm-file":
        for h in hits:
            h["body"] = _norm_file_body(h["item"], None, {})
    for h in hits:
        meta = make_meta(source, h["title"], h["url"], h["date"])
        meta["tags"] = list(h["tags"])
        body = build_body(h["title"], h["body"], source, h["date"])
        result = validate_metadata(meta)
        rel = f"{source['target_dir']}/{build_filename(meta)}"
        if result.errors:
            errors.append(f"{h['url']}: schema 校验失败：{'；'.join(result.errors)}")
            continue
        old = existing.get(meta["doc_id"])
        status = "一致" if (old and old["body"] == body) else ("新增" if not old else "更新")
        results[rel] = {
            "status": status, "doc_id": meta["doc_id"], "title": h["title"],
            "publish_date": h["date"], "chars": len(body),
            "existing": old["rel"] if old else None,
        }
        if not dry_run:
            (staging / rel).parent.mkdir(parents=True, exist_ok=True)
            post = frontmatter.Post(body, **meta)
            (staging / rel).write_text(frontmatter.dumps(post), encoding="utf-8")

    if not dry_run and results:
        # 清理本次未出现的旧暂存文件（应对站点缓存分层导致的标题版本漂移——
        # 同一事项不同轮抓到带/不带「点击详情」的版本，会生成不同文件名，需清理旧版）。
        kept = {Path(r).as_posix() for r in results} | {"_manifest.json"}
        for old in staging.rglob("*.md"):
            if old.relative_to(staging).as_posix() not in kept:
                old.unlink()
        manifest = {
            "source": source["name"],
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "results": results, "errors": errors,
        }
        (staging / "_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = [f"== {source['name']}（{'预演' if dry_run else '抓取'}）: {len(results)} 篇"]
    for e in errors[:10]:
        summary.append(f"  [错误] {e}")
    if len(errors) > 10:
        summary.append(f"  … 共 {len(errors)} 条错误")
    return {"results": results, "errors": errors, "summary": "\n".join(summary)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", help="只抓指定来源（sources.yaml 里 crawler: bst 的 name）")
    ap.add_argument("--all", action="store_true", help="抓全部 bst 来源")
    ap.add_argument("--dry-run", action="store_true", help="预演，不写暂存区")
    ap.add_argument("--limit", type=int, default=0, help="每来源最多抓多少条（调试用）")
    ap.add_argument("--skip-pdf", action="store_true", help="规范性文件不下载 PDF 正文")
    ap.add_argument("--workers", type=int, default=8,
                    help="并发请求数（实测 40 并发全秒回；单页失败自动重试 3 次，"
                         "真正风险是代理链路间歇挂起，不是站点限流）")
    args = ap.parse_args()

    sources = load_bst_sources()
    if args.source:
        sources = [s for s in sources if s["name"] == args.source]
    elif not args.all:
        ap.error("必须指定 --source <name> 或 --all")

    for source in sources:
        print(crawl_source(source, args.limit, args.dry_run, args.skip_pdf, args.workers)["summary"])


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):  # Windows 控制台默认 GBK，中文会乱码
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    main()
