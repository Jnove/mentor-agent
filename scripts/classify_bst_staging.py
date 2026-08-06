"""bst 百事通暂存文档处理：正文 md5 去重 + 标题查重 + 质量审查 + 分类入库/淘汰。

只处理 data/kb_staging 下 bst-faq / bst-work-guide / bst-norm-file 三个目录，
不动其他来源。默认 dry-run（只统计打印）；--apply 才实际移动/改写。

入库：  knowledge_base/FAQ/百事通/{常见问题,办事指南}/   knowledge_base/政策/百事通/规范性文件/
        时效性通知（标题含"通知"且正文为活动/时间安排）→ knowledge_base/通知/百事通/
淘汰：  knowledge_base/staging/bst-<类型>/<原相对路径>，valid=false, review_status=rejected
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

import frontmatter

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "kb_staging"
KB = ROOT / "knowledge_base"
REPORT = STAGING / "bst_classify_report.md"

SRC_DIRS = {
    "bst-faq": STAGING / "bst-faq" / "FAQ" / "百事通" / "常见问题",
    "bst-work-guide": STAGING / "bst-work-guide" / "FAQ" / "百事通" / "办事指南",
    "bst-norm-file": STAGING / "bst-norm-file" / "政策" / "百事通" / "规范性文件",
}
TARGET_DIRS = {
    "bst-faq": KB / "FAQ" / "百事通" / "常见问题",
    "bst-work-guide": KB / "FAQ" / "百事通" / "办事指南",
    "bst-norm-file": KB / "政策" / "百事通" / "规范性文件",
}
STAGING_DST = {
    "bst-faq": KB / "staging" / "bst-faq",
    "bst-work-guide": KB / "staging" / "bst-work-guide",
    "bst-norm-file": KB / "staging" / "bst-norm-file",
}
KEEP_PRIORITY = {"bst-faq": 0, "bst-work-guide": 1, "bst-norm-file": 2}

HIGH_KEYWORDS = [
    "本科生", "研究生", "学生", "选课", "转专业", "保研", "推免", "学位", "奖学金",
    "助学金", "助学贷款", "评奖", "军训", "宿舍", "食堂", "校车", "医保", "医疗",
    "休学", "复学", "退学", "学籍", "毕业", "就业", "实习", "竞赛", "夏令营", "招生",
    "培养方案", "绩点", "教材", "实验", "考试", "自习", "图书馆", "校园卡", "入党",
    "团组织", "志愿", "社会实践", "出国", "交流", "签证", "户口", "证明", "成绩单",
    "报到", "注册", "学费", "资助", "勤工俭学", "困难补助", "心理咨询",
]
LOW_KEYWORDS = [
    "教职工", "离退休", "退休", "在职", "劳务派遣", "博士后", "职称", "评聘", "招聘",
    "招租", "招标", "中标", "采购", "成交", "成果转化", "捐赠", "校友", "干部任免",
    "党委", "纪委", "审计", "财务报销", "科研项目", "课题申报", "学报", "期刊",
    "学术会议",
    # 抽样补充：教职工/行政向服务（无学生语境才生效的词见 STUDENT_GATED）
    "领军人才", "高层次人才", "人才派遣", "派遣", "青年教师", "师德", "用印", "立卷",
    "任务书", "差旅费", "加班", "预算编制", "科技奖励", "专利", "用房", "横向",
    "入账", "涉密", "管理费", "特聘", "校史", "仪器", "验收", "国防", "继续教育",
    "协同办公", "商品房",
]
# 这些低价值词只在"无学生词"语境下生效（博士后/科研项目/课题申报/学术会议/财务报销/报销）
STUDENT_WORDS = ("学生", "本科生", "研究生", "留学生", "在校生", "新生", "大学生", "本科", "硕博", "食堂", "宿舍")
STAFF_MARKERS = ("教职工", "离退休", "退休", "在职", "劳务派遣", "职称", "评聘", "招聘")

FOOTER_RE = re.compile(
    r"^(咨询电话|受理部门|科室|监督电话|受理机构|受理地方|受理时间|办理时间)[:：]"
)


def body_without_footer(body: str) -> str:
    """去掉正文尾部「咨询电话/受理部门/科室」等落款行，避免影响关键词打分。"""
    return "\n".join(ln for ln in body.splitlines() if not FOOTER_RE.match(ln.strip()))

PLACEHOLDER_PATTERNS = ("自动抽取失败", "（答案为空）", "答案为空")
EMPTY_CHARS = 30


def norm_title(text: str) -> str:
    return re.sub(r"\s+", "", str(text).lower())


def sig_of(body: str) -> str:
    return re.sub(r"\s+", "", body or "")


def md5_of(body: str) -> str:
    return hashlib.md5(sig_of(body).encode("utf-8")).hexdigest()


def parse(path: Path):
    post = frontmatter.loads(path.read_text(encoding="utf-8-sig"))
    return post


def build_kb_title_index(kb: Path) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for path in sorted(kb.rglob("*.md")):
        rel = path.relative_to(kb).as_posix()
        if rel.startswith("staging/"):
            continue
        try:
            post = parse(path)
        except Exception:
            continue
        title = str(post.metadata.get("title") or path.stem)
        index.setdefault(norm_title(title), []).append(rel)
    return index


def hard_reject_reason(meta: dict, body: str, sig: str) -> str | None:
    if PLACEHOLDER_PATTERNS and any(p in sig for p in PLACEHOLDER_PATTERNS):
        return "空/占位"
    if len(sig) < EMPTY_CHARS:
        return "空/占位"
    title = norm_title(str(meta.get("title", "")))
    hay = title + sig[:500]
    if re.search(r"任免|任命|免去|任职|干部", title) and not any(w in hay for w in STUDENT_WORDS):
        return "纯人事任免"
    if re.search(r"招标|招租|采购|中标|成交", title) and not any(w in hay for w in STUDENT_WORDS):
        return "纯招标/采购"
    if re.search(r"新闻|快讯|报道", title) and not any(w in hay for w in STUDENT_WORDS):
        return "纯新闻稿"
    return None


def score_doc(meta: dict, body: str) -> tuple[str, list[str], list[str]]:
    title = str(meta.get("title", ""))
    tags = " ".join(str(t) for t in (meta.get("tags") or []))
    haystack = title + " " + tags + " " + body_without_footer(body)[:500]
    has_student = any(w in haystack for w in STUDENT_WORDS)
    # 教职工向标记且无任何学生词 → 直接低价值（如"教职工医保"这类高频词打平的情况）
    if any(m in haystack for m in STAFF_MARKERS) and not has_student:
        return "low", [], [m for m in STAFF_MARKERS if m in haystack]
    high = [k for k in HIGH_KEYWORDS if k in haystack]
    low = []
    for k in LOW_KEYWORDS:
        if k in haystack:
            # 党委/纪委在"党委学生工作部"等落款中出现，有学生语境时不作为低价值信号；
            # 其余 gated 词（报销/博士后/科研项目等）同样仅在无学生语境下计数
            if k in STUDENT_GATED and has_student:
                continue
            if k in ("党委", "纪委") and has_student:
                continue
            low.append(k)
    if len(high) > len(low):
        return "valuable", high, low
    if len(low) > len(high):
        return "low", high, low
    return "uncertain", high, low


STUDENT_GATED = {"博士后", "财务报销", "报销", "科研项目", "课题申报", "学术会议", "成果奖", "课题申请", "居住证", "采购", "招租", "招标", "中标", "成交"}


def load_docs() -> list[dict]:
    docs = []
    for src, d in SRC_DIRS.items():
        for path in sorted(d.glob("*.md")):
            try:
                post = parse(path)
            except Exception as exc:
                docs.append({
                    "src": src, "path": path, "meta": {}, "body": "",
                    "parse_error": str(exc),
                })
                continue
            meta = post.metadata
            body = post.content or ""
            docs.append({
                "src": src, "path": path, "meta": meta, "body": body,
                "md5": md5_of(body), "sig": sig_of(body),
            })
    return docs


def pick_keep(members: list[dict]) -> dict:
    def key(d: dict) -> tuple:
        return (
            KEEP_PRIORITY[d["src"]],
            len(d["path"].parts),
            d["path"].as_posix(),
        )
    return min(members, key=key)


def analyze(docs: list[dict], kb_index: dict[str, list[str]]) -> dict:
    """去重 + 标题查重 + 质量审查。原地给 docs 打 reason，返回各桶列表。"""
    dropped_md5: list[dict] = []
    for members in [m for m in _by_hash(docs).values() if len(m) > 1]:
        keep = pick_keep(members)
        for m in members:
            if m is not keep:
                m["reason"] = "重复(md5)"
                dropped_md5.append(m)

    dropped_title: list[dict] = []
    for d in docs:
        if "reason" in d:
            continue
        key = norm_title(d["meta"].get("title"))
        if key and key in kb_index:
            d["reason"] = "重复(标题匹配正式库)"
            d["kb_hits"] = kb_index[key]
            dropped_title.append(d)

    rejected_low: list[dict] = []
    uncertain: list[dict] = []
    notice_candidates: list[dict] = []
    valuable: list[dict] = []
    for d in docs:
        if "reason" in d:
            continue
        hard = hard_reject_reason(d["meta"], d["body"], d["sig"])
        if hard:
            d["reason"] = f"低价值({hard})"
            rejected_low.append(d)
            continue
        verdict, high, low = score_doc(d["meta"], d["body"])
        d["high"] = high
        d["low"] = low
        if verdict == "low":
            d["reason"] = f"低价值(关键词: {'/'.join(low)})"
            rejected_low.append(d)
        elif verdict == "uncertain":
            d["reason"] = "不确定"
            uncertain.append(d)
        else:
            d["reason"] = "入库"
            valuable.append(d)

    return {
        "dropped_md5": dropped_md5,
        "dropped_title": dropped_title,
        "rejected_low": rejected_low,
        "uncertain": uncertain,
        "notice": notice_candidates,
        "valuable": valuable,
    }


def _by_hash(docs: list[dict]) -> dict[str, list[dict]]:
    by_hash: dict[str, list[dict]] = {}
    for d in docs:
        by_hash.setdefault(d["md5"], []).append(d)
    return by_hash


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际移动/改写")
    args = ap.parse_args()

    docs = [d for d in load_docs() if not d.get("parse_error")]
    failed = [d for d in load_docs() if d.get("parse_error")]
    print(f"共解析 {len(docs)} 篇，解析失败 {len(failed)} 篇")
    for d in failed:
        print("  PARSE FAIL:", d["src"], d["path"].name, d["parse_error"])

    buckets = analyze(docs, build_kb_title_index(KB))
    dropped_md5 = buckets["dropped_md5"]
    dropped_title = buckets["dropped_title"]
    rejected_low = buckets["rejected_low"]
    uncertain = buckets["uncertain"]
    # 不确定 → 保守原则入库：正文有实质内容（已排除空/占位）且服务对象模糊 → 有价值
    for d in uncertain:
        d["reason"] = "入库(不确定→保守)"
    valuable = buckets["valuable"] + uncertain

    # ---- 统计 ----
    per_src: dict[str, dict] = {}
    for src in SRC_DIRS:
        src_docs = [d for d in docs if d["src"] == src]
        n_total = len(src_docs)
        n_md5 = sum(1 for d in src_docs if d.get("reason") == "重复(md5)")
        n_title = sum(1 for d in src_docs if d.get("reason") == "重复(标题匹配正式库)")
        n_low = sum(1 for d in src_docs if d.get("reason", "").startswith("低价值"))
        n_unc = sum(1 for d in src_docs if d.get("reason") == "入库(不确定→保守)")
        n_ingest = n_total - n_md5 - n_title - n_low
        per_src[src] = {
            "total": n_total, "md5": n_md5, "title": n_title, "low": n_low,
            "uncertain": n_unc, "ingest": n_ingest,
        }

    print("\n=== 统计（dry-run，未移动）===")
    for src in SRC_DIRS:
        s = per_src[src]
        print(
            f"{src}: 总 {s['total']} | md5重复 {s['md5']} | 标题重复 {s['title']} "
            f"| 低价值 {s['low']} | 不确定(保守入库) {s['uncertain']} | 入库 {s['ingest']}"
        )
    print(f"\n合计: 总 {len(docs)} | md5重复 {len(dropped_md5)} | 标题重复 {len(dropped_title)} "
          f"| 低价值 {len(rejected_low)} | 不确定(保守入库) {len(uncertain)} | 入库 {len(valuable)}")

    lines = ["# bst 百事通暂存文档分类报告", "",
             f"- 扫描：{len(docs)} 篇（bst-faq {len(list(SRC_DIRS['bst-faq'].glob('*.md')))} / "
             f"bst-work-guide {len(list(SRC_DIRS['bst-work-guide'].glob('*.md')))} / "
             f"bst-norm-file {len(list(SRC_DIRS['bst-norm-file'].glob('*.md')))}）",
             f"- md5 重复剔除：{len(dropped_md5)}；与正式库标题重复：{len(dropped_title)}",
             f"- 质量淘汰（低价值）：{len(rejected_low)}；不确定→保守入库：{len(uncertain)}",
             f"- 入库：{len(valuable)}；淘汰：{len(dropped_md5) + len(dropped_title) + len(rejected_low)}",
             ""]
    for src in SRC_DIRS:
        s = per_src[src]
        lines.append(f"- {src}: 总 {s['total']} → 入库 {s['ingest']}（其中不确定保守入库 {s['uncertain']}）"
                     f"，淘汰 {s['total'] - s['ingest']}（md5 {s['md5']} / 标题 {s['title']} / 低价值 {s['low']}）")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告已写入 {REPORT.relative_to(ROOT).as_posix()}")

    # 目标目录冲突检查
    if valuable:
        dests = {}
        for d in valuable:
            dest = TARGET_DIRS[d["src"]]
            dests.setdefault(dest, set())
            dests[dest].add(d["path"].name)
        print("\n=== 目标目录文件名冲突检查 ===")
        for dest, names in dests.items():
            dup_names = {n for n in names if sum(1 for x in names if x == n) > 1}
            print(f"{dest}: {len(names)} 个文件，冲突 {len(dup_names)}")
            for n in dup_names:
                print("  CONFLICT:", n)

    if not args.apply:
        return

    # ---- 4. 实际移动 ----
    moved_ingest = 0
    moved_rejected = 0
    for d in valuable:
        dest = TARGET_DIRS[d["src"]] / d["path"].name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            print("!! 目标已存在，跳过:", dest)
            continue
        shutil.move(str(d["path"]), str(dest))
        moved_ingest += 1

    for d in docs:
        if "reason" not in d:
            continue
        if d.get("reason").startswith("低价值") or d.get("reason").startswith("重复"):
            rel = d["path"].relative_to(SRC_DIRS[d["src"]])
            dest = STAGING_DST[d["src"]] / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                print("!! 淘汰目标已存在，跳过:", dest)
                continue
            shutil.move(str(d["path"]), str(dest))
            post = parse(dest)
            post.metadata["valid"] = False
            post.metadata["review_status"] = "rejected"
            dest.write_text(
                frontmatter.dumps(post, sort_keys=False).rstrip() + "\n",
                encoding="utf-8", newline="\n",
            )
            moved_rejected += 1

    print(f"\n已移动：入库 {moved_ingest}，淘汰 {moved_rejected}")


if __name__ == "__main__":
    main()
