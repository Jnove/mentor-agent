# -*- coding: utf-8 -*-
"""知识库规模统计：来源数 / 入口 md 数 / 块数。

用法：python scripts/kb_stats.py
输出：正式发布文档数（排除 staging）、来源数（source_org 归一去重）、顶层分类分布。
块数（chroma 向量块）不在此统计——运行 python ingest.py 末尾会打印"库中共 N 条"。
"""
import glob
import os
from collections import Counter
import frontmatter

KB = "knowledge_base"


def norm(org):
    s = org.replace("浙江大学", "").strip()
    s = s.replace("党委学生工作部", "学生工作部")
    if s in ("学工部", "学工部（学生工作处）"):
        s = "学生工作部"
    return s


def main():
    all_files = []
    for f in glob.glob(os.path.join(KB, "**", "*.md"), recursive=True):
        all_files.append(f.replace("\\", "/"))

    published = [f for f in all_files if "/staging/" not in f]

    orgs = Counter()
    for f in published:
        try:
            post = frontmatter.load(f)
            org = str(post.get("source_org", "")).strip()
            if org:
                orgs[norm(org)] += 1
        except Exception:
            pass

    top = Counter(f.replace(f"{KB}/", "").split("/")[0] for f in published)

    print(f"全部 md: {len(all_files)}")
    print(f"正式发布 md（入口文档）: {len(published)}")
    print(f"来源数（source_org 归一去重）: {len(orgs)}")
    print()
    print("顶层分类分布:")
    for k, n in top.most_common():
        print(f"  {n:5d}  {k}")
    print()
    print("提示：块数 = 运行 ingest.py 末尾打印的『库中共 N 条』。")


if __name__ == "__main__":
    main()
