# -*- coding: utf-8 -*-
"""通用 promote：把 <site> 审核 keep 的篇目从 data/kb_staging 发布到 knowledge_base。

逐字节复制 + 只改 frontmatter：
  review_status: needs_review -> verified
  last_checked_at: null -> 运行当天
  maintainer: unassigned -> 学长组
  category: 按目标目录覆盖（政策/通知/FAQ），改分类文件据 | 分隔的目标路径
目标 = staging 相对路径去掉首个 <site>-<来源>/ 段。

用法：
  python scripts/promote.py <site> [--categories 2|3|政策,通知,FAQ] [--files <keep名...>] [--dry-run] [--date YYYY-MM-DD] [--maintainer 学长组]
"""
import argparse
import glob
import io
import json
import os
import re
import shutil
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.kb_schema import CATEGORIES

STAGING = "data/kb_staging"
KB = "knowledge_base"
AUDIT_DIR = os.path.join(STAGING, "audit")


def find_keep_files(audit_dir, site):
    """glob audit/<site>_*_keep.txt；_final_keep.txt 优先，排除 _merged_keep.txt 中间件。"""
    cands = sorted(glob.glob(os.path.join(audit_dir, f"{site}_*_keep.txt")))
    finals = [c for c in cands if "_final_keep.txt" in c]
    if finals:
        return finals
    return [c for c in cands if "_merged_keep.txt" not in c]


def read_keep(path):
    if not os.path.exists(path):
        return []
    return [l.strip() for l in io.open(path, encoding="utf-8").read().splitlines() if l.strip()]


def split_fix(line):
    if "|" in line:
        src, dst = line.split("|", 1)
        return src, dst, True
    return line, line, False


def clean_zw(s):
    # 抓取标题可能带零宽空格（U+200B 等），污染 KB 目标文件名；src 保持原样以命中 staging
    return "".join(c for c in s if ord(c) not in (0x200B, 0x200C, 0x200D, 0xFEFF))


def kb_target(rel, site_prefix):
    parts = rel.split("/")
    # src 以 <site>-<来源>/ 开头；重分类 dst 可能只写 KB 相对路径（如 政策/传媒学院/招生）
    if parts[0].startswith(site_prefix):
        parts = parts[1:]
    assert len(parts) > 1, rel
    return "/".join(parts)


def update_frontmatter(text, category, checked_at, maintainer):
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    assert m, "frontmatter not found"
    fm = m.group(1)

    def repl(line):
        if line.startswith("review_status:"):
            return "review_status: verified"
        if line.startswith("last_checked_at:"):
            return f"last_checked_at: '{checked_at}'"
        if line.startswith("maintainer:"):
            return f"maintainer: {maintainer}"
        if line.startswith("category:"):
            return f"category: {category}"
        return line

    new_fm = "\n".join(repl(l) for l in fm.split("\n"))
    return text[: m.start(1)] + new_fm + text[m.end(1):]


def main():
    ap = argparse.ArgumentParser(description="把 <site> 审核 keep 的篇目发布到 knowledge_base")
    ap.add_argument("site", help="站点前缀，如 cmic（用于 glob keep 文件与剥 staging 前缀）")
    ap.add_argument("--categories", default="2",
                    help="分类集合：2=政策,通知；3=加 FAQ；或显式逗号列表（如 政策,通知,FAQ）")
    ap.add_argument("--files", nargs="*", default=None,
                    help="显式指定 keep 文件名（空格分隔）；省略则 glob audit/<site>_*_keep.txt")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写盘")
    ap.add_argument("--date", default=None, help="last_checked_at 日期，默认今天")
    ap.add_argument("--maintainer", default="学长组", help="maintainer 值")
    args = ap.parse_args()

    # 分类集合解析
    if args.categories in ("2", "3"):
        cats = {"政策", "通知"} if args.categories == "2" else {"政策", "通知", "FAQ"}
    else:
        cats = {c.strip() for c in args.categories.split(",") if c.strip()}
    bad = cats - CATEGORIES
    assert not bad, f"非法分类: {sorted(bad)}; 合法集合: {sorted(CATEGORIES)}"

    site = args.site
    site_prefix = f"{site}-"
    checked_at = args.date or date.today().isoformat()
    out_json = os.path.join(STAGING, f"_promote_{site}_final.json")

    if args.files:
        keep_paths = [os.path.join(AUDIT_DIR, f) for f in args.files]
    else:
        keep_paths = find_keep_files(AUDIT_DIR, site)
    if not keep_paths:
        print(f"[!] 未找到 {site} 的 keep 文件（glob audit/{site}_*_keep.txt 或 --files 指定）")
        return

    entries = []
    seen = {}
    skipped = 0
    for path in keep_paths:
        fname = os.path.basename(path)
        for line in read_keep(path):
            src_rel, dst_rel, is_fix = split_fix(line)
            # 行级 site 过滤：src 首段必须带 <site>- 前缀（防御共享/无前缀 keep 文件混入）
            if not src_rel.split("/")[0].startswith(site_prefix):
                skipped += 1
                continue
            dst_rel = clean_zw(dst_rel)
            if is_fix and not dst_rel.lower().endswith(".md"):
                # 重分类目标可只写目录（如 政策/传媒学院/招生），自动补源文件名
                dst_rel = dst_rel.rstrip("/") + "/" + src_rel.rsplit("/", 1)[-1]
            kb_rel = kb_target(dst_rel, site_prefix)
            category = kb_rel.split("/")[0]
            assert category in cats, (kb_rel, category)
            src_path = os.path.join(STAGING, *src_rel.split("/"))
            assert os.path.exists(src_path), f"missing staging: {src_path}"
            if kb_rel in seen:
                print(f"  [目标冲突] {kb_rel}（{seen[kb_rel]} vs {fname}）")
            seen[kb_rel] = fname
            src_first = src_rel.split("/")[1]
            if not is_fix and src_first != category:
                print(f"  [原样目录不符] {fname}: {src_rel} -> {kb_rel}")
            dst = os.path.join(KB, *kb_rel.split("/"))
            entries.append({"name": kb_rel, "src": src_rel, "size": os.path.getsize(src_path), "fix": is_fix})
            if args.dry_run:
                print(("  [fix] " if is_fix else "  [keep] ") + f"{src_rel} -> {kb_rel}")
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src_path, dst)
            text = io.open(dst, encoding="utf-8").read()
            new = update_frontmatter(text, category, checked_at, args.maintainer)
            io.open(dst, "w", encoding="utf-8", newline="").write(new)
    print(f"--- {site} 共 {len(entries)} 篇（{sum(1 for e in entries if e['fix'])} 改分类 / {sum(1 for e in entries if not e['fix'])} 原样）"
          + (f"，跳过 {skipped} 行非 {site} 前缀" if skipped else "")
          + ("（dry-run，未写入）" if args.dry_run else f"，已发布到 {out_json}"))
    if not args.dry_run:
        json.dump(entries, io.open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
