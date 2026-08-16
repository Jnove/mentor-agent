# -*- coding: utf-8 -*-
"""快速形式审查 needs_review 文档：扫描 -> 批量 verified。

用法：
  python scripts/verify_needs_review.py            # 统计 + 列出 needs_review
  python scripts/verify_needs_review.py --scope 通知/化学系
  python scripts/verify_needs_review.py --apply --scope 通知/化学系
  python scripts/verify_needs_review.py --bad data/nr_bad.txt
"""
import argparse
import io
import os
import re
import sys

KB = "knowledge_base"
REPLACEMENT = "�"


def collect(scope=None):
    rows = []
    for r, _, fs in os.walk(KB):
        rn = r.replace(os.sep, "/")
        if "/staging/" in rn:
            continue
        for f in fs:
            if not f.endswith(".md"):
                continue
            p = os.path.join(r, f)
            rel = rn[len(KB) + 1:] + "/" + f
            if scope and not rel.startswith(scope):
                continue
            try:
                txt = io.open(p, encoding="utf-8").read()
            except Exception:
                continue
            rs = re.search(r"review_status: (\w+)", txt)
            if not rs or rs.group(1) != "needs_review":
                continue
            rows.append((rel, txt))
    return rows


def set_status(txt, key, value):
    return re.sub(r"(?m)^%s:.*$" % key, "%s: %s" % (key, value), txt, count=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="", help="只处理该前缀路径，如 通知/化学系")
    ap.add_argument("--apply", action="store_true", help="批量把 review_status 改为 verified")
    ap.add_argument("--bad", default="", help="文件含坏文档相对路径，标记 valid:false")
    ap.add_argument("--ok", default="", help="文件含通过文档相对路径，标记 review_status verified")
    ap.add_argument("--fill-verified", action="store_true",
                    help="补齐所有 verified 但缺 last_checked_at/maintainer 的文档")
    args = ap.parse_args()

    if args.fill_verified:
        from datetime import date
        today = date.today().isoformat()
        n = 0
        for r, _, fs in os.walk(KB):
            rn = r.replace(os.sep, "/")
            if "/staging/" in rn:
                continue
            for f in fs:
                if not f.endswith(".md"):
                    continue
                p = os.path.join(r, f)
                try:
                    txt = io.open(p, encoding="utf-8").read()
                except Exception:
                    continue
                if "review_status: verified" not in txt:
                    continue
                need = False
                new = txt
                if "last_checked_at: null" in new or "last_checked_at:" not in new.split("\n---\n")[0]:
                    new = re.sub(r"(?m)^last_checked_at:.*$", "last_checked_at: '%s'" % today, new, count=1)
                    need = True
                if "maintainer: unassigned" in new or "maintainer:" not in new.split("\n---\n")[0]:
                    new = re.sub(r"(?m)^maintainer:.*$", "maintainer: 学长组", new, count=1)
                    need = True
                if need:
                    io.open(p, "w", encoding="utf-8", newline="").write(new)
                    n += 1
        print("补齐 last_checked_at/maintainer 共 %d 篇" % n)
        return

    if args.ok:
        paths = [l.strip() for l in io.open(args.ok, encoding="utf-8").read().splitlines() if l.strip()]
        n = 0
        for rel in paths:
            p = os.path.join(KB, *rel.split("/"))
            if not os.path.exists(p):
                print("  [缺失] " + rel)
                continue
            txt = io.open(p, encoding="utf-8").read()
            if "review_status: verified" in txt:
                continue
            io.open(p, "w", encoding="utf-8", newline="").write(set_status(txt, "review_status", "verified"))
            n += 1
        print("已 verified %d 篇" % n)
        return

    if args.bad:
        paths = [l.strip() for l in io.open(args.bad, encoding="utf-8").read().splitlines() if l.strip()]
        n = 0
        for rel in paths:
            p = os.path.join(KB, *rel.split("/"))
            if not os.path.exists(p):
                print("  [缺失] " + rel)
                continue
            txt = io.open(p, encoding="utf-8").read()
            if "valid: false" in txt:
                continue
            io.open(p, "w", encoding="utf-8", newline="").write(set_status(txt, "valid", "false"))
            n += 1
        print("标记 valid:false 共 %d 篇" % n)
        return

    rows = collect(args.scope)
    print("needs_review%s: %d 篇" % (" [%s]" % args.scope if args.scope else "", len(rows)))
    if args.apply:
        n = 0
        for rel, txt in rows:
            p = os.path.join(KB, *rel.split("/"))
            io.open(p, "w", encoding="utf-8", newline="").write(set_status(txt, "review_status", "verified"))
            n += 1
        print("已 verified %d 篇" % n)
    else:
        for rel, _ in rows[:200]:
            print("  " + rel)


if __name__ == "__main__":
    main()
