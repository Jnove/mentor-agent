# -*- coding: utf-8 -*-
"""把 spa 审核 keep 的篇目从 data/kb_staging 发布到 knowledge_base。

逐字节复制 + 只改 frontmatter：
  review_status: needs_review -> verified
  last_checked_at: null -> '2026-08-15'
  maintainer: unassigned -> 学长组
  category: 按目标目录覆盖（政策/通知），改分类文件据 | 分隔的目标路径
目标 = staging 相对路径去掉首个 spa-<来源>/ 段。
用法：python scripts/spa_promote.py [--dry-run]
"""
import io
import json
import os
import re
import shutil
import sys

STAGING = "data/kb_staging"
KB = "knowledge_base"
AUDIT_DIR = os.path.join(STAGING, "audit")
FILES = [f"spa_agent{c}_keep.txt" for c in "ABCD"]
OUT_JSON = os.path.join(STAGING, "_promote_spa_final.json")
DRY = "--dry-run" in sys.argv


def read_keep(fname):
    p = os.path.join(AUDIT_DIR, fname)
    if not os.path.exists(p):
        return []
    return [l.strip() for l in io.open(p, encoding="utf-8").read().splitlines() if l.strip()]


def split_fix(line):
    if "|" in line:
        src, dst = line.split("|", 1)
        return src, dst, True
    return line, line, False


def clean_zw(s):
    # 抓取标题可能带零宽空格（U+200B 等），污染 KB 目标文件名；src 保持原样以命中 staging
    return "".join(c for c in s if ord(c) not in (0x200B, 0x200C, 0x200D, 0xFEFF))


def kb_target(staging_rel):
    parts = staging_rel.split("/")
    # src 以 spa-<来源>/ 开头；重分类 dst 可能只写 KB 相对路径（如 政策/公共管理学院/招生）
    if parts[0].startswith("spa-"):
        parts = parts[1:]
    assert len(parts) > 1, staging_rel
    return "/".join(parts)


def update_frontmatter(text, category):
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    assert m, "frontmatter not found"
    fm = m.group(1)

    def repl(line):
        if line.startswith("review_status:"):
            return "review_status: verified"
        if line.startswith("last_checked_at:"):
            return "last_checked_at: '2026-08-15'"
        if line.startswith("maintainer:"):
            return "maintainer: 学长组"
        if line.startswith("category:"):
            return f"category: {category}"
        return line

    new_fm = "\n".join(repl(l) for l in fm.split("\n"))
    return text[: m.start(1)] + new_fm + text[m.end(1):]


def main():
    entries = []
    seen = {}
    for fname in FILES:
        for line in read_keep(fname):
            src_rel, dst_rel, is_fix = split_fix(line)
            dst_rel = clean_zw(dst_rel)
            if is_fix and not dst_rel.lower().endswith(".md"):
                # 重分类目标可只写目录（如 政策/公共管理学院/招生），自动补源文件名
                dst_rel = dst_rel.rstrip("/") + "/" + src_rel.rsplit("/", 1)[-1]
            kb_rel = kb_target(dst_rel)
            category = kb_rel.split("/")[0]
            assert category in ("政策", "通知"), (kb_rel, category)
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
            if DRY:
                print(("  [fix] " if is_fix else "  [keep] ") + f"{src_rel} -> {kb_rel}")
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src_path, dst)
            text = io.open(dst, encoding="utf-8").read()
            new = update_frontmatter(text, category)
            io.open(dst, "w", encoding="utf-8", newline="").write(new)
    print(f"--- 共 {len(entries)} 篇（{sum(1 for e in entries if e['fix'])} 改分类 / {sum(1 for e in entries if not e['fix'])} 原样）" + ("（dry-run，未写入）" if DRY else "，已发布"))
    if not DRY:
        json.dump(entries, io.open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
