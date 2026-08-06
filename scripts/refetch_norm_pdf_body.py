"""重新抽取百事通规范性文件 PDF 正文，替换 knowledge_base 中的乱码版本。

背景：bst_crawl 采集时用 pypdf 抽取 PDF，部分 PDF 的字体 CMap 把 GBK 字节
映射到韩文码位（如 훐릲헣붭 = 中共浙江），pypdf/pdfplumber 都无法正确解码。
早期"韩文反转"修复被证实不可靠：PDF 的 CMap 映射不遵循 EUC-KR 编码规则，
反转会引入错误汉字（如"妇藕泣都"），已废弃。

正确做法：重新用 pypdf 抽取（与采集器同一路径），只做白名单清洗删除乱码
字符、不做任何反转。乱码集中在文头单位行（2-3 行），正文完整可读；
来源机构信息保留在 frontmatter 的拟文部门/发文号字段里。

用法: python scripts/refetch_norm_pdf_body.py [--apply] [--workers 8]
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import frontmatter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import bst  # noqa: E402
from core.config import KB_DIR  # noqa: E402
from scripts.fix_bst_norm_mojibake import _OK  # noqa: E402

TARGET = KB_DIR / "政策" / "百事通"


def extract_pypdf(raw: bytes) -> str | None:
    """与 bst_crawl._norm_pdf_text 相同的 pypdf 抽取路径。"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        return text.strip() or None
    except Exception:
        return None


def clean_no_reverse(text: str) -> tuple[str, int]:
    """白名单清洗：删除乱码字符，不做韩文反转。返回 (文本, 删除数)。"""
    keep = [c for c in text if _OK.match(c) or c in "\r\n\t"]
    return "".join(keep), len(text) - len(keep)


def refetch_one(p: Path) -> tuple[Path, dict]:
    post = frontmatter.loads(p.read_text(encoding="utf-8"))
    m = re.search(r"docNo=(\d+)", str(post.metadata.get("source_url", "")))
    if not m:
        return p, {"err": "no-docno"}
    raw = bst.fetch(bst.norm_pdf_url(m.group(1)), timeout=60)
    if not raw:
        return p, {"err": "download-fail"}
    text = extract_pypdf(raw)
    if not text:
        return p, {"err": "extract-fail"}
    cleaned, deleted = clean_no_reverse(text)
    old = post.content
    old_bad = sum(1 for c in old if not _OK.match(c) and c not in "\r\n\t")
    return p, {
        "old_len": len(old), "new_len": len(cleaned),
        "old_bad": old_bad, "new_bad": 0, "deleted": deleted,
        "body": cleaned,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="写回知识库文件（默认只报告）")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    files = sorted(TARGET.rglob("*.md"))
    report: list[tuple[Path, dict]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(refetch_one, p): p for p in files}
        for fut in as_completed(futs):
            p, info = fut.result()
            if "err" in info:
                print(f"  [跳过] {p.name[:36]} {info['err']}")
                continue
            info["path"] = p
            report.append((p, info))

    n_bad = sum(1 for _, i in report if i["old_bad"])
    n_shorter = sum(1 for _, i in report if i["new_len"] < i["old_len"] * 0.8)
    total_deleted = sum(i["deleted"] for _, i in report)
    print(f"\n重新抽取 {len(report)}/{len(files)} 篇：原含乱码 {n_bad} 篇，"
          f"重抽后疑似丢内容(短于原 80%) {n_shorter} 篇，共删除乱码 {total_deleted} 字")
    for p, i in report:
        flag = "⚠" if i["new_len"] < i["old_len"] * 0.8 else "✓"
        print(f"  [{flag}] {p.name[:34]} 旧 {i['old_len']}字/乱{i['old_bad']} "
              f"→ 新 {i['new_len']}字/删{i['deleted']}")

    if not args.apply:
        print("\n（--apply 写回知识库）")
        return 0
    for p, i in report:
        if i["new_len"] < i["old_len"] * 0.8:
            print(f"  [保留旧版] {p.name[:36]}（重抽疑似丢内容，不覆盖）")
            continue
        post = frontmatter.loads(p.read_text(encoding="utf-8"))
        post.content = i["body"]
        p.write_text(frontmatter.dumps(post), encoding="utf-8", newline="\n")
    print(f"已写回 {len(report)} 篇。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(main())
