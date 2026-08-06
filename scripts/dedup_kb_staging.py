"""data/kb_staging 查重脚本。

按「正文去掉空白后的 md5」做精确查重。聚合来源（sources.yaml 中 is_aggregate: true，
即官网「最新」汇总栏目）在重复组内让位给垂直栏目：同一通知只保留垂直栏目那一份。

文档分四类：
  ocr   —— 正文仅 [图片]/@PDFIFRAME@ 占位，纯图片无文字，需 OCR 后才能入库
  empty —— 正文为空（或仅标题+发布日期行），无检索价值，建议剔除
  重复  —— 与另一份正文 md5 完全一致，剔除
  入库  —— 唯一内容，直接入库

默认只生成报告 data/kb_staging/dedup_report.md；加 --apply 会把被剔除的重复副本
移动到 data/kb_staging/_dedup/ 下（保留原相对路径，可逆，不删除）。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import re
import shutil
from pathlib import Path

import frontmatter
import yaml

ROOT = Path(__file__).resolve().parent.parent
STAGING_DIR = ROOT / "data" / "kb_staging"
SOURCES_FILE = ROOT / "scripts" / "sources.yaml"
REPORT = STAGING_DIR / "dedup_report.md"
OCR_PLAN = "docs/ocr-rapidocr-plan.md"

_PLACEHOLDER_RE = re.compile(r"^\[图片\]$|^@PDFIFRAME@$")


def load_aggregate_names() -> set[str]:
    with open(SOURCES_FILE, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {s.get("name") for s in data.get("sources", []) if s.get("is_aggregate")}


def parse_doc(path: Path) -> tuple[dict, str, str]:
    """返回 (metadata, kind, md5)。kind ∈ {ocr, empty, ok}。"""
    post = frontmatter.loads(path.read_text(encoding="utf-8-sig"))
    body = post.content
    sig = "".join(
        ln.strip()
        for ln in body.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#") and not ln.lstrip().startswith(">")
    )
    if _PLACEHOLDER_RE.match(sig):
        kind = "ocr"
    elif sig == "":
        kind = "empty"
    else:
        kind = "ok"
    digest = hashlib.md5(re.sub(r"\s+", "", body).encode("utf-8")).hexdigest()
    return post.metadata, kind, digest


def rel_of(path: Path) -> str:
    return path.relative_to(STAGING_DIR).as_posix()


def source_of(path: Path) -> str:
    return rel_of(path).split("/", 1)[0]


def canonical_key(path: Path, aggregate: set[str]) -> tuple:
    """非聚合来源优先；其次路径层级浅者优先；最后按相对路径字典序。"""
    rel = rel_of(path)
    return (source_of(path) in aggregate, len(path.parts), rel)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="把被剔除的重复副本移动到 _dedup/ 下（可逆，不删除）")
    args = ap.parse_args()

    aggregate = load_aggregate_names()

    docs: list[tuple[Path, dict, str, str]] = []
    for path in sorted(STAGING_DIR.rglob("*.md")):
        if any(seg.startswith("_") for seg in path.parts[len(STAGING_DIR.parts):]):
            continue  # _dedup/ 等内部目录不参与
        if len(path.relative_to(STAGING_DIR).parts) < 2:
            continue  # 排除根级报告类工件（dedup_report.md 等）
        meta, kind, digest = parse_doc(path)
        docs.append((path, meta, kind, digest))

    ocr = [d for d in docs if d[2] == "ocr"]
    empty = [d for d in docs if d[2] == "empty"]
    ok = [d for d in docs if d[2] == "ok"]

    by_hash: dict[str, list[tuple[Path, dict, str, str]]] = {}
    for d in ok:
        by_hash.setdefault(d[3], []).append(d)

    groups = [members for members in by_hash.values() if len(members) > 1]
    singles = [members[0] for members in by_hash.values() if len(members) == 1]

    dup_copies: list[tuple[tuple[Path, dict, str, str], tuple[Path, dict, str, str]]] = []
    kept: list[tuple[Path, dict, str, str]] = []
    for members in groups:
        keep = min(members, key=lambda m: canonical_key(m[0], aggregate))
        kept.append(keep)
        for m in members:
            if m is not keep:
                dup_copies.append((keep, m))

    ingest = sorted(singles + kept, key=lambda d: rel_of(d[0]))

    lines: list[str] = []
    w = lines.append
    w(f"# kb_staging 查重报告")
    w("")
    w("- 扫描目录：`data/kb_staging`")
    w(f"- 总文档：{len(docs)} ｜ 重复组：{len(groups)} ｜ 重复副本（剔除）：{len(dup_copies)}"
      f" ｜ OCR 待处理：{len(ocr)} ｜ 空正文（建议剔除）：{len(empty)} ｜ 直接入库：{len(ingest)}")
    w(f"- 生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}（`--apply` 前仅读不改）")
    w("")

    w("## 摘要（按来源）")
    w("")
    w("| 来源 | 总数 | 入库 | 重复剔除 | OCR | 空正文 |")
    w("|---|---|---|---|---|---|")
    sources = sorted({source_of(d[0]) for d in docs})
    for src in sources:
        def count(pred):
            return sum(1 for d in docs if source_of(d[0]) == src and pred(d))
        total = count(lambda d: True)
        n_ocr = count(lambda d: d[2] == "ocr")
        n_empty = count(lambda d: d[2] == "empty")
        n_dup = count(lambda d: d[2] == "ok" and any(d is m for _, m in dup_copies))
        n_ingest = total - n_ocr - n_empty - n_dup
        agg = "（聚合）" if src in aggregate else ""
        w(f"| {src}{agg} | {total} | {n_ingest} | {n_dup} | {n_ocr} | {n_empty} |")
    w("")

    w("## 一、OCR 待处理（纯图片，需 OCR 后入库）")
    w("")
    w(f"正文仅图片占位无文字。OCR 方案见 `{OCR_PLAN}`（RapidOCR，尚未实现）；"
      "这 3 篇暂不入库，等 OCR 实现后回填文字再入库。")
    w("")
    for path, meta, _, _ in sorted(ocr, key=lambda d: rel_of(d[0])):
        w(f"- **{meta.get('title', '?')}**")
        w(f"  - 来源：`{source_of(path)}`")
        w(f"  - URL：{meta.get('source_url', '?')}")
        w(f"  - 文件：`{rel_of(path)}`")
    w("")

    w("## 二、空正文（无内容，建议剔除）")
    w("")
    w("正文为空（仅标题与发布日期行），入库无检索价值，建议不迁入知识库。")
    w("")
    for path, meta, _, _ in sorted(empty, key=lambda d: rel_of(d[0])):
        w(f"- **{meta.get('title', '?')}**  `{source_of(path)}`  [{meta.get('publish_date', '?')}]")
        w(f"  - 文件：`{rel_of(path)}`")
    w("")

    w("## 三、重复剔除（不入库）")
    w("")
    w("正文 md5 完全一致。每组保留垂直栏目副本，剔除聚合来源「最新」栏目副本；"
      "若组内多个垂直栏目同文，仅保留优先级最高的一份。")
    w("")
    for members in sorted(groups, key=lambda m: rel_of(min(m, key=lambda d: canonical_key(d[0], aggregate))[0])):
        keep = min(members, key=lambda m: canonical_key(m[0], aggregate))
        title = keep[1].get("title", "?")
        w(f"### {title}")
        w(f"- 保留：`{rel_of(keep[0])}`")
        for m in sorted((d for d in members if d is not keep), key=lambda d: rel_of(d[0])):
            w(f"- 剔除：`{rel_of(m[0])}`")
    w("")

    w("## 四、直接入库（唯一内容）")
    w("")
    w("以下文档正文唯一、无重复、非空，可进入 `knowledge_base/` 入库流程。")
    w("")
    for path, meta, _, _ in sorted(ingest, key=lambda d: rel_of(d[0])):
        w(f"- `{rel_of(path)}`")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if args.apply:
        for keep, drop in dup_copies:
            dest = STAGING_DIR / "_dedup" / rel_of(drop[0])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(drop[0]), str(dest))
        print(f"已移动 {len(dup_copies)} 份重复副本到 data/kb_staging/_dedup/")

    print(f"报告已写入 {rel_of(REPORT)}")
    print(f"总文档 {len(docs)}：入库 {len(ingest)}，重复剔除 {len(dup_copies)}，"
          f"OCR {len(ocr)}，空正文 {len(empty)}")


if __name__ == "__main__":
    main()
