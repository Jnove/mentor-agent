"""
把 knowledge_base/ 下符合 KB_FORMAT.md 规范的 markdown 文档
切块、向量化后写入本地 Chroma 向量库。

增量入库：按文件内容 hash 判断，只处理新增/变更的文档；
被删除或标记 valid: false 的文档，其旧块会从库里清除。

用法:
    python ingest.py               # 增量更新
    python ingest.py --rebuild     # 全量重建（换 embedding 模型后必须用这个）
"""
import argparse
import hashlib

import chromadb
import frontmatter

from core.chunking import split_by_headings
from core.config import COLLECTION, DB_DIR, KB_DIR

REQUIRED = ["title", "source_url", "source_org", "publish_date", "category"]


def load_docs():
    """返回 [(path, post, content_hash)]，跳过缺字段/已失效的文档。"""
    docs = []
    for path in sorted(KB_DIR.rglob("*.md")):
        raw = path.read_bytes()
        post = frontmatter.loads(raw.decode("utf-8"))
        missing = [k for k in REQUIRED if not post.get(k)]
        if missing:
            print(f"[跳过] {path.name} 缺少字段: {missing}")
            continue
        if post.get("valid", True) is False:
            print(f"[跳过] {path.name} 已标记失效")
            continue
        docs.append((path, post, hashlib.sha256(raw).hexdigest()))
    return docs


def make_chunks(path, post, content_hash):
    """一篇文档 -> (ids, texts, metas)。

    id 和 file 元数据都用相对 knowledge_base/ 的路径，
    否则不同子文件夹下的同名文件会生成相同 id 互相覆盖。
    """
    rel = path.relative_to(KB_DIR).as_posix()
    ids, texts, metas = [], [], []
    for i, chunk in enumerate(split_by_headings(post.content)):
        ids.append(f"{rel}::{i}")
        # 把标题拼进块里，检索时更容易命中
        texts.append(f"《{post['title']}》\n{chunk}")
        metas.append({
            "title": str(post["title"]),
            "source_url": str(post["source_url"]),
            "source_org": str(post["source_org"]),
            "publish_date": str(post["publish_date"]),
            "category": str(post["category"]),
            "file": rel,
            "content_hash": content_hash,
        })
    return ids, texts, metas


def main(rebuild: bool = False):
    client = chromadb.PersistentClient(path=DB_DIR)
    if rebuild:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
    col = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    # 现有库里每个文件的 hash 和块 id
    existing = col.get(include=["metadatas"])
    by_file: dict[str, dict] = {}
    for id_, meta in zip(existing["ids"] or [], existing["metadatas"] or []):
        meta = meta or {}
        info = by_file.setdefault(str(meta.get("file", "")), {"hash": meta.get("content_hash"), "ids": []})
        info["ids"].append(id_)

    docs = load_docs()
    seen = set()
    add_ids, add_texts, add_metas = [], [], []
    for path, post, content_hash in docs:
        rel = path.relative_to(KB_DIR).as_posix()
        seen.add(rel)
        prev = by_file.get(rel)
        if prev and prev["hash"] == content_hash:
            continue  # 未变更
        if prev:
            col.delete(ids=prev["ids"])
            print(f"[更新] {rel}")
        else:
            print(f"[新增] {rel}")
        ids, texts, metas = make_chunks(path, post, content_hash)
        add_ids += ids
        add_texts += texts
        add_metas += metas

    # 库里有、目录里没了（或被标失效/缺字段跳过）的文件 -> 清除旧块
    for fname, info in by_file.items():
        if fname and fname not in seen:
            col.delete(ids=info["ids"])
            print(f"[清除] {fname}（已删除或失效）")

    if add_texts:
        from core.embeddings import get_embedder  # 无变更时不加载模型，秒退

        embed = get_embedder()
        col.add(ids=add_ids, documents=add_texts, embeddings=embed(add_texts), metadatas=add_metas)
        print(f"完成：新增/更新 {len(add_texts)} 块，库中共 {col.count()} 条 -> {DB_DIR}")
    else:
        print(f"没有变更，库中共 {col.count()} 条")
    if add_texts:
        print("提示：app 正在运行的话需要重启，BM25 索引才能看到新文档")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="全量重建向量库")
    args = parser.parse_args()
    main(rebuild=args.rebuild)
