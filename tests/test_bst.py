"""core.bst 解析器测试：离线 HTML 夹具驱动，不依赖网络。

用法: python tests/test_bst.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.bst import (
    parse_faq_page, parse_info_page, parse_norm_file_detail,
    parse_norm_file_page, parse_total, parse_work_guide_page,
)

FAQ_PAGE = """
<div class="txts_result"><h3>约有 <span>2409</span> 项符合的查询结果</h3></div>
<dd class="D3"><table><tbody>
  <tr><td colspan="2"><div class="T1"><em>问</em> <div class="ts">如何补办校园卡？</div> </div>
    <div class="T2"><em>答</em> <div class="ts">
      <h4 id="5059_high_content" docNo='5059' class="da_high_content">
        <div class="floatVerticalLine"></div>
        <div class="expand-btn" onclick="showAllFaq(5059)">展开<em></em></div>
        带上证件去行政服务办事大厅补办即可。
        <div class="close-btn" onclick="showAllFaq(5059)">收起</div>
      </h4></div></div></td></tr>
  <tr><td><b>咨询电话:</b></td><td>88981012</td></tr>
  <tr><td><b>受理部门:</b></td><td>信息技术中心</td></tr>
  <tr><td><b>科 室:</b></td><td>校园卡管理</td></tr>
  <tr class="end"><td colspan="2"><a onclick="faq_solve(1,'5059')">已解决</a></td></tr>
</tbody></table></dd>
<dd class="D3"><table><tbody>
  <tr><td colspan="2"><div class="T1"><em>问</em> <div class="ts">校园卡丢了怎么挂失？</div> </div>
    <div class="T2"><em>答</em> <div class="ts">
      <h4 id="5058_high_content" docNo='5058' class="da_high_content">
        在自助机或 app 上挂失，<font color='red'>校园卡</font>立即冻结。
      </h4></div></div></td></tr>
  <tr><td><b>咨询电话:</b></td><td>88981012</td></tr>
  <tr><td><b>受理部门:</b></td><td>信息技术中心</td></tr>
</tbody></table></dd>
"""

WORK_GUIDE_PAGE = """
<h3>约有 <span>669</span> 项符合的查询结果</h3>
<div class="lists"><table class="bst_tb"><tbody>
  <tr><td colspan="2"><div class="T3" id="data_Z0419">本科生提前毕业申请</div></td></tr>
  <tr><td colspan="2"><b>受理时间:</b>毕业前一学年</td></tr>
  <tr><td><b>咨询电话:</b>88206184</td><td><b>监督电话:</b>88206184</td></tr>
  <tr><td><b>受理机构：</b>本科生院东1B-120</td><td><b>受理地方：</b>本科生院东1B-120</td></tr>
</tbody></table></div>
"""

NORM_FILE_PAGE = """
<h3>约有 <span>747</span> 项符合的查询结果</h3>
<dl><dt><a href="http://10.0.0.1/info.html?id=18259" id="norm_file_18259"
  title="浙江大学研究生学位申请实施办法">浙江大学研究生学位申请实施办法</a></dt>
<dd><div class="T2"><h3 class="cut"><b>【发文号】</b>浙大发研〔2026〕27号
  | <b>【施行日期】</b>2026-07-10
  | <b>【文件下载】</b><a href="/tool/downNormFile.do?docNo=18259">下载</a></h3></div></dd></dl>
"""

NORM_DETAIL_PAGE = """
<li><span >【拟文部门】</span>研究生院</li>
<li><span >【发文号】</span>浙大发研〔2026〕27号</li>
<li><span >【印发日期】</span>2026-07-10</li>
<li><span >【时效性】</span>现行有效</li>
"""

INFO_PAGE = """
<h3>约有 <span>14322</span> 项符合的查询结果</h3>
<dl>
  <dt class="cut"><a href="http://bksy.zju.edu.cn/2025/1029/c28418a3097101/page.htm"
    dataSource = "new_web" target="_Blank">关于第四轮<font color='red'>选课</font>的通知</a></dt>
  <dd><div class="T2"><div class="limit-lines3">选课安排在11月7日。</div>
    <h3>发布时间： 2025-10-29 | 信息来源： 本科生院办公网 </h3></div></dd>
