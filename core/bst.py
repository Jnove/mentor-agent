"""浙大百事通检索（s.zju.edu.cn）通道：HTML 解析器 + 运行时实时检索。

s.zju.edu.cn 是全校统一检索平台，接口均为公开无登录的搜索端点
（服务端渲染 HTML，UTF-8）：
    GET /search/faq.do?words=&pageNo=&pageSize=   常见问题（约 2409 条，Q/A/电话/部门）
    GET /search/workGuide.do?words=...            办事指南（约 669 条）
    GET /search/normFile.do?words=...             规范性文件（约 747 条）
    GET /search/normFileView.do?docNo=N           规范性文件信息页（全文在 PDF）
    /tool/viewpdf.do?dataId=N&sourceType=norm_file 规范性文件 PDF
    GET /search/info.do?words=...                 网页资讯（全校网站索引：标题/摘要/链接/时间）
    GET /search/all.do?words=...                  聚合页（资讯+FAQ+办事指南+规范性文件）

离线快照采集（scripts/bst_crawl.py）与运行时兜底检索（ui/chat_page.py）共用本模块解析器。
"""
from __future__ import annotations

import re
import urllib.parse

import httpx

BASE_URL = "https://s.zju.edu.cn"
_UA = {"User-Agent": "Mozilla/5.0 (mentor-agent)"}

# 直连，不信系统代理（Clash 等）：
# 1) urllib 在本机环境（Clash TUN fake-ip + 系统代理）下长连接收尾会挂起且 timeout 失效，
#    实测单页假死 120s+；httpx 按 Content-Length/chunked 正常收包，稳定。
# 2) 学校站点公网直连又快又稳，不走代理还能避开代理出口限流。
_HTTPX = httpx.Client(trust_env=False, follow_redirects=True)


