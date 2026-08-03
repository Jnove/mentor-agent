"""生成知识库暂存区审核清单：data/kb_staging/review_checklist.md

读取各来源 _manifest.json + 暂存文档 frontmatter，套用下方人工维护的审核要点，
输出给负责人逐条勾选的清单。清单落在 data/ 下（gitignore），不进 git。

用法: python scripts/kb_review_checklist.py
"""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

ROOT = Path(__file__).resolve().parent.parent
STAGING = ROOT / "data" / "kb_staging"
OUT = STAGING / "review_checklist.md"

# 人工维护的审核要点：(来源名, 标题子串, 建议)。匹配到的文档追加该行。
NOTES = [
    # ---- libweb-guizhang（图书馆规章制度）----
    ("libweb-guizhang", "电子资源使用管理办法 （试行）",
     "近重复：标题带空格，与库内《电子资源使用管理办法（试行）》同名，需归并或对齐 doc_id。"),
    ("libweb-guizhang", "线装古籍借阅规则",
     "近重复：标题缺『浙江大学图书馆』前缀，与库内同名文档 doc_id 不同，需归并。"),
    ("libweb-guizhang", "校友入馆须知",
     "日期核对：爬虫按 URL 取 2023-04-28；库内手工版取正文『更新时间 2025-06-27』，确认以哪个为准。"),
    # ---- ckc-qna（竺院 Q&A）----
    ("ckc-qna", "排名证明Q&A",
     "近重复：库内拆为 _part1/_part2 两篇，爬虫保持整篇；决定合并还是保留拆分。"),
    # ---- ckc-zhaosheng（竺院新生选拔）----
    ("ckc-zhaosheng", "2022级竺可桢学院学生选拔安排", "历史稿，判 valid: false。"),
    ("ckc-zhaosheng", "2021级优秀学生转入混合班、人文社科实验班", "历史稿，判 valid: false。"),
    ("ckc-zhaosheng", "2024级新生选拔安排", "历史稿，2025/2026 版 supersedes。"),
    ("ckc-zhaosheng", "2025级新生选拔安排", "历史稿，2026 版 supersedes（若已发布）。"),
    ("ckc-zhaosheng", "求是科学班2024年招生简章", "历史稿，被 2026 版 supersedes。"),
    ("ckc-zhaosheng", "求是科学班2025年招生简章",
     "历史稿，被 2026 版 supersedes；与库内分专业版（化学/数学/物理等）关系需确认是否互相 supersedes。"),
    ("ckc-zhaosheng", "求是科学班2026年招生简章", "最新版，valid: true。"),
    ("ckc-zhaosheng", "混合班2024年招生简章", "历史稿，被 2026 版 supersedes。"),
    ("ckc-zhaosheng", "混合班2025年招生简章", "历史稿，被 2026 版 supersedes。"),
    ("ckc-zhaosheng", "混合班2026年招生简章", "最新版，valid: true。"),
    ("ckc-zhaosheng", "图灵班2026年招生简章", "最新版，valid: true；与库内《图灵班增补实施细则》互补。"),
    ("ckc-zhaosheng", "新农科实验班2025年招生简章", "历史稿，被 2026 版 supersedes。"),
    ("ckc-zhaosheng", "新农科实验班（本博贯通）2024年招生简章", "历史稿，valid 评估。"),
    # ---- ckc-admin-files（竺院行政文件，多为 PDF）----
    ("ckc-admin-files", "学生评价实施细则（2024年修订版）", "PDF 正文（pypdf 抽取），抽查抽取质量；当前最新版。"),
    ("ckc-admin-files", "学生评价实施细则（2022年修订版）", "PDF 正文；2022 版被 2024 版 supersedes。"),
    ("ckc-admin-files", "学生学业评价办法", "PDF 正文，抽查完整性（正文较短，确认未截断）。"),
    ("ckc-admin-files", "学生分流工作实施细则", "PDF 正文，抽查质量。"),
    ("ckc-admin-files", "本科生主修专业确认办法", "PDF 正文，抽查质量。"),
    ("ckc-admin-files", "荣誉证书授予实施细则", "PDF 正文，抽查质量；与库内《荣誉证书授予Q&A》配套。"),
    ("ckc-admin-files", "专业导师制管理办法", "PDF 正文，抽查质量；与库内《导师制Q&A》配套。"),
    ("ckc-admin-files", "深度科研训练项目实施细则", "内联正文，抽查。"),
    ("ckc-admin-files", "选拔非竺可桢学院优秀学生转入竺可桢学院", "PDF 正文，抽查质量。"),
    ("ckc-admin-files", "最佳任课教师获奖名单", "时效性名单，重分类为通知或判 valid: false。"),
    ("ckc-admin-files", "优秀专业导师获得者名单", "时效性名单，重分类为通知或判 valid: false。"),
    ("ckc-admin-files", "转入竺可桢学院学生名单", "时效性名单，重分类为通知或判 valid: false。"),
    ("ckc-admin-files", "成立『双一流』经费领导小组的通知", "行政通知，valid 评估。"),
    # ---- cs-zhaosheng（计算机学院招生）----
    ("cs-zhaosheng", "2023年拟录取研究生政审、调档", "历史通知，valid: false。"),
    ("cs-zhaosheng", "2024年推荐免试研究生", "历史通知，valid: false。"),
    ("cs-zhaosheng", "2024级推荐免试研究生", "历史通知，valid: false。"),
    ("cs-zhaosheng", "2025年优秀应届本科毕业生免试攻读", "历史通知，2026 版 supersedes。"),
    ("cs-zhaosheng", "2025级推荐免试研究生", "历史通知，2026 版 supersedes。"),
    ("cs-zhaosheng", "2026级推荐免试研究生工作安排", "最新，valid: true。"),
    ("cs-zhaosheng", "2026年招收推荐免试研究生工作办法", "最新，valid: true；保研流程核心文档。"),
    ("cs-zhaosheng", "2026年推荐免试研究生招生复试公告", "最新，valid: true。"),
    ("cs-zhaosheng", "2026年面向港澳台地区研究生招生", "最新，valid: true。"),
    ("cs-zhaosheng", "2023年博士研究生招生简章", "历史，valid: false。"),
    ("cs-zhaosheng", "2023年博士研究生『申请-考核』", "历史，valid: false。"),
    ("cs-zhaosheng", "2023年普博、春硕博网报公告", "历史，valid: false。"),
    ("cs-zhaosheng", "导师资格目录-2026（持续更新中）", "长期有效（持续更新），valid: true。"),
    ("cs-zhaosheng", "导师与导师团队信息-2026（持续更新中）", "长期有效（持续更新），valid: true。"),
]