</dl>
"""


def test_parse_total():
    assert parse_total(FAQ_PAGE) == 2409
    assert parse_total("<h3>无结果</h3>") == 0


def test_faq_full_fields():
    items = parse_faq_page(FAQ_PAGE)
    assert len(items) == 2
    first = items[0]
    assert first["doc_no"] == "5059"
    assert first["question"] == "如何补办校园卡？"
    assert "行政服务办事大厅" in first["answer"]
    assert "展开" not in first["answer"]  # 按钮节点被剔除
    assert first["phone"] == "88981012"
    assert first["dept"] == "信息技术中心"
    assert first["office"] == "校园卡管理"


def test_faq_missing_optional_fields():
    """缺科室/部门字段的条目不能整条丢失。"""
    items = parse_faq_page(FAQ_PAGE)
    second = items[1]
    assert second["doc_no"] == "5058"
    assert second["question"] == "校园卡丢了怎么挂失？"
    assert "自助机" in second["answer"]
    assert second["office"] == ""
    assert "<font" not in second["answer"]  # 高亮标签被清理


def test_work_guide():
    items = parse_work_guide_page(WORK_GUIDE_PAGE)
    assert len(items) == 1
    it = items[0]
    assert it["title"] == "本科生提前毕业申请"
    assert it["fields"]["受理时间"] == "毕业前一学年"
    assert it["fields"]["咨询电话"] == "88206184"
    assert it["phone"] == "88206184"


def test_norm_file():
    items = parse_norm_file_page(NORM_FILE_PAGE)
    assert len(items) == 1
    it = items[0]
    assert it["doc_no"] == "18259"
    assert it["title"] == "浙江大学研究生学位申请实施办法"
    assert it["issue_no"] == "浙大发研〔2026〕27号"
    assert it["effective_date"] == "2026-07-10"
    assert "viewpdf" in it["view_url"] or "normFileView" in it["view_url"]


def test_norm_file_detail():
    meta = parse_norm_file_detail(NORM_DETAIL_PAGE)
    assert meta["拟文部门"] == "研究生院"
    assert meta["时效性"] == "现行有效"


def test_info_page():
    items = parse_info_page(INFO_PAGE)
    assert len(items) == 1
    it = items[0]
    assert it["title"] == "关于第四轮选课的通知"
    assert it["url"].startswith("http://bksy.zju.edu.cn")
    assert it["publish_date"] == "2025-10-29"
    assert it["source_org"] == "本科生院办公网"


def test_bst_hits_compatible_with_rag_hits():
    """bst_search 返回条目要带 build_context/UI 需要的字段。"""
    from core.bst import _to_hit

    hit = _to_hit({
        "doc_no": "5059", "question": "如何补办校园卡？",
        "answer": "去大厅补办", "phone": "8898", "dept": "信技", "office": "卡办",
    }, "faq")
    for key in ("id", "title", "text", "source_url", "source_org", "publish_date", "score"):
        assert key in hit, key
    assert hit["from_bst"] == "常见问题"
    assert hit["source_url"].startswith("https://s.zju.edu.cn/search/faq.do")

    info = _to_hit({
        "title": "选课通知", "url": "https://bksy.zju.edu.cn/a/b/page.htm",
        "snippet": "摘要", "publish_date": "2025-10-29", "source_org": "本科生院",
    }, "info")
    assert info["from_bst"] == "校园资讯"
    assert info["source_url"] == "https://bksy.zju.edu.cn/a/b/page.htm"


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
        except Exception:
            failed += 1
            print(f"ERROR {fn.__name__}")
            traceback.print_exc()
    print(f"{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
