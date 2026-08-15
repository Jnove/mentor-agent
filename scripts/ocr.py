"""图片 OCR 提取：把暂存 markdown 里的 ![图片](url) 下载并 OCR，补充文字。

配套 kb_crawl.py 的 JS_MD 改动 A（图片输出带绝对 URL）。由 kb_crawl.py 内部调用，
不提供独立 CLI。依赖 rapidocr_onnxruntime（未安装时自动降级为保留原占位）。
"""
from __future__ import annotations

import base64
import re

_IMG_RE = re.compile(r"!\[图片(?:：([^\]]*))?\]\(([^)]+)\)")
_OCR_NOTE = "> 图片OCR：RapidOCR 自动识别，未经人工核对。"
_MIN_EDGE = 80  # 边长小于此像素的图（图标/装饰）跳过 OCR

# 会话内 fetch → base64：复用浏览器会话与 cookie，绕 ckc 等站点 WAF/防盗链
_FETCH_JS = r"""
async (u) => {
  try {
    const r = await fetch(u, {credentials: 'include'});
    if (!r.ok) return null;
    const b = await r.arrayBuffer();
    const u8 = new Uint8Array(b);
    let bin = '';
    const CHUNK = 0x8000;
    for (let i = 0; i < u8.length; i += CHUNK) {
      bin += String.fromCharCode.apply(null, u8.subarray(i, i + CHUNK));
    }
    return btoa(bin);
  } catch (e) { return null; }
}
"""


def make_downloader(page):
    """返回 download(url) -> bytes | None。走 Playwright 会话内 fetch（绕 WAF），
    绝不裸 httpx/curl。"""
    def download(url: str) -> bytes | None:
        try:
            b64 = page.evaluate(_FETCH_JS, url)
        except Exception:
            return None
        if not b64:
            return None
        try:
            return base64.b64decode(b64)
        except Exception:
            return None
    return download


def _engine():
    """惰性加载 RapidOCR 引擎；缺失/初始化失败返回 None（不打断抓取，保留原文）。

    优先新版统一包 rapidocr（3.x），退回旧版 rapidocr_onnxruntime（1.x）。
    """
    try:
        from rapidocr import RapidOCR
        return RapidOCR()
    except Exception:
        pass
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception:
        return None


def _normalize(result) -> list:
    """新旧引擎输出 → 统一 [{box, text, score}] 列表。

    新包 rapidocr 3.x：RapidOCROutput（.txts/.boxes/.scores）；
    旧包 rapidocr_onnxruntime 1.x：返回 (result, elapse)，result 为 [box, text, score] 列表或 None。
    """
    if result is None:
        return []
    if not isinstance(result, tuple):
        # rapidocr 3.x 结构化对象
        try:
            out = []
            for box, txt, score in zip(result.boxes, result.txts, result.scores):
                out.append([box, txt, score])
            return out
        except Exception:
            return []
    items = result[0]
    if not items:
        return []
    return list(items)


def _parse_items(items) -> list[tuple[float, float, float, str]]:
    """RapidOCR items → [(y_c, x_c, h, text)]；低置信度/空文本过滤。"""
    out: list[tuple[float, float, float, str]] = []
    for it in items:
        try:
            box, text, score = it[0], it[1], it[2]
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except Exception:
            continue
        text = str(text).strip()
        if not text:
            continue
        try:
            if float(score) < 0.5:
                continue
        except Exception:
            pass
        out.append(((min(ys) + max(ys)) / 2, (min(xs) + max(xs)) / 2, max(ys) - min(ys), text))
    return out


def _cluster_rows(blocks: list[tuple[float, float, float, str]]) -> list[list[tuple[float, float, float, str]]]:
    """按 y 中心聚类成行：相邻块 y 差 < 中位块高 × 0.75 视为同一行。"""
    if not blocks:
        return []
    blocks = sorted(blocks, key=lambda b: b[0])
    heights = sorted(b[2] for b in blocks)
    med_h = heights[len(heights) // 2]
    threshold = max(8.0, med_h * 0.75)
    rows: list[list] = []
    cur = [blocks[0]]
    for b in blocks[1:]:
        if b[0] - cur[-1][0] <= threshold:
            cur.append(b)
        else:
            rows.append(cur)
            cur = [b]
    rows.append(cur)
    return rows


def _format_rows(rows) -> list[str]:
    """行内按 x 排序；一行 ≥2 个文本块 → markdown 表格行，否则段落行。"""
    out: list[str] = []
    for row in rows:
        row = sorted(row, key=lambda b: b[1])
        texts = [b[3].replace("\n", " ").strip() for b in row if b[3].strip()]
        if len(texts) >= 2:
            cells = [t.replace("|", "\\|") for t in texts]
            out.append("| " + " | ".join(cells) + " |")
        elif len(texts) == 1:
            out.append(texts[0])
    return out


def ocr_markdown_images(md: str, download) -> str:
    """把 md 里的 ![图片](url) 逐张下载+OCR，在图片标记后追加识别结果；
    下载失败/尺寸过小/无文本 → 保留原占位不动。"""
    if not _IMG_RE.search(md):
        return md
    engine = _engine()
    if engine is None:
        return md

    def replace(match: re.Match) -> str:
        _, url = match.group(1), match.group(2)
        img = download(url)
        if not img:
            return match.group(0)
        try:
            import cv2
            import numpy as np
            decoded = cv2.imdecode(np.frombuffer(img, dtype=np.uint8), cv2.IMREAD_COLOR)
            if decoded is None or min(decoded.shape[:2]) < _MIN_EDGE:
                return match.group(0)
            res = engine(decoded)
        except Exception:
            return match.group(0)
        items = _normalize(res)
        rows = _format_rows(_cluster_rows(_parse_items(items)))
        if not rows:
            return match.group(0)
        lines = [_OCR_NOTE] + ["> " + ln if ln else ">" for ln in rows]
        return match.group(0) + "\n" + "\n".join(lines)

    return _IMG_RE.sub(replace, md)
