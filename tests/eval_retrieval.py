"""检索质量回归集：用真实 embedding + reranker 对整库跑 golden questions。

用法: python tests/eval_retrieval.py
前提: 已运行 python ingest.py 构建向量库；首次运行加载模型约 10-30 秒。

指标：
- hit@5：期望文档出现在前 5 个结果里（按 file 子串匹配，命中任一即可）
- MRR：期望文档首次出现位置的倒数均值
- 负例最高分：知识库确实没有的问题，全部候选的最高重排分——
  用来校准 config.PROMPT_MIN_SCORE（阈值应高于负例常态、远低于正例）

调 CANDIDATES/TOP_K/PROMPT_MIN_SCORE、换 embedding/reranker、改切块前后各跑一次，
对比 miss 清单。攒到新的真实提问（尤其黑话表述）就往 GOLDEN 里加。
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
if hasattr(sys.stdout, "buffer"):  # Windows 控制台默认 GBK，中文会乱码
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# (问题, [期望命中的 file 路径子串，命中任一算对])
# 期望子串尽量收窄到"真正能回答这个问题"的那一两篇：写宽了（如用"竺可桢学院"
# 匹配竺院旗下 20 个文件）检索退化时 hit@5 仍是绿的，回归集就失去哨兵作用
GOLDEN = [
    # —— 直白表述 ——
    # 校级《学籍管理办法》第六至十一章包含现行转专业通则；学院细则文件名
    # 也包含“转专业”。二者命中任一均是有效依据。
    ("转专业需要什么条件", ["转专业", "学籍管理规定_教务处_2020_part2"]),
    ("军训一般什么时候开始，每天几点起床", ["military_training/time.md"]),
    ("军训期间有哪些活动", ["military_training/activities.md"]),
    ("图书馆的研讨室怎么预约", ["图书馆空间预约指南"]),
    ("校园无线网络怎么连接", ["无线网络", "network"]),
    ("在校外怎么访问图书馆的电子资源", ["VPN", "校外接入"]),
    ("浙大邮箱怎么开通", ["浙大邮箱"]),
    ("校园卡丢了怎么挂失", ["校园卡"]),
    ("推免保研有什么要求", ["推免保研"]),
    ("国家助学贷款怎么申请", ["助学贷款"]),
    ("奖学金怎么评定", ["奖学金", "scholarships"]),
    ("考试作弊会受到什么处分", ["违纪处分"]),
    ("学士学位授予有什么条件", ["学位授予"]),
    ("休学和退学有什么规定", ["学籍管理规定"]),
    ("新生报到的流程是怎样的", ["registration/procedure.md"]),
    ("第一次到学校怎么坐车", ["registration/transportation.md"]),
    ("新生要警惕哪些骗局", ["registration/secure.md"]),
    ("辅修专业怎么申请", ["learning/minor.md"]),
    ("选课有什么技巧", ["course_sys"]),
    ("海宁校区的食堂和住宿怎么样", ["haining/life/canteen_dorm.md"]),
    # —— 口语/黑话表述（同义词扩展的观测对象）——
    ("竺院是干什么的", ["intoCKC", "求是科学班"]),
    ("求是科学班有哪些专业", ["求是科学班"]),
    ("彩票系统是怎么回事", ["course_sys", "slang"]),
    ("一卡通丢了怎么补办", ["校园卡"]),
    ("SRTP 是什么项目", ["SRTP", "research"]),
    ("图灵班怎么才能进", ["图灵班"]),
    ("混合班的选拔是怎么安排的", ["混合班"]),
]

# 知识库没有正面答案的问题。两类解读（2026-07 实测）：
# - 纯库外垃圾应接近 0（如"食堂招标"0.010），PROMPT_MIN_SCORE 负责拦；
# - 语义近邻会拿高分（"教师职称评审"命中奖学金评定 0.56、"杭州地铁"命中
#   到校交通 0.98——后者其实算库内沾边内容），阈值拦不住也不该拦，
#   靠 SYSTEM_PROMPT 第 1 条"资料里没有就说没有"兜底。阈值只应对第一类调。
NEGATIVES = [
    # “杭州地铁末班车”会高分命中《到校交通》，它确实包含地铁接驳信息，不能
    # 再作为纯库外负例；改用知识库明确没有覆盖的校外城市。
    "上海地铁末班车是几点",
    "考研国家线是多少分",
    "食堂档口承包招标怎么申请",
    "教师职称评审的条件是什么",
]


def main():
    import chromadb

    from core.config import COLLECTION, DB_DIR, PROMPT_MIN_SCORE
    from core.embeddings import get_embedder
    from core.retrieval import Retriever, load_reranker

    col = chromadb.PersistentClient(path=DB_DIR).get_collection(COLLECTION)
    r = Retriever(get_embedder(), col, reranker=load_reranker())
    if r.reranker is None:
        print("警告：reranker 未加载，分数与阈值指标无意义")
    print(f"库: {col.count()} 块 / {len(r.catalog)} 篇；"
          f"PROMPT_MIN_SCORE={PROMPT_MIN_SCORE}\n")

    hits_at5, rr_sum, misses, near, top1_scores = 0, 0.0, [], [], []
    for q, expects in GOLDEN:
        # top_k 放大到候选总量：拿到按重排分排序的完整候选序列。
        # 用默认 top_k 的话第 6 位起是"覆盖补位"顺序而非分数顺序，排名会失真
        results = r.search(q, top_k=99, min_score=-1)
        if results and "score" in results[0]:
            top1_scores.append(results[0]["score"])
        files = [h.get("file", "") for h in results]
        rank = next(
            (i + 1 for i, f in enumerate(files)
             if any(e in f for e in expects)), None,
        )
        if rank is not None and rank <= 5:
            hits_at5 += 1
        if rank is not None:
            rr_sum += 1.0 / rank
            if rank > 5:
                near.append((q, rank))  # 差一点：在候选里但没进 top5
        else:
            misses.append((q, expects, files[:3]))

    n = len(GOLDEN)
    print(f"hit@5: {hits_at5}/{n} = {hits_at5 / n:.0%}   MRR: {rr_sum / n:.3f}")
    if top1_scores:
        ts = sorted(top1_scores)
        print(f"golden top-1 重排分: min {ts[0]:.3f} / 中位 {ts[len(ts) // 2]:.3f}"
              f"（阈值离 min 越远越安全）")
    for q, expects, top3 in misses:
        print(f"  MISS  {q}  期望{expects}\n        实际前3: {top3}")
    for q, rank in near:
        print(f"  NEAR  {q}  期望文档排第 {rank}（在候选里但没进 top5）")

    print("\n负例（最高重排分应低于 PROMPT_MIN_SCORE）：")
    neg_max = 0.0
    for q in NEGATIVES:
        results = r.search(q, top_k=99, min_score=-1)
        top = results[0] if results else None
        s = top.get("score", 0.0) if top else 0.0
        neg_max = max(neg_max, s)
        flag = "  <-- 超过阈值！" if s >= PROMPT_MIN_SCORE else ""
        print(f"  {s:.3f}  {q}  最高命中:《{top.get('title', '')}》{flag}" if top
              else f"  (无候选)  {q}")
    print(f"\n负例最高分 {neg_max:.3f}；阈值 {PROMPT_MIN_SCORE} "
          f"{'安全' if neg_max < PROMPT_MIN_SCORE else '需要上调或个案分析'}")

    # 正例视角：阈值不应误杀期望文档的入围块
    killed = []
    for q, expects in GOLDEN:
        results = r.search(q)  # 走真实阈值
        if not any(any(e in h.get("file", "") for e in expects) for h in results):
            killed.append(q)
    if killed:
        print(f"阈值误杀（过滤后期望文档消失）: {killed}")
    else:
        print("阈值无误杀：所有 golden 问题过滤后期望文档仍在结果里")


if __name__ == "__main__":
    main()