# 未命中人工要点时按标题启发式补一条通用提示
HEURISTIC = {
    "2026": "最新年份，重点核对适用范围后标 verified。",
    "持续更新": "长期有效文档，勿误标过期。",
    "名单": "时效性名单/公示，确认是否需长期保留。",
}


def load_manifests() -> list[tuple[str, dict]]:
    out = []
    for d in sorted(STAGING.iterdir()):
        if not d.is_dir():
            continue
        mf = d / "_manifest.json"
        if mf.exists():
            out.append((d.name, json.loads(mf.read_text(encoding="utf-8"))))
    return out


def title_of(source: str, rel: str) -> str:
    try:
        post = frontmatter.loads((STAGING / source / rel).read_text(encoding="utf-8"))
        return str(post.get("title", ""))
    except Exception:
        return Path(rel).stem


def review_note(source: str, title: str) -> str:
    for s, sub, note in NOTES:
        if s == source and sub in title:
            return note
    for sub, note in HEURISTIC.items():
        if sub in title:
            return note
    return "常规核验：来源、正文、适用范围。" if "持续更新" not in title else ""


def render() -> str:
    manifests = load_manifests()
    total = sum(len(m["results"]) for _, m in manifests)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# 知识库暂存审核清单",
        "",
        f"> 生成：{now}｜暂存 {total} 篇（{len(manifests)} 个来源），全部 needs_review，尚未入库。",
        "",
        "## 审核步骤",
        "1. 逐条核对下方『审核要点』，在暂存文档里改 `valid` / 归并 / 重分类。",
        "2. 审核通过后发布：",
        "   ```bash",
        "   python scripts/govern_kb.py",
        "   python ingest.py",
        "   python tests/eval_retrieval.py   # 检索回归",
        "   ```",
        "3. 已核验的文档：`review_status` 改 verified，填 `last_checked_at` 与 `maintainer`。",
        "",
        "## 通用要点（先看）",
        "- **近重复**：带『近重复』标注的文档与库内已有 doc 同名但 doc_id 不同，先归并再发布。",
        "- **时效**：历史年份的简章/通知判 `valid: false`；新版给旧版标 `supersedes`。",
        "- **PDF**：ckc-admin-files 的政策正文由 pypdf 抽取，抽查抽取质量与完整性。",
        "",
    ]
    for source, m in manifests:
        lines.append(f"## {source}（{len(m['results'])} 篇，errors={len(m['errors'])})")
        if m["errors"]:
            lines.append("")
            lines.append("**本轮抓取错误：**")
            for e in m["errors"]:
                lines.append(f"- {e}")
        lines.append("")
        lines.append("| 状态 | 标题 | 日期 | 审核要点 |")
        lines.append("|---|---|---|---|")
        for rel, r in sorted(m["results"].items(), key=lambda kv: kv[1]["publish_date"], reverse=True):
            title = title_of(source, rel)
            note = review_note(source, title)
            lines.append(f"| {r['status']} | {title} | {r['publish_date']} | {note} |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    OUT.write_text(render(), encoding="utf-8")
    print(f"已生成 {OUT.relative_to(ROOT).as_posix()}（{OUT.stat().st_size} 字节）")


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    main()
