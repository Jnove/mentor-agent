"""
把 knowledge_base/ 下符合 KB_FORMAT.md v2 的 markdown 文档
切块、向量化后写入本地 Chroma 向量库。

增量入库：按文件内容 hash 判断，只处理新增/变更的文档；
被删除、标记 valid: false 或 rejected 的文档，其旧块会从库里清除。

用法:
    python ingest.py               # 增量更新
    python ingest.py --rebuild     # 全量重建（换 embedding 模型后必须用这个）
    python ingest.py --include-needs-review  # 仅开发/审核环境
"""
import argparse
import hashlib

import chromadb
import frontmatter

from core.chunking import split_by_headings
from core.config import COLLECTION, DB_DIR, KB_DIR, MAX_CHUNK_CHARS
from core.kb_paths import iter_published_markdown
from core.kb_schema import is_publishable, validate_metadata

# 切块配置参与 content_hash：改块长/切块算法后，普通增量 ingest 也会重切全部文档。
_CHUNK_STAMP = f"|chunker-v2:{MAX_CHUNK_CHARS}".encode()


def load_docs(*, include_needs_review: bool = False, kb_dir=KB_DIR):
    """返回可发布文档；正式目录中的 schema 错误会阻止整次入库。"""
    docs = []
    errors = []
    skipped_invalid = 0
    skipped_unverified = 0
    for path in iter_published_markdown(kb_dir):
        try:
            raw = path.read_bytes()
            post = frontmatter.loads(raw.decode("utf-8"))
        except Exception as exc:
            errors.append(f"{path}: 解析失败：{exc}")
            continue
        result = validate_metadata(post.metadata)
        if result.errors:
            errors.append(f"{path}: {'; '.join(result.errors)}")
            continue
        for warning in result.warnings:
            print(f"[警告] {path.name}: {warning}")
        if post.get("valid") is False or post.get("review_status") == "rejected":
            skipped_invalid += 1
            continue
        if not is_publishable(post.metadata, include_needs_review=include_needs_review):
            skipped_unverified += 1
            continue
        docs.append((path, post, hashlib.sha256(raw + _CHUNK_STAMP).hexdigest()))
    if errors:
        for error in errors:
            print(f"[错误] {error}")
        raise SystemExit(f"知识库校验失败：{len(errors)} 篇文档不符合 KB_FORMAT.md v2")
    skipped = []
    if skipped_invalid:
        skipped.append(f"失效或拒绝 {skipped_invalid} 篇")
    if skipped_unverified:
        skipped.append(f"尚未人工核验 {skipped_unverified} 篇")
    if skipped:
        print(f"[跳过汇总] {'；'.join(skipped)}")
    return docs


def _list_text(post, key: str) -> str:
    return "、".join(str(value) for value in post.get(key, []) if str(value).strip())


def make_chunks(path, post, content_hash):
    """一篇文档 -> (ids, texts, metas)。"""
    rel = path.relative_to(KB_DIR).as_posix()
    ids, texts, metas = [], [], []
    tag_text = _list_text(post, "tags")
    search_prefix = f"标题：{post['title']}\n分类：{post['category']}"
    if tag_text:
        # 前缀和正文共享 embedding/reranker 的 512 token 窗口；只把前 100 字标签入块。
        search_tags = tag_text
        if len(search_tags) > 100:
            search_tags = search_tags[:100].rsplit("、", 1)[0]
        search_prefix += f"\n检索标签：{search_tags}"
    for i, chunk in enumerate(split_by_headings(post.content)):
        ids.append(f"{rel}::{i}")
        texts.append(f"{search_prefix}\n\n{chunk}")
        metas.append({
            "schema_version": int(post["schema_version"]),
            "doc_id": str(post["doc_id"]),
            "title": str(post["title"]),
            "source_url": str(post["source_url"]),
            "source_org": str(post["source_org"]),
            "source_type": str(post["source_type"]),
            "authority_level": str(post["authority_level"]),
            "publish_date": str(post["publish_date"]),
            "category": str(post["category"]),
            "tags": tag_text,
            "review_status": str(post["review_status"]),
            "last_checked_at": str(post.get("last_checked_at") or ""),
            "maintainer": str(post["maintainer"]),
            "applies_to": _list_text(post, "applies_to"),
            "campuses": _list_text(post, "campuses"),
            "colleges": _list_text(post, "colleges"),
            "effective_from": str(post.get("effective_from") or ""),
            "effective_until": str(post.get("effective_until") or ""),
            "supersedes": _list_text(post, "supersedes"),
            "superseded_by": _list_text(post, "superseded_by"),
            "file": rel,
            "content_hash": content_hash,
        })
    return ids, texts, metas