def fetch(url: str, timeout: float = 15) -> bytes | None:
    """GET 一个页面；网络/HTTP 异常一律返回 None（调用方自行降级）。"""
    try:
        resp = _HTTPX.get(url, headers=_UA, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    except Exception:
        return None


# 来源分级：百事通是校级平台转发的各处室内容，权威性按发布单位走，登记为 university
SOURCE_ORG = "浙江大学"
PAGE_SIZE = 30  # 站点最大每页条数（空搜索枚举时用，减请求数）

_RE_STRIP_TAG = re.compile(r"<[^>]+>")
_RE_FONT = re.compile(r"<font[^>]*>|</font>")
_RE_WS = re.compile(r"\s+")


def _clean(html_frag: str) -> str:
    """去 HTML 标签/关键词高亮，归一空白。"""
    text = _RE_FONT.sub("", html_frag or "")
    text = _RE_STRIP_TAG.sub("", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return _RE_WS.sub(" ", text).strip()


def _page(kind: str, query: str = "", page: int = 1, size: int = PAGE_SIZE) -> bytes | None:
    path = f"/search/{kind}.do"
    qs = urllib.parse.urlencode(
        {"words": query, "pageNo": page, "pageSize": size})
    return fetch(f"{BASE_URL}{path}?{qs}")


def parse_total(html: str) -> int:
    """「约有 <span>2409</span> 项符合的查询结果」。解析失败返回 0。"""
    m = re.search(r"约有\s*<span>\s*(\d+)", html)
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------- FAQ

_FAQ_SPLIT = re.compile(r"<em>问</em>")
_FAQ_Q = re.compile(r'<div class="ts">(.*?)</div>', re.S)
_FAQ_A = re.compile(r"docNo='(\d+)'[^>]*class=\"da_high_content\"[^>]*>(.*?)</h4>", re.S)
_FAQ_FIELD = re.compile(r"<b>([^<]{1,8}?)[：:]\s*</b>\s*</td>\s*<td>([^<]*)</td>", re.S)
_HIDE_BTN = re.compile(r'<div class="(?:expand-btn|close-btn)"[^>]*>.*?</div>', re.S)


def parse_faq_page(html: str) -> list[dict]:
    """解析 FAQ 列表页 → [{doc_no, question, answer, phone, dept, office}]。

    按「问」标记分块，块内独立提取各字段——个别条目缺科室/部门字段不影响整条；
    答案在 <h4 class="da_high_content"> 里，含「展开/收起」按钮节点需剔除；
    答案过长时站点本身会截断（列表页无全文），接受截断结果。
    """
    items: list[dict] = []
    starts = [m.start() for m in _FAQ_SPLIT.finditer(html)]
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else start + 6000
        seg = html[start:end]
        qm = _FAQ_Q.search(seg)
        if not qm:
            continue
        question = _clean(qm.group(1))
        if not question:
            continue
        am = _FAQ_A.search(seg)
        fields = {k: _clean(v) for k, v in _FAQ_FIELD.findall(seg)}
        items.append({
            "doc_no": am.group(1) if am else "",
            "question": question,
            "answer": _clean(_HIDE_BTN.sub("", am.group(2))) if am else "",
            "phone": fields.get("咨询电话", ""),
            "dept": fields.get("受理部门", ""),
            "office": fields.get("科 室", ""),
        })
    return items


# ---------------------------------------------------------------- 办事指南

_WG_TITLE = re.compile(r'id="data_[A-Z0-9]+">(.*?)</div>', re.S)
_WG_FIELD = re.compile(r"<b>([^<]{1,12}?)[：:]\s*</b>([^<]*)</td>", re.S)
_WG_TEL = re.compile(r"咨询电话:</b>\s*([^<\s]+)", re.S)
# 站点多后端模板差异：部分页面标题后跟「点击详情」等链接文本，污染标题导致同名撞车
_WG_TITLE_NOISE = re.compile(r"(?:点击|查看)?(?:详情|更多)\s*$")


def parse_work_guide_page(html: str) -> list[dict]:
    """解析办事指南列表页 → [{code, title, fields:{键:值}, phone}]。

    每条事项是一张表：标题在 id="data_XXX" 的 div，其余为 <b>键:</b>值 键值对
    （受理时间/咨询电话/监督电话/受理机构/受理地方 等，字段不固定，缺失即无）。
    标题尾部可能带「点击详情」链接文本（站点模板差异），需剔除防同名撞车。
    """
    items: list[dict] = []
    # 按标题 div 切块，块内解析键值对
    for m in _WG_TITLE.finditer(html):
        seg = html[m.start(): m.start() + 4000]
        fields = {}
        for fm in _WG_FIELD.finditer(seg):
            key, val = _clean(fm.group(1)), _clean(fm.group(2))
            if key and val:
                fields.setdefault(key, val)
        tel = _WG_TEL.search(seg)
        title = _WG_TITLE_NOISE.sub("", _clean(m.group(1))).strip()
        if not title:
            continue
        items.append({
            "code": "",
            "title": title,
            "fields": fields,
            "phone": tel.group(1).strip() if tel else fields.get("咨询电话", ""),
        })
    return items


# ---------------------------------------------------------------- 规范性文件

_NF_BLOCK = re.compile(
    r'<a[^>]*id="norm_file_(\d+)"[^>]*title="([^"]*)"[^>]*>(.*?)</a>.*?'
    r"<b>【发文号】</b>\s*([^|]*)"
    r".*?<b>【施行日期】</b>\s*([^|]*)",
    re.S,
)


def parse_norm_file_page(html: str) -> list[dict]:
    """解析规范性文件列表页 → [{doc_no, title, issue_no, effective_date, download_url, view_url}]。"""
    items: list[dict] = []
    for m in _NF_BLOCK.finditer(html):
        doc_no = m.group(1)
        title = _clean(m.group(3))
        if not title:
            title = _clean(m.group(2))
        items.append({
            "doc_no": doc_no,
            "title": title,
            "issue_no": _clean(m.group(4)),
            "effective_date": _clean(m.group(5)),
            "view_url": f"{BASE_URL}/search/normFileView.do?docNo={doc_no}",
            "download_url": f"{BASE_URL}/tool/downNormFile.do?docNo={doc_no}",
        })
    return items


_NF_META = re.compile(r"<span\s*>【([^】]+)】</span>([^<]+)", re.S)


def parse_norm_file_detail(html: str) -> dict:
    """解析规范性文件信息页右侧的文档基本信息（拟文部门/发文号/印发日期/时效性/分类）。"""
    meta: dict[str, str] = {}
    for m in _NF_META.finditer(html):
        meta[_clean(m.group(1))] = _clean(m.group(2))
    return meta


def norm_pdf_url(doc_no: str) -> str:
    return f"{BASE_URL}/tool/viewpdf.do?dataId={doc_no}&sourceType=norm_file&fileType=pdf"


# ---------------------------------------------------------------- 网页资讯

_INFO_BLOCK = re.compile(
    r'<a href="([^"]+)"[^>]*dataSource[^>]*>(.*?)</a>.*?'
    r'class="limit-lines3">(.*?)</div>.*?'
    r"发布时间：\s*([0-9-]+)\s*\| 信息来源：\s*([^<]*)",
    re.S,
)


def parse_info_page(html: str) -> list[dict]:
    """解析网页资讯列表页 → [{title, url, snippet, publish_date, source_org}]。

    info 通道是全校网站索引（bksy/zhfw/各学院…），返回真实原文链接，
    实时性强，适合作为知识库未命中的兜底。
    """
    items: list[dict] = []
    for m in _INFO_BLOCK.finditer(html):
        url = m.group(1).strip()
        title = _clean(m.group(2))
        if not title or not url.startswith("http"):
            continue
        items.append({
            "title": title,
            "url": url,
            "snippet": _clean(m.group(3)),
            "publish_date": m.group(4).strip(),
            "source_org": _clean(m.group(5)),
        })
    return items


# ---------------------------------------------------------------- 运行时检索

import time as _time

# 实时兜底节流：单用户对话频率低，主要防 UI 连点/重试风暴；
# 学校站点对低频请求无限制（实测 1s 间隔 20+ 连发全秒回），2s 足够。
BST_MIN_INTERVAL = 2.0
_last_call: float = 0.0
_cache: dict[str, list[dict]] = {}


def _throttled(query: str, top_n: int) -> list[dict] | None:
    """满足节流条件才放行；返回 None 表示应使用缓存。"""
    global _last_call
    now = _time.monotonic()
    if now - _last_call < BST_MIN_INTERVAL:
        return _cache.get(query)
    _last_call = now
    return None


def _to_hit(item: dict, kind: str) -> dict:
    """把解析条目转成与 RAG 检索结果兼容的 dict（build_context/UI 直接可用）。"""
    if kind == "faq":
        text = item["answer"]
        if item["phone"]:
            text += f"\n咨询电话：{item['phone']}"
        if item["dept"]:
            text += f"\n受理部门：{item['dept']}"
        if item["office"]:
            text += f"\n科室：{item['office']}"
        return {
            "id": f"bst-faq-{item['doc_no']}",
            "title": item["question"],
            "text": text,
            "source_url": f"{BASE_URL}/search/faq.do?words="
                          + urllib.parse.quote(item["question"]),
            "source_org": SOURCE_ORG,
            "publish_date": "unknown",
            "score": 1.0,
            "from_bst": "常见问题",
        }
    if kind == "info":
        return {
            "id": f"bst-info-{item['url'][-64:]}",
            "title": item["title"],
            "text": item["snippet"] or item["title"],
            "source_url": item["url"],
            "source_org": item["source_org"] or SOURCE_ORG,
            "publish_date": item["publish_date"],
            "score": 1.0,
            "from_bst": "校园资讯",
        }
    raise ValueError(kind)


def search_faq(query: str, top_n: int = 5) -> list[dict]:
    html = _page("faq", query, size=top_n)
    if html is None:
        return []
    try:
        items = parse_faq_page(html.decode("utf-8", errors="replace"))
    except Exception:
        return []
    return [_to_hit(it, "faq") for it in items[:top_n]]


def search_info(query: str, top_n: int = 5) -> list[dict]:
    html = _page("info", query, size=top_n)
    if html is None:
        return []
    try:
        items = parse_info_page(html.decode("utf-8", errors="replace"))
    except Exception:
        return []
    return [_to_hit(it, "info") for it in items[:top_n]]


def bst_search(query: str, top_n: int = 4) -> list[dict]:
    """实时兜底检索：FAQ 优先，不足部分用网页资讯补齐。

    调用方在 RAG 无命中/低分命中时触发；网络失败或解析失败返回 []（不影响主链路）。
    返回条目与 RAG hits 结构兼容，可原样并入 build_context。
    受站点限流约束：调用间隔不足时返回缓存（同一 query 命中缓存，不同 query 返回 []）。
    """
    cached = _throttled(query, top_n)
    if cached is not None:
        return cached
    hits = search_faq(query, top_n=top_n)
    if len(hits) < top_n:
        hits += search_info(query, top_n=top_n - len(hits))
    seen: set[str] = set()
    unique: list[dict] = []
    for h in hits:
        key = h.get("source_url") or h.get("id")
        if key and key in seen:
            continue
        seen.add(key)
        unique.append(h)
        if len(unique) >= top_n:
            break
    result = unique[:top_n]
    if len(_cache) > 100:
        _cache.pop(next(iter(_cache)))  # 简单 LRU：满了丢最早一条
    _cache[query] = result
    return result
