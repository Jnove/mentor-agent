from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


REPO_URL = "https://github.com/kaixuanwang2003/zju-welcome"
SOURCE_ORG = "zju-welcome 新生指引编委会（GitHub）"


def clean_title(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip().strip("#").strip()


def first_h1(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("#"):
            return clean_title(line.lstrip("#").strip()) or fallback
    return fallback


def yaml_quote(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def git_output(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), *args],
        text=True,
        encoding="utf-8",
    ).strip()


def parse_nav(repo: Path) -> dict[str, list[str]]:
    nav_text = (repo / "mkdocs.yml").read_text(encoding="utf-8")
    nav_part = nav_text.split("\nnav:", 1)[1].split("\ntheme:", 1)[0]
    stack: dict[int, str] = {}
    nav_map: dict[str, list[str]] = {}
    for raw in nav_part.splitlines():
        if not raw.strip().startswith("- "):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        item = raw.strip()[2:].strip()
        leaf = re.match(r"(.+?):\s*[\"']([^\"']+\.md)[\"']\s*$", item)
        if leaf:
            label = leaf.group(1).strip()
            rel = leaf.group(2).strip().replace("\\", "/")
            parents = [stack[k] for k in sorted(stack) if k < indent]
            nav_map[rel] = parents + [label]
            stack[indent] = label
        elif item.endswith(":"):
            stack[indent] = item[:-1].strip()
        for k in list(stack):
            if k > indent:
                del stack[k]
    return nav_map


BASE_TAGS_BY_TOP = {
    "basics": ["常用信息", "校历", "地图", "校区", "院系", "联系方式", "网站", "公众号", "软件", "校史校情"],
    "registration": ["报到", "新生报到", "入学准备", "注册缴费", "始业教育", "防骗", "交通", "电话卡"],
    "military_training": ["军训", "作息", "组织架构", "军训活动", "入党", "纪律", "军训提醒"],
    "learning": ["学习", "学业政策", "培养方案", "课程考核", "专业确认", "转专业", "辅修", "科研训练", "竞赛", "竺院"],
    "course_sys": ["选课", "选课系统", "选课规则", "预选", "补退选", "课程容量", "选课操作"],
    "awards&grants": ["奖助", "评奖评优", "奖学金", "荣誉称号", "学生资助", "标兵", "竺院奖助"],
    "life": ["校园生活", "宿舍", "食堂", "校园网", "校车", "医疗", "图书馆", "场馆预约", "社团", "杭州生活"],
    "dorms": ["园区", "宿舍", "学园", "求是学院", "丹青", "云峰", "蓝田", "竺院玉湖"],
    "haining": ["海宁国际校区", "ZJUI", "海宁学习", "海宁生活", "院历", "校区地图"],
    "HK_Macao_Taiwan": ["港澳台学生", "港澳台专题", "身份材料", "课程衔接", "实践实习", "升学就业"],
    "cc98": ["CC98", "论坛", "经验帖", "新生宝典", "信息检索"],
}


KEYWORD_TAGS = [
    ("转专业", ["转专业", "主修专业确认", "学籍异动"]),
    ("专业确认", ["专业确认", "主修专业", "分流"]),
    ("培养方案", ["培养方案", "毕业学分", "课程类别", "主干课程"]),
    ("毕业", ["毕业要求", "毕业资格", "学分要求"]),
    ("推免", ["推免", "保研", "深造", "本博贯通"]),
    ("保研", ["推免", "保研", "深造"]),
    ("奖学金", ["奖学金", "国家奖学金", "一等奖学金", "外设奖学金"]),
    ("评奖评优", ["评奖评优", "标兵", "综合评价", "能力素养"]),
    ("荣誉", ["荣誉称号", "三好学生", "优秀学生干部"]),
    ("资助", ["资助对象", "助学金", "勤工助学", "绿色通道", "困难生"]),
    ("第二课堂", ["第二课堂", "二课", "美育", "劳育"]),
    ("第三课堂", ["第三课堂", "三课", "社会实践", "志愿服务"]),
    ("第四课堂", ["第四课堂", "四课", "国际化", "交流项目"]),
    ("志愿", ["志愿服务", "志愿者", "星级志愿者", "小时数"]),
    ("考试", ["考试", "期中考试", "期末考试", "开卷", "半开卷", "补考", "缓考"]),
    ("成绩", ["成绩", "绩点", "GPA", "重修", "零分", "成绩记载"]),
    ("英语", ["英语", "CET", "四级", "六级", "口语", "免修", "水平测试"]),
    ("选课", ["选课", "预选", "补退选", "选课系统", "课表"]),
    ("校车", ["校车", "交通", "站点", "玉泉", "紫金港"]),
    ("校园卡", ["校园卡", "实体卡", "电子校园卡", "补卡"]),
    ("网络", ["校园网", "ZJUWLAN", "ZJUWLAN-Secure", "WebVPN", "RVPN", "通行证"]),
    ("图书馆", ["图书馆", "座位预约", "研讨间", "违约"]),
    ("场馆", ["场馆预约", "教室借用", "公共空间", "活动室"]),
    ("食堂", ["食堂", "餐饮", "点餐", "圆桌"]),
    ("宿舍", ["宿舍", "寝室", "园区", "门禁"]),
    ("社团", ["社团", "学生组织", "百团大战", "纳新"]),
    ("入党", ["入党", "党支部", "团员", "积极分子"]),
    ("军训", ["军训", "军训活动", "军训作息", "军训考核"]),
    ("海宁", ["海宁国际校区", "ZJUI", "ZJE"]),
    ("港澳台", ["港澳台学生", "港澳台专题"]),
    ("竺可桢", ["竺可桢学院", "竺院", "混合班", "荣誉项目"]),
    ("求是学院", ["求是学院", "学园", "思政培养"]),
]


SPECIFIC_BY_STEM = {
    "network_detailed": ["统一身份认证", "通行证激活", "RVPN账号", "校园邮箱激活"],
    "school_calendar": ["校历", "学期安排", "寒暑假", "考试周"],
    "school_map": ["校园地图", "地图导航"],
    "campuses": ["紫金港", "玉泉", "西溪", "华家池", "之江", "舟山", "海宁"],
    "colleges": ["学部", "学院", "专业学院"],
    "contact": ["常用电话", "咨询电话", "报警电话"],
    "websites": ["常用网站", "教务系统", "办公网"],
    "channels": ["公众号", "小程序", "信息渠道"],
    "software": ["浙大钉", "正版软件", "APP"],
    "slang": ["黑话", "校园称呼", "简称"],
    "fee": ["学费", "缴费", "注册"],
    "preparations_online": ["线上准备", "迎新系统", "信息填报"],
    "items_to_take": ["物品准备", "行李", "证件"],
    "transportation": ["到校交通", "火车站", "机场", "报到交通"],
    "procedure": ["报到流程", "现场报到", "学院报到"],
    "cellphone-plans": ["电话卡", "校园套餐", "运营商"],
    "time": ["军训作息", "时间安排"],
    "structure": ["军训组织", "连队", "团部"],
    "party": ["入党流程", "入党申请书", "党团关系"],
    "concepts": ["基本概念", "学分", "绩点", "四课融通"],
    "program": ["培养方案", "毕业学分", "主干课程"],
    "eval": ["课程考核", "考试", "成绩"],
    "declaring_major": ["专业确认", "主修专业"],
    "major_transfer": ["转专业", "跨专业"],
    "minor": ["辅修", "微辅修"],
    "special_course": ["特殊课程", "体育", "英语", "H课"],
    "training_program": ["科研训练", "SRTP", "素质训练"],
    "research_academic": ["学生科研训练计划", "科研训练", "SRTP"],
    "competitions": ["学科竞赛", "竞赛加分"],
    "intoCKC": ["竺院", "混合班", "辅修班", "荣誉项目"],
    "enroll": ["选课通知", "选课时间"],
    "rules": ["选课规则", "选课轮次"],
    "operation": ["选课操作", "选课系统"],
    "tricks": ["选课技巧", "捡漏", "冲突"],
    "evaluation": ["基础评价", "综合评价", "能力素养"],
    "honor": ["荣誉称号", "标兵"],
    "scholarships": ["奖学金", "奖学金比例"],
    "grants": ["学生资助", "助学金", "勤工助学"],
    "awards_ckc": ["竺院奖助", "竺院评奖评优"],
    "campus": ["校园区域", "紫金港区域"],
    "dorm": ["寝室园区", "宿舍"],
    "post": ["快递", "收寄服务"],
    "canteen": ["食堂", "餐饮"],
    "traffic": ["校园交通", "校车", "自行车"],
    "medical-treatment": ["校医院", "医保", "就医"],
    "library": ["图书馆", "座位预约", "研讨间"],
    "public_resources": ["场馆空间", "教室借用", "预约"],
    "extracurricular": ["学生组织", "社团", "百团大战"],
    "sports": ["运动场馆", "艺博馆"],
    "cc98": ["CC98", "论坛注册", "校园论坛"],
    "recommended": ["经验帖", "精选帖子", "论坛资源"],
}


def category_for(rel: str) -> str:
    if rel.startswith(("registration", "military_training")):
        return "通知"
    if rel in {"callout.md", "basics/school_calendar.md", "haining/basics/school_calendar.md"}:
        return "通知"
    if rel.startswith(("learning", "course_sys", "awards&grants", "haining/learning", "HK_Macao_Taiwan")):
        return "政策"
    if rel == "haining/scholarship.md":
        return "政策"
    return "FAQ"


def collect_tags(rel: str, title: str, breadcrumbs: list[str], text: str) -> list[str]:
    tags = ["浙江大学", "本科新生", "新生指引", "zju-welcome", "GitHub来源", "非官方资料", "日期取自Git提交"]
    parts = rel.split("/")
    top = parts[0]
    tags += BASE_TAGS_BY_TOP.get(top, [])
    tags += [clean_title(x) for x in breadcrumbs if clean_title(x) not in {"首页"}]
    tags += SPECIFIC_BY_STEM.get(Path(rel).stem, [])
    haystack = title + "\n" + rel + "\n" + text[:6000]
    for needle, extra in KEYWORD_TAGS:
        if needle in haystack:
            tags += extra
    tags += [p for p in parts[:-1] if p != "docs"]

    deduped: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        tag = clean_title(str(tag)).strip()
        if tag and tag not in seen:
            seen.add(tag)
            deduped.append(tag)
    return deduped


def source_url(commit: str, rel: str) -> str:
    return f"{REPO_URL}/blob/{commit}/docs/{rel.replace(' ', '%20')}"


def generate(repo: Path, mentor_root: Path) -> None:
    repo = repo.resolve()
    kb_root = (mentor_root / "knowledge_base").resolve()
    out_root = (kb_root / "zju-welcome").resolve()
    if not str(out_root).startswith(str(kb_root)) or out_root.name != "zju-welcome":
        raise RuntimeError(f"Unsafe output path: {out_root}")
    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    commit = git_output(repo, "rev-parse", "HEAD")
    nav_map = parse_nav(repo)
    rows = []

    for src in sorted((repo / "docs").rglob("*.md")):
        rel = src.relative_to(repo / "docs").as_posix()
        raw = src.read_text(encoding="utf-8-sig")
        original_title = first_h1(raw, Path(rel).stem)
        breadcrumbs = nav_map.get(rel, [])
        if breadcrumbs:
            title_parts = [p for p in breadcrumbs if p != "首页"]
            if not title_parts:
                title_parts = [original_title]
            elif clean_title(title_parts[-1]) != clean_title(original_title):
                title_parts.append(original_title)
        else:
            title_parts = [rel.split("/")[0], original_title]
        doc_title = "新生指引｜" + "｜".join(clean_title(p) for p in title_parts if clean_title(p))
        publish_date = git_output(repo, "log", "-1", "--format=%ad", "--date=short", "--", str(src.relative_to(repo)))
        category = category_for(rel)
        tags = collect_tags(rel, original_title, breadcrumbs, raw)

        out = out_root / "docs" / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        tag_line = "、".join(tags)
        frontmatter = [
            "---",
            f"title: {yaml_quote(doc_title)}",
            f"source_url: {yaml_quote(source_url(commit, rel))}",
            f"source_org: {yaml_quote(SOURCE_ORG)}",
            f"publish_date: {publish_date}",
            f"category: {yaml_quote(category)}",
            "tags:",
            *[f"  - {yaml_quote(tag)}" for tag in tags],
            "valid: true",
            "---",
            "",
        ]
        intro = [
            f"# {doc_title}",
            "",
            f"- 原始文章标题：{original_title}",
            f"- 原仓库路径：docs/{rel}",
            f"- 来源说明：仅来自 GitHub 仓库 `kaixuanwang2003/zju-welcome`；这是学生编写的新生指引资料，不等同于学校官方政策原文。",
            f"- 检索标签：{tag_line}",
            "",
            "## 原文内容",
            "",
        ]
        out.write_text("\n".join(frontmatter + intro) + raw.rstrip() + "\n", encoding="utf-8", newline="\n")
        rows.append((rel, doc_title, category, publish_date, len(tags), len(raw)))

    manifest_lines = [
        "---",
        f"title: {yaml_quote('zju-welcome 导入清单')}",
        f"source_url: {yaml_quote(REPO_URL)}",
        f"source_org: {yaml_quote(SOURCE_ORG)}",
        "publish_date: 2026-07-09",
        f"category: {yaml_quote('FAQ')}",
        "tags:",
        f"  - {yaml_quote('导入清单')}",
        f"  - {yaml_quote('zju-welcome')}",
        "valid: false",
        "---",
        "",
        "# zju-welcome 导入清单",
        "",
        f"- 源仓库：{REPO_URL}",
        f"- 固定提交：`{commit}`",
        "- 导入范围：`docs/**/*.md`",
        f"- 导入文章数：{len(rows)}",
        "- 日期说明：`publish_date` 使用每篇文章在 Git 仓库中的最后提交日期；已加标签 `日期取自Git提交`。",
        "- 来源限制：未扩展到浙江大学官网、教务处、学工部等外部来源。",
        "",
        "| 源路径 | 知识库标题 | 分类 | 日期 | 标签数 | 原文字数 |",
        "|---|---|---|---|---:|---:|",
    ]
    for rel, title, category, publish_date, tag_count, chars in rows:
        manifest_lines.append(
            f"| `docs/{rel}` | {title.replace('|', '\\|')} | {category} | {publish_date} | {tag_count} | {chars} |"
        )
    (out_root / "IMPORT_MANIFEST.md").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8", newline="\n")

    print(f"generated={len(rows)}")
    print(f"out={out_root}")
    print(f"commit={commit}")


if __name__ == "__main__":
    mentor = Path(__file__).resolve().parents[1]
    source_repo = mentor.parent / "zju-welcome"
    generate(source_repo, mentor)