def main(rebuild: bool = False, *, include_needs_review: bool = False):
    # 先完整解析和校验，--rebuild 也不能因坏文档提前销毁现有索引。
    if include_needs_review:
        print("[警告] 已显式启用 needs_review 文档；禁止将该模式用于生产发布")
    docs = load_docs(include_needs_review=include_needs_review)
    client = chromadb.PersistentClient(path=DB_DIR)
    col = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    # 分页拉取现有 metadata，避免全库一次 get 触发 Chroma 底层 SQLite 变量上限。
    by_file: dict[str, dict] = {}
    if not rebuild:
        _PAGE = 1000
        offset = 0
        while True:
            page = col.get(include=["metadatas"], limit=_PAGE, offset=offset)
            ids = page["ids"] or []
            for id_, meta in zip(ids, page["metadatas"] or []):
                meta = meta or {}
                info = by_file.setdefault(str(meta.get("file", "")), {"hash": meta.get("content_hash"), "ids": []})
                info["ids"].append(id_)
            if len(ids) < _PAGE:
                break
            offset += _PAGE

    seen = set()
    add_ids, add_texts, add_metas = [], [], []
    stale_ids: list[str] = []
    messages: list[str] = []
    for path, post, content_hash in docs:
        rel = path.relative_to(KB_DIR).as_posix()
        seen.add(rel)
        prev = by_file.get(rel)
        if prev and prev["hash"] == content_hash:
            continue
        ids, texts, metas = make_chunks(path, post, content_hash)
        if prev:
            # upsert 成功后才清掉因新切块数量减少而残留的旧 ID。
            stale_ids.extend(sorted(set(prev["ids"]) - set(ids)))
            messages.append(f"[更新] {rel}")
        else:
            messages.append(f"[新增] {rel}")
        add_ids += ids
        add_texts += texts
        add_metas += metas

    removed_files: list[tuple[str, list[str]]] = []
    for fname, info in by_file.items():
        if fname and fname not in seen:
            removed_files.append((fname, info["ids"]))

    # embedding 是最常见的外部失败点；必须在任何删除操作之前完成。
    embeddings = None
    if add_texts:
        from core.embeddings import get_embedder

        embeddings = get_embedder()(add_texts)

    if rebuild:
        try:
            client.delete_collection(COLLECTION)
        except Exception:
            pass
        col = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    if add_texts:
        # Chroma 默认 max_batch_size=5461；大批量增量（如一次接入数百篇）会超限，需分批 upsert。
        _BATCH = 1000
        for i in range(0, len(add_ids), _BATCH):
            col.upsert(
                ids=add_ids[i : i + _BATCH],
                documents=add_texts[i : i + _BATCH],
                embeddings=embeddings[i : i + _BATCH],
                metadatas=add_metas[i : i + _BATCH],
            )
        for message in messages:
            print(message)
    if stale_ids:
        col.delete(ids=stale_ids)
    for fname, ids in removed_files:
        col.delete(ids=ids)
        print(f"[清除] {fname}（已删除、失效或拒绝）")

    if add_texts:
        print(f"完成：新增/更新 {len(add_texts)} 块，库中共 {col.count()} 条 -> {DB_DIR}")
        print("提示：app 正在运行的话需要重启，BM25 索引才能看到新文档")
    elif rebuild:
        print(f"完成：已重建空索引，库中共 {col.count()} 条")
    elif removed_files or stale_ids:
        print(f"完成：已清理旧块，库中共 {col.count()} 条")
    else:
        print(f"没有变更，库中共 {col.count()} 条")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="全量重建向量库")
    parser.add_argument(
        "--include-needs-review",
        action="store_true",
        help="仅开发/审核环境：把 valid:true 的 needs_review 文档加入索引",
    )
    args = parser.parse_args()
    main(rebuild=args.rebuild, include_needs_review=args.include_needs_review)
