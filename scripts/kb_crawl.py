"""来源采集器：按 scripts/sources.yaml 配置抓取网页并生成 KB 暂存文档。

用法:
    python scripts/kb_crawl.py --source libweb-guizhang          # 抓单个来源（写暂存区）
    python scripts/kb_crawl.py --all --dry-run                    # 预演，不写暂存区
    python scripts/kb_crawl.py --source libweb-guizhang --limit 2 # 限制详情页数（调试）

输出:
    knowledge_base/staging/<source_name>/<target_dir>/<title>_<org>_<年>.md
    knowledge_base/staging/<source_name>/_manifest.json

原则:
    - 只负责抓取与机械性元数据；无法确定的字段一律 未明确 / unknown / needs_review。
    - 正文保留原文结构；normalize_headings 时仅把「一、二、三」短行提升为 ## 标题。
    - 不判断政策是否有效/被替代——那是人工审核阶段的事。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import frontmatter
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.config import KB_DIR
from core.kb_schema import make_doc_id, validate_metadata

ROOT = Path(__file__).resolve().parent.parent
STAGING_DIR = ROOT / "data" / "kb_staging"
SOURCES_FILE = Path(__file__).resolve().parent / "sources.yaml"

REQUIRED_SOURCE_FIELDS = (
    "name", "source_org", "authority_level", "category", "source_type",
    "target_dir", "base_url", "content_selector",
)

# 在浏览器上下文内把正文容器转 Markdown：附件/图片/链接显式化，表格保留。
JS_MD = r"""
(el) => {
  const lines = [];
  const FILE_EXT = /\.(docx?|xlsx?|pptx?|pdf|zip|rar|7z|wps)(\?|#|$)/i;
  const isFile = (h) => { try { return FILE_EXT.test(new URL(h, document.baseURI).pathname); } catch(e){ return false; } };
  function renderInline(node) {
    let out = '';
    for (const n of node.childNodes) {
      if (n.nodeType === 3) { out += n.textContent.replace(/\s*\n\s*/g, ' '); }
      else if (n.nodeType === 1) {
        const tag = n.tagName;
        if (tag === 'A') {
          const t = (n.innerText || '').trim().replace(/\s+/g, ' ');
          const href = n.getAttribute('href') || '';
          const abs = (href) ? new URL(href, document.baseURI).href : '';
          if (href && isFile(abs)) { out += '[附件：' + (t || '文件') + '](' + abs + ')'; }
          else if (/^https?:/.test(abs)) { out += '[' + t + '](' + abs + ')'; }
          else { out += t; }
        } else if (tag === 'IMG') {
          const alt = (n.getAttribute('alt') || '').trim();
          if (alt) out += '[图片：' + alt + ']';   // 空 alt 多为装饰性图标，跳过降噪
        } else if (tag === 'BR') { out += '\n'; }
        else { out += renderInline(n); }
      }
    }
    return out;
  }
  function tableMd(t) {
    const rows = [];
    t.querySelectorAll('tr').forEach(tr => {
      const cells = [];
      tr.querySelectorAll('th,td').forEach(c => {
        const txt = renderInline(c).trim().replace(/\s+/g, ' ').replace(/\|/g, '\\|');
        cells.push(txt);
      });
      if (cells.length) rows.push(cells);
    });
    if (!rows.length) return '';
    const out = [];
    rows.forEach((row, i) => {
      out.push('| ' + row.join(' | ') + ' |');
      if (i === 0) out.push('| ' + rows[0].map(() => '---').join(' | ') + ' |');
    });
    return out.join('\n');
  }
  function walk(node) {
    const tag = node.tagName;
    const kids = Array.from(node.children);
    if (/^H[1-6]$/.test(tag)) { lines.push('#'.repeat(+tag[1]) + ' ' + node.textContent.trim().replace(/\n+/g, ' ')); }
    else if (tag === 'P') { const txt = renderInline(node).trim(); if (txt) lines.push(txt); }
    else if (tag === 'UL' || tag === 'OL') {
      const m = tag === 'UL' ? '- ' : '1. ';
      for (const li of node.children) { const t = renderInline(li).trim(); if (t) lines.push(m + t); }
    }
    else if (tag === 'TABLE') { const md = tableMd(node); if (md) lines.push(md); }
    else if (tag === 'IFRAME') {
      // WebPlus 常用 pdfjs 播放器嵌 PDF；把 PDF 地址标记出来，由 Python 侧抽正文或占位
      const src = node.getAttribute('src') || '';
      const m = src.match(/[?&]file=([^&]+)/);
      const raw = m ? decodeURIComponent(m[1]) : src;
      if (raw) { try { lines.push('@PDFIFRAME@ ' + new URL(raw, document.baseURI).href); } catch(e) {} }
    }
    else if (tag === 'IMG') { lines.push('[图片]'); }
    else if (tag === 'HR') { lines.push('---'); }
    else { for (const k of kids) walk(k); }
  }
  walk(el);
  return lines.join('\n');
}
"""


# ---------------------------------------------------------------- 元数据规则

_CN_HEAD = re.compile(r"^[一二三四五六七八九十]+、\s*\S")
_TITLE_REV = re.compile(r"[（(](\d{4})年(\d{1,2})月(\d{1,2})日(?:修订|更新)?[)）]")
_URL_DATE = re.compile(r"/(\d{4})/(\d{4})/")
_DATELESS_ENDINGS = ("。", "；", "：")


def clean_title(title: str) -> str:
    """去掉「（YYYY年M月D日修订/更新）」后缀并归一空白，保证 doc_id 跨版本稳定。"""
    cleaned = _TITLE_REV.sub("", title)
    return re.sub(r"\s+", " ", cleaned).strip()


def resolve_publish_date(title: str, url: str, rule: str) -> str:
    """发布日期解析链：标题修订日 > 详情 URL 日期 > unknown。绝不拿抓取日期冒充。"""
    if rule == "title_revision":
        m = _TITLE_REV.search(title)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = _URL_DATE.search(url)
    if m and m.group(2).isdigit():
        mm, dd = int(m.group(2)[:2]), int(m.group(2)[2:])
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            return f"{m.group(1)}-{mm:02d}-{dd:02d}"
    return "unknown"


def normalize_headings(text: str) -> str:
    """把「一、二、三」短行提升为 ## 标题；过长/带句读的行按正文保留。"""
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        if (_CN_HEAD.match(s) and len(s) <= 30
                and not s.endswith(_DATELESS_ENDINGS)
                and not s.startswith(("（", "(", "—", "1.", "注"))):
            out.append("## " + re.sub(r"^[一二三四五六七八九十]+、\s*", "", s))
        else:
            out.append(ln)
    return "\n".join(out)


def make_meta(source: dict, title: str, url: str, date: str) -> dict:
    defaults = source.get("defaults", {})
    return {
        "schema_version": 2,
        "doc_id": make_doc_id(title, url),
        "title": title,
        "source_url": url,
        "source_org": source["source_org"],
        "source_type": source["source_type"],
        "authority_level": source["authority_level"],
        "publish_date": date,
        "category": source["category"],
        "tags": list(defaults.get("tags", ["未分类"])),
        "valid": True,
        "review_status": "needs_review",
        "last_checked_at": None,
        "maintainer": source.get("maintainer", "unassigned"),
        "applies_to": list(defaults.get("applies_to", ["未明确"])),
        "campuses": list(defaults.get("campuses", ["未明确"])),
        "colleges": list(defaults.get("colleges", ["未明确"])),
        "effective_from": None,
        "effective_until": None,
        "supersedes": [],
        "superseded_by": [],
    }


def _safe_filename_part(text: str) -> str:
    """Windows 非法文件名字符（\\ / : * ? \" < > |）替换为下划线；标题里偶见（如"Nature | xxx"）。"""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip(" _")
    return cleaned or "doc"


def build_filename(meta: dict) -> str:
    year = meta["publish_date"][:4] if meta["publish_date"] != "unknown" else "未知"
    return f"{_safe_filename_part(meta['title'])}_{_safe_filename_part(meta['source_org'])}_{year}.md"


def build_body(title: str, md: str, source: dict, date: str) -> str:
    parts = [f"# {title}"]
    if source.get("publish_rule") == "title_revision" and date != "unknown":
        parts.append(f"> 修订日期：{date}。")
    elif date != "unknown":
        parts.append(f"> 页面发布日期：{date}（取自页面 URL）。")
    parts.append(md.strip())
    return "\n\n".join(parts)


class CrawlError(Exception):
    pass


_PDF_MARK = re.compile(r"^@PDFIFRAME@ (.+)$")


def extract_pdf_text(pdf_url: str) -> str | None:
    """尝试用 pypdf 抽 PDF 正文；无库 / 失败 / 扫描件返回 None。"""
    try:
        import httpx
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        resp = httpx.get(pdf_url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        reader = PdfReader(io.BytesIO(resp.content))
        text = "\n".join(p.extract_text() or "" for p in reader.pages)
        return text.strip() or None
    except Exception:
        return None


def _normalize_cookie(c: dict) -> dict | None:
    """Cookie-Editor 扩展导出格式 → Playwright add_cookies 格式；必填缺失返回 None。"""
    out: dict = {"name": str(c.get("name", "")), "value": str(c.get("value", ""))}
    if c.get("url"):
        out["url"] = c["url"]
    else:
        out["domain"] = c.get("domain", "")
        out["path"] = c.get("path", "/")
    expires = c.get("expirationDate") or c.get("expires")
    if expires:
        out["expires"] = int(expires)
    if "httpOnly" in c:
        out["httpOnly"] = bool(c["httpOnly"])
    if "secure" in c:
        out["secure"] = bool(c["secure"])
    ss = c.get("sameSite")
    if ss:
        out["sameSite"] = {"no_restriction": "None", "lax": "Lax", "strict": "Strict"}.get(
            str(ss).lower(), str(ss).capitalize())
    if not out.get("name") or not (out.get("domain") or out.get("url")):
        return None
    return out


# ---------------------------------------------------------------- 抓取

def load_sources(path: Path = SOURCES_FILE) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    sources = data.get("sources", [])
    bad = []
    for s in sources:
        # crawler: bst 来源由 scripts/bst_crawl.py（HTTP 采集）负责，不走 WebPlus 浏览器抓取
        if not s.get("enabled") or s.get("crawler") == "bst":
            continue
        missing = [k for k in REQUIRED_SOURCE_FIELDS if not s.get(k)]
        if missing:
            bad.append(f"{s.get('name')}: 缺 {missing}")
    if bad:
        raise SystemExit("sources.yaml 配置错误：\n  " + "\n  ".join(bad))
    return sources


def discover_detail_urls(page, source: dict) -> list[str]:
    """按 list_url 抓详情链接；可选 max_pages 翻页（WebPlus listN.htm 约定）。

    第 1 页为 list_url 本身，第 N 页为同目录 listN.htm（list.htm -> list2.htm）。
    某页抓不到详情链接即提前停止（已翻到底或该页结构异常），不报错。
    """
    max_pages = int(source.get("max_pages", 1))
    pat = re.compile(source["detail_url_pattern"])
    m = re.match(r"^(.*)[.](htm|psp)$", source["list_url"])
    paged_stem, paged_ext = (m.group(1), m.group(2)) if m else (None, None)
    urls: list[str] = []
    for page_no in range(1, max_pages + 1):
        if page_no == 1:
            list_url = source["list_url"]
        elif paged_stem:
            list_url = f"{paged_stem}{page_no}.{paged_ext}"
        else:
            break
        try:
            page.goto(list_url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            hrefs = page.eval_on_selector_all("a", "els => els.map(e => e.href || '')")
        except Exception as exc:
            print(f"  列表页 {list_url} 失败：{exc}")
            break
        page_urls = [h for h in hrefs if pat.match(h)]
        if not page_urls:
            break
        urls.extend(page_urls)
    return list(dict.fromkeys(urls))  # 去重保序


def crawl_detail(page, url: str, source: dict) -> tuple[str, str]:
    page.goto(url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    sel = source.get("content_selector")
    if not page.query_selector(sel):
        raise CrawlError(f"找不到正文容器 {sel}")
    md = page.eval_on_selector(sel, JS_MD)
    title = ""
    if source.get("title_selector"):
        title = page.eval_on_selector(
            source["title_selector"], "e => (e.innerText || '').trim()")
    if not title:
        title = page.title()
    title = title.strip()

    # WebPlus 政策正文常是 pdfjs iframe 嵌 PDF；抽正文，抽不动则写附件占位
    pdfs = _PDF_MARK.findall(md)
    if pdfs:
        inline = _PDF_MARK.sub("", md).strip()
        parts = [inline] if inline else []
        for pdf_url in pdfs:
            text = extract_pdf_text(pdf_url)
            if text:
                parts.append(text)
            else:
                parts.append(f"[附件：{title}.pdf]({pdf_url})\n\n> 本页正文为 PDF 附件，自动抽取失败，请打开原文查看。")
        md = "\n\n".join(parts)
    return title, md


def index_existing_kb() -> dict[str, str]:
    """doc_id -> 现有 KB 文件相对路径（用于暂存对比分类）。"""
    index: dict[str, str] = {}
    for path in KB_DIR.rglob("*.md"):
        try:
            post = frontmatter.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        did = str(post.get("doc_id", ""))
        if did and did not in index:
            index[did] = path.relative_to(KB_DIR).as_posix()
    return index


def existing_content(rel: str) -> str:
    """现有 KB 文档的正文（用于对比是否一致）。"""
    try:
        return frontmatter.loads((KB_DIR / rel).read_text(encoding="utf-8")).content
    except Exception:
        return ""


def crawl_source(page, source: dict, limit: int, dry_run: bool,
                 existing_index: dict[str, str]) -> dict:
    """抓取一个来源，返回 {manifest, 摘要文本}。"""
    staging = STAGING_DIR / source["name"]
    staging.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict] = {}
    errors: list[str] = []

    if source.get("detail_url_pattern"):
        try:
            urls = discover_detail_urls(page, source)
        except Exception as exc:
            return {"results": results, "errors": [f"列表页失败：{exc}"],
                    "summary": f"{source['name']}: 列表页失败"}
        if limit:
            urls = urls[:limit]
    else:
        urls = source.get("urls", [])

    for url in urls:
        try:
            raw_title, md = crawl_detail(page, url, source)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            continue
        date = resolve_publish_date(raw_title, url, source.get("publish_rule", "url_date"))
        title = clean_title(raw_title)
        body = build_body(title, md, source, date)
        if source.get("normalize_headings"):
            body = normalize_headings(body)
        meta = make_meta(source, title, url, date)
        result = validate_metadata(meta)
        rel = f"{source['target_dir']}/{build_filename(meta)}"
        if result.errors:
            errors.append(f"{url}: schema 校验失败：{'；'.join(result.errors)}")
            continue

        existing_rel = existing_index.get(meta["doc_id"])
        if existing_rel is None:
            status = "新增"
        elif existing_content(existing_rel) == body:
            status = "一致"
        else:
            status = "更新"

        results[rel] = {
            "status": status, "doc_id": meta["doc_id"], "title": title,
            "publish_date": date, "chars": len(body),
            "existing": existing_rel,
        }
        if not dry_run:
            (staging / rel).parent.mkdir(parents=True, exist_ok=True)
            post = frontmatter.Post(body, **meta)
            (staging / rel).write_text(frontmatter.dumps(post), encoding="utf-8")

    if not dry_run:
        # 清理本次未出现的旧暂存文件（应对来源侧删稿）。统一用 posix 路径比对；
        # 本轮 0 成功时不清理——站点故障时保留上次暂存，避免误删旧审核材料。
        if results:
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

    summary = _format_summary(source["name"], results, errors, dry_run)
    return {"results": results, "errors": errors, "summary": summary}


def _format_summary(name: str, results: dict, errors: list[str], dry_run: bool) -> str:
    lines = [f"== {name}（{'预演' if dry_run else '抓取'}）: {len(results)} 篇"]
    for rel, r in sorted(results.items()):
        lines.append(f"  [{r['status']}] {r['title']}（{r['publish_date']}）→ {rel}")
    for e in errors:
        lines.append(f"  [错误] {e}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 覆盖检查
# 目的：枚举站点首页导航的全部栏目，与 sources.yaml 已注册的 detail_url_pattern
# 比对，把「漏注册栏目」显性化，避免像 ckc 二次选拔那样整栏未收录还不自知。

_NAV_LIST_RE = re.compile(r"list[.](htm|psp)$")              # WebPlus 栏目入口
_DETAIL_C_NUM = re.compile(r"/c([0-9]+)a[0-9]+/page[.]htm$")  # 详情 URL 里的栏目 c 号
_REG_C_NUM = re.compile(r"c([0-9]+)a")


def _site_netloc(url: str) -> str:
    return urlparse(url).netloc.lower()


def _same_site(href: str, netloc: str) -> bool:
    # 各栏目列表页都带综合服务网「更多」链接（跨域 c 号），不按 netloc 过滤会污染 c 号集合
    return urlparse(href).netloc.lower() == netloc


def _registered_columns(sources: list[dict], base_url: str) -> tuple[set[str], bool, list[dict]]:
    """该站点已注册的 c 号集合、是否含通用规则、站点内来源条目。"""
    netloc = _site_netloc(base_url)
    specific: set[str] = set()
    site_sources: list[dict] = []
    generic = False
    for s in sources:
        if not s.get("enabled") or not s.get("base_url"):
            continue
        if _site_netloc(str(s["base_url"])) != netloc:
            continue
        site_sources.append(s)
        pat = s.get("detail_url_pattern") or ""
        m = _REG_C_NUM.search(pat)
        if m and m.group(1).isdigit():
            specific.add(m.group(1))
        elif pat:
            generic = True  # 形如 c\d+a\d+ 的通用规则：视为该站点整站覆盖
    return specific, generic, site_sources


def _nav_columns(page, netloc: str) -> list[dict]:
    """站点首页导航里同站的 list.htm / list.psp 栏目链接（标题、地址），去重按标题排序。"""
    links = page.eval_on_selector_all(
        "a",
        "els => els.map(e => ({t:(e.innerText||'').trim(), h:e.href||''})).filter(x => x.t)",
    )
    seen: set[str] = set()
    cols: list[dict] = []
    for ln in links:
        href = ln["h"]
        if not _same_site(href, netloc) or not _NAV_LIST_RE.search(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        cols.append({"title": ln["t"], "href": href})
    cols.sort(key=lambda c: c["title"])
    return cols


def _column_cnums(page, netloc: str) -> set[str]:
    """当前列表页里同站全部详情链接的栏目 c 号集合（WebPlus 一个栏目一个 c 号）。"""
    hrefs = page.eval_on_selector_all("a", "els => els.map(e => e.href || '')")
    return {m.group(1) for h in hrefs
            if _same_site(h, netloc) if (m := _DETAIL_C_NUM.search(h))}


def check_coverage(page, base_url: str, sources: list[dict]) -> dict:
    """对一个站点做覆盖检查：导航栏目逐个采样 c 号与注册比对，再核验每个注册来源。"""
    netloc = _site_netloc(base_url)
    specific, generic, site_sources = _registered_columns(sources, base_url)

    page.goto(base_url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    columns: list[dict] = []
    seen_cnums: set[str] = set()
    for col in _nav_columns(page, netloc):
        try:
            page.goto(col["href"], timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            cnums = _column_cnums(page, netloc)
        except Exception as exc:
            columns.append({**col, "status": "failed", "note": str(exc)[:80]})
            continue
        seen_cnums |= cnums
        if generic:
            status, note = "covered", "通用规则（整站覆盖）"
        elif not cnums:
            status, note = "no_details", "列表页无详情链接"
        elif cnums <= specific:
            status, note = "covered", "c号 " + " ".join(sorted(cnums))
        else:
            status, note = "uncovered", "漏注册 c号：" + " ".join(sorted(cnums - specific))
        columns.append({**col, "status": status, "note": note, "cnums": sorted(cnums)})

    verified: list[dict] = []
    verified_cnums: set[str] = set()
    for s in site_sources:
        name = s["name"]
        exp = _REG_C_NUM.search(s.get("detail_url_pattern") or "")
        if not s.get("list_url"):
            verified.append({"name": name, "status": "skipped", "note": "未配 list_url"})
            continue
        try:
            page.goto(s["list_url"], timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            cnums = _column_cnums(page, netloc)
        except Exception as exc:
            verified.append({"name": name, "status": "failed", "note": str(exc)[:80]})
            continue
        if exp and exp.group(1).isdigit():
            if cnums and exp.group(1) in cnums:
                verified_cnums.add(exp.group(1))
                verified.append({"name": name, "status": "ok", "note": "校验 c" + exp.group(1)})
            elif cnums:
                verified.append({"name": name, "status": "mismatch", "note": "列表详情 c号 " + " ".join(sorted(cnums)) + "，预期 c" + exp.group(1)})
            else:
                verified.append({"name": name, "status": "empty", "note": "预期 c" + exp.group(1) + "，列表无详情链接"})
        else:
            note = "通用规则，采样 " + " ".join(sorted(cnums)) if cnums else "通用规则，列表无详情链接"
            verified.append({"name": name, "status": "ok", "note": note})

    missing = sorted(specific - seen_cnums - verified_cnums)
    return {
        "base_url": base_url, "netloc": netloc, "generic": generic,
        "specific": sorted(specific), "columns": columns,
        "verified": verified, "missing": missing,
    }


def _coverage_report(data: dict) -> str:
    head = "== 覆盖检查：" + data["netloc"]
    if data["generic"]:
        head += "（通用规则，整站覆盖）"
    else:
        head += "（注册 c号：" + (" ".join(data["specific"]) if data["specific"] else "无") + "）"
    lines = [head]
    lines.append("   导航栏目 " + str(len(data["columns"])) + " 个：")
    for c in data["columns"]:
        lines.append("   [" + c["status"] + "] " + c["title"] + " — " + c["note"])
    lines.append("   已注册来源 " + str(len(data["verified"])) + " 个：")
    for v in data["verified"]:
        lines.append("   [" + v["status"] + "] " + v["name"] + " — " + v["note"])
    if data["missing"]:
        lines.append("   ⚠ 注册了却在导航和列表页都看不到的 c号：" + " ".join(data["missing"]))
    else:
        lines.append("   未发现「注册了却消失」的 c号。")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="知识库来源采集器")
    ap.add_argument("--source", help="只抓指定来源（sources.yaml 的 name）")
    ap.add_argument("--all", action="store_true", help="抓全部 enabled 来源")
    ap.add_argument("--dry-run", action="store_true", help="预演，不写暂存区")
    ap.add_argument("--limit", type=int, default=0, help="每来源最多抓多少详情页")
    ap.add_argument("--headed", action="store_true",
                    help="有头模式（SSO 登录用；默认 headless）")
    ap.add_argument("--profile", default=str(ROOT / "data" / "kb_crawl_profile"),
                    help="持久化浏览器 profile 目录（存 SSO cookie；data/ 下不进 git）")
    ap.add_argument("--login", metavar="URL",
                    help="SSO 登录：有头打开 URL，手动登录后 cookie 存进 profile，之后可 headless 复用")
    ap.add_argument("--channel", default="",
                    help="浏览器通道：chromium(默认)/msedge/chrome。有头登录 GUI 起不来时用 --channel msedge（系统 Edge）")
    ap.add_argument("--import-cookies", metavar="FILE",
                    help="从 JSON 文件导入 cookie（Cookie-Editor 扩展导出格式），注入 profile 后退出；绕过 SSO 反自动化登录")
    ap.add_argument("--check-coverage", action="store_true",
                    help="覆盖检查：枚举站点导航栏目并与 sources.yaml 注册比对，暴露漏注册栏目")
    ap.add_argument("--site", metavar="URL", default="",
                    help="覆盖检查范围：只查该 base_url；缺省检查全部已注册站点")
    args = ap.parse_args()

    existing_index = index_existing_kb()
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # --login 必须用有头浏览器（用户要看到并操作登录框）
        headless = not args.headed and not args.login
        # 降低被 SSO 反自动化检测拖慢/挂起的概率（登录页对自动化浏览器常见）
        context = p.chromium.launch_persistent_context(
            args.profile, headless=headless, channel=args.channel or None,
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"])
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        if args.import_cookies:
            with open(args.import_cookies, encoding="utf-8") as f:
                raw = json.load(f)
            cookies = [c for c in (_normalize_cookie(c) for c in raw) if c]
            context.add_cookies(cookies)
            context.close()
            print(f"已导入 {len(cookies)}/{len(raw)} 条 cookie 到 {args.profile}，可 headless 抓取")
            return

        if args.login:
            page = context.new_page()
            page.goto(args.login, timeout=60000, wait_until="domcontentloaded")
            print(f"请在打开的浏览器里完成登录（{args.login}），完成后回到此终端按回车…")
            input()
            print("登录完成，cookie 已保存到 profile。")
            context.close()
            return

        sources = load_sources()
        if args.check_coverage:
            bases = [args.site] if args.site else sorted(
                {str(s["base_url"]) for s in sources
                 if s.get("enabled") and s.get("base_url")})
            page = context.new_page()
            for base in bases:
                print(_coverage_report(check_coverage(page, base, sources)))
                print()
            context.close()
            return
        if args.source:
            sources = [s for s in sources if s["name"] == args.source]
        elif args.all:
            sources = [s for s in sources if s.get("enabled")]
        else:
            ap.error("必须指定 --source <name> 或 --all")

        page = context.new_page()
        for source in sources:
            out = crawl_source(page, source, args.limit, args.dry_run, existing_index)
            print(out["summary"])
        context.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):  # Windows 控制台默认 GBK，中文会乱码
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    main()



