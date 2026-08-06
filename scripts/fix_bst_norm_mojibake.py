"""修复百事通规范性文件 PDF 抽取乱码（knowledge_base/政策/百事通/）。

乱码两种来源：
1. PDF 字体 CID 错乱：GBK 字节被按 EUC-KR 解码成韩文音节（如 훐릲헣붭 = 中共浙江），
   可逆：韩文音节 → EUC-KR 字节 → GBK 解码还原。
2. 无字形占位：控制字符/小语种字母（老挝文、缅甸文、彝文等），原始字节已丢失，
   只能删除（多出现在印章/空白区域）。

误报排除：﹝﹞○△℃×±≈≤≥ 等是正常中文标点/符号，保留。

用法: python scripts/fix_bst_norm_mojibake.py [--apply]
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

import frontmatter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import KB_DIR  # noqa: E402

TARGET = KB_DIR / "政策" / "百事通"

# 允许保留的字符范围（白名单之外一律删除——PDF 字体 CID 错乱的产物不可恢复）：
#   CJK 汉字/扩展、CJK 标点、全角（排除半角假名 FF66-FF9F）、ASCII、通用标点、
#   字母符号（℃）、罗马数字、箭头、数学符号（∑）、带圈数字、几何符号（●○□）、
#   CJK 兼容标点及小标点变体（︰︵︶﹝﹞）、常用 Latin-1 符号（°±²³·×÷）
_OK = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff"
    r"\u3000-\u303f"
    r"\uff00-\uff65\uffa0-\uffef"
    r"\u0020-\u007e"
    r"\u2000-\u206f"
    r"\u2100-\u218f"
    r"\u2190-\u21ff"
    r"\u2200-\u22ff"
    r"\u2460-\u24ff"
    r"\u25a0-\u25ff"
    r"\ufe30-\ufe6f"
    r"\u00b0\u00b1\u00b2\u00b3\u00b7\u00d7\u00f7"
    r"]"
)
_HANGUL = re.compile(r"[\uac00-\ud7a3\ud7b0-\ud7ff]")  # 韩文音节（GBK→EUC-KR 错乱的特征）


def fix_hangul(text: str) -> tuple[str, int]:
    """韩文音节反转回 GBK 字节再解码；返回 (替换后文本, 恢复字数)。

    浙大文件里出现完整韩文音节 = PDF 把 GBK 双字节按 EUC-KR 解码的错乱，
    逐音节 encode('euc-kr') 取回原始 GBK 字节再解码即还原；无法还原的置替换符。
    """
    out: list[str] = []
    n = 0
    for ch in text:
        if _HANGUL.match(ch):
            try:
                b = ch.encode("euc-kr")
                dec = b.decode("gbk", errors="strict")
                if len(dec) == 1 and "\u4e00" <= dec <= "\u9fff":
                    out.append(dec)
                    n += 1
                    continue
            except Exception:
                pass
            out.append("\ufffd")  # 无法还原的韩文音节置替换符，随后删除
        else:
            out.append(ch)
    return "".join(out), n


def clean_text(text: str) -> tuple[str, dict]:
    """返回 (清洗后文本, 统计)。

    注意：早期"韩文音节反转恢复"方案已被废弃——PDF 的 CMap 把 GBK 字节映射到
    任意韩文码位（不遵循 EUC-KR），反转会引入错误汉字（如"妇藕泣都"）。
    规范性文件乱码的正确修复路径见 scripts/refetch_norm_pdf_body.py
    （重新 pypdf 抽取 + 本函数白名单清洗）。
    """
    stats = {"hangul_restored": 0, "deleted": 0}
    cleaned = "".join(
        ch for ch in text
        if _OK.match(ch) or ch in "\r\n\t"
    )
    stats["deleted"] = sum(1 for ch in text if not _OK.match(ch) and ch not in "\r\n\t")
    return cleaned, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="写回知识库文件（默认只报告）")
    args = ap.parse_args()

    files = sorted(TARGET.rglob("*.md"))
    report: list[tuple[Path, dict, str, str]] = []
    for path in files:
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        bad = [c for c in post.content if not _OK.match(c) and c not in "\r\n\t"]
        if not bad:
            continue
        cleaned, stats = clean_text(post.content)
        still_bad = [c for c in cleaned if not _OK.match(c) and c not in "\r\n\t"]
        report.append((path, stats, "".join(sorted(set(bad)))[:40],
                       "".join(sorted(set(still_bad)))[:40]))

    if not report:
        print("没有发现乱码文档。")
        return 0

    print(f"含乱码文档 {len(report)}/{len(files)}：")
    total = {"hangul_restored": 0, "deleted": 0}
    for path, stats, before, after in report:
        for k, v in stats.items():
            total[k] += v
        flag = "✓" if not after else "⚠ 残留"
        print(f"  [{flag}] {path.name[:38]} 恢复{stats['hangul_restored']} "
              f"删除{stats['deleted']} 残留:{after or '无'}")
    print(f"\n合计：恢复 {total['hangul_restored']} 字，删除 {total['deleted']} 个乱码字符")

    if not args.apply:
        print("\n（--apply 写回知识库）")
        return 0

    for path, stats, _, still in report:
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        cleaned, _ = clean_text(post.content)
        post.content = cleaned
        path.write_text(frontmatter.dumps(post), encoding="utf-8", newline="\n")
    print(f"已写回 {len(report)} 篇。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    raise SystemExit(main())
