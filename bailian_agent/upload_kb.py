"""Resumable Bailian knowledge-base upload using the official Alibaba Cloud SDK."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import httpx
from alibabacloud_bailian20231229 import models as bailian_models
from alibabacloud_bailian20231229.client import Client as BailianClient
from alibabacloud_tea_openapi import models as open_api_models

from bailian_agent.upload_manifest import build_manifest


class UploadError(RuntimeError):
    pass


SMART_CHUNK_SIZE = 1200


def load_access_key_csv(path: Path) -> tuple[str, str]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if len(rows) != 1:
        raise UploadError("AccessKey CSV must contain exactly one data row")
    access_key_id = (rows[0].get("AccessKey ID") or "").strip()
    access_key_secret = (rows[0].get("AccessKey Secret") or "").strip()
    if not access_key_id or not access_key_secret:
        raise UploadError("AccessKey CSV is missing AccessKey ID or AccessKey Secret")
    return access_key_id, access_key_secret


def load_latest_manifest(path: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                record = json.loads(line)
                identity = record["identity_key"]
                if records.get(identity, {}).get("status") == "uploaded" and record.get("status") != "uploaded":
                    continue
                records[identity] = record
    return records


def append_state(path: Path, record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(line)
        file.flush()
        os.fsync(file.fileno())


def cloud_file_name(record: dict) -> str:
    stem = Path(record["relative_path"]).stem
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .") or "document"
    short_id = record["doc_id"].removeprefix("kb-")[:10]
    suffix = f"__{short_id}.md"
    return safe_stem[: 128 - len(suffix)] + suffix


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _response_data(response, operation: str):
    body = getattr(response, "body", None)
    success = str(getattr(body, "success", "true")).lower()
    code = getattr(body, "code", None)
    if body is None or success in {"false", "0"} or code not in {None, "Success"}:
        message = getattr(body, "message", "empty response")
        raise UploadError(f"{operation} failed: {code or 'Unknown'} {message}")
    data = getattr(body, "data", None)
    if data is None:
        raise UploadError(f"{operation} returned no data")
    return data


class RateLimiter:
    def __init__(self, calls_per_second: float = 8.0) -> None:
        self.interval = 1.0 / calls_per_second
        self.lock = threading.Lock()
        self.next_call = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = self.next_call - now
            if delay > 0:
                time.sleep(delay)
            self.next_call = time.monotonic() + self.interval


class Api:
    def __init__(self, access_key_id: str, access_key_secret: str) -> None:
        self.access_key_id = access_key_id
        self.access_key_secret = access_key_secret
        self.local = threading.local()
        self.limiter = RateLimiter()

    def client(self) -> BailianClient:
        if not hasattr(self.local, "client"):
            config = open_api_models.Config(
                access_key_id=self.access_key_id,
                access_key_secret=self.access_key_secret,
            )
            config.endpoint = "bailian.cn-beijing.aliyuncs.com"
            self.local.client = BailianClient(config)
        return self.local.client

    def http(self) -> httpx.Client:
        if not hasattr(self.local, "http"):
            self.local.http = httpx.Client(timeout=120, trust_env=False)
        return self.local.http

    def call(self, function, operation: str):
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                self.limiter.wait()
                return function(self.client())
            except Exception as error:
                last_error = error
                if attempt == 3:
                    break
                time.sleep(2**attempt)
        raise UploadError(f"{operation} failed after retries: {last_error}") from last_error


def _local_path(root: Path, record: dict) -> Path:
    root = root.resolve()
    path = (root / record["relative_path"]).resolve()
    if root not in path.parents or not path.is_file():
        raise UploadError(f"invalid local path: {record['relative_path']}")
    return path


def upload_one(api: Api, workspace_id: str, root: Path, record: dict) -> dict:
    path = _local_path(root, record)
    content = path.read_bytes()
    name = cloud_file_name(record)
    md5 = hashlib.md5(content).hexdigest()
    lease_request = bailian_models.ApplyFileUploadLeaseRequest(
        category_type="UNSTRUCTURED",
        file_name=name,
        md_5=md5,
        size_in_bytes=str(len(content)),
    )
    lease_response = api.call(
        lambda client: client.apply_file_upload_lease("default", workspace_id, lease_request),
        "ApplyFileUploadLease",
    )
    lease = _response_data(lease_response, "ApplyFileUploadLease")
    upload_headers = {str(key): str(value) for key, value in (lease.param.headers or {}).items()}
    upload_response = api.http().request(
        lease.param.method,
        lease.param.url,
        headers=upload_headers,
        content=content,
    )
    upload_response.raise_for_status()

    add_request = bailian_models.AddFileRequest(
        category_id="default",
        category_type="UNSTRUCTURED",
        lease_id=lease.file_upload_lease_id,
        original_file_url=record["source_url"],
        parser="DASHSCOPE_DOCMIND",
    )
    add_response = api.call(
        lambda client: client.add_file(workspace_id, add_request),
        "AddFile",
    )
    added = _response_data(add_response, "AddFile")
    return {
        **record,
        "status": "parsing",
        "cloud_file_id": added.file_id,
        "cloud_file_name": name,
        "parse_status": "INIT",
        "last_error": None,
        "attempts": int(record.get("attempts") or 0) + 1,
    }


def upload_pending(
    api: Api,
    workspace_id: str,
    root: Path,
    manifest_path: Path,
    records: dict[str, dict],
    selected: set[str],
    workers: int,
) -> None:
    pending = [
        records[identity]
        for identity in selected
        if records[identity].get("status") in {"pending", "upload_failed"}
    ]
    if not pending:
        return
    completed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(upload_one, api, workspace_id, root, record): record
            for record in pending
        }
        for future in as_completed(futures):
            old = futures[future]
            try:
                updated = future.result()
            except Exception as error:
                updated = {
                    **old,
                    "status": "upload_failed",
                    "last_error": str(error)[:500],
                    "attempts": int(old.get("attempts") or 0) + 1,
                }
            records[updated["identity_key"]] = updated
            append_state(manifest_path, updated)
            completed += 1
            if completed % 25 == 0 or completed == len(pending):
                failed = sum(1 for identity in selected if records[identity].get("status") == "upload_failed")
                print(f"upload {completed}/{len(pending)} failed={failed}", flush=True)


def wait_for_parsing(
    api: Api,
    workspace_id: str,
    manifest_path: Path,
    records: dict[str, dict],
    selected: set[str],
    workers: int,
    poll_seconds: int,
) -> None:
    while True:
        parsing = [
            records[identity]
            for identity in selected
            if records[identity].get("status") == "parsing" and records[identity].get("cloud_file_id")
        ]
        if not parsing:
            return
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    api.call,
                    lambda client, file_id=record["cloud_file_id"]: client.describe_file(
                        workspace_id,
                        file_id,
                        bailian_models.DescribeFileRequest(),
                    ),
                    "DescribeFile",
                ): record
                for record in parsing
            }
            for future in as_completed(futures):
                old = futures[future]
                try:
                    data = _response_data(future.result(), "DescribeFile")
                    if data.status == "PARSE_SUCCESS":
                        updated = {**old, "status": "ready", "parse_status": data.status, "last_error": None}
                    elif data.status == "PARSE_FAILED":
                        updated = {
                            **old,
                            "status": "parse_failed",
                            "parse_status": data.status,
                            "last_error": data.parse_error_message or "parse failed",
                        }
                    elif data.status != old.get("parse_status"):
                        updated = {**old, "parse_status": data.status}
                    else:
                        continue
                except Exception as error:
                    updated = {**old, "last_error": str(error)[:500]}
                records[updated["identity_key"]] = updated
                append_state(manifest_path, updated)
        ready = sum(1 for identity in selected if records[identity].get("status") == "ready")
        remaining = sum(1 for identity in selected if records[identity].get("status") == "parsing")
        failed = sum(1 for identity in selected if records[identity].get("status") == "parse_failed")
        print(f"parse ready={ready} remaining={remaining} failed={failed}", flush=True)
        if remaining:
            time.sleep(max(10, poll_seconds))


def wait_for_index_job(
    api: Api,
    workspace_id: str,
    index_id: str,
    job_id: str,
    batch: list[dict],
    manifest_path: Path,
    records: dict[str, dict],
    poll_seconds: int,
) -> None:
    while True:
        request = bailian_models.GetIndexJobStatusRequest(
            index_id=index_id,
            job_id=job_id,
            page_number=1,
            page_size=max(10, len(batch)),
        )
        response = api.call(
            lambda client: client.get_index_job_status(workspace_id, request),
            "GetIndexJobStatus",
        )
        data = _response_data(response, "GetIndexJobStatus")
        print(f"index job={job_id} status={data.status}", flush=True)
        if data.status == "COMPLETED":
            by_id = {document.doc_id: document for document in (data.documents or [])}
            by_name = {document.doc_name: document for document in (data.documents or [])}
            for old in batch:
                document = by_id.get(old.get("cloud_file_id")) or by_name.get(old.get("cloud_file_name"))
                if document is not None and document.status != "FINISH":
                    updated = {
                        **old,
                        "status": "index_failed",
                        "last_error": document.message or document.status,
                    }
                else:
                    updated = {
                        **old,
                        "status": "uploaded",
                        "cloud_document_id": getattr(document, "doc_id", None) or old.get("cloud_file_id"),
                        "uploaded_at": _now(),
                        "last_error": None,
                    }
                records[updated["identity_key"]] = updated
                append_state(manifest_path, updated)
            return
        if data.status == "FAILED":
            for old in batch:
                updated = {**old, "status": "index_failed", "last_error": "index job failed"}
                records[updated["identity_key"]] = updated
                append_state(manifest_path, updated)
            return
        time.sleep(max(10, poll_seconds))


def build_index_request(index_id: str, batch: list[dict]) -> bailian_models.SubmitIndexAddDocumentsJobRequest:
    return bailian_models.SubmitIndexAddDocumentsJobRequest(
        chunk_size=SMART_CHUNK_SIZE,
        index_id=index_id,
        document_ids=[record["cloud_file_id"] for record in batch],
        source_type="DATA_CENTER_FILE",
    )


def index_ready(
    api: Api,
    workspace_id: str,
    index_id: str,
    manifest_path: Path,
    records: dict[str, dict],
    selected: set[str],
    batch_size: int,
    poll_seconds: int,
) -> None:
    existing_jobs: dict[str, list[dict]] = {}
    for identity in selected:
        record = records[identity]
        if record.get("status") == "indexing" and record.get("index_job_id"):
            existing_jobs.setdefault(record["index_job_id"], []).append(record)
    for job_id, batch in existing_jobs.items():
        wait_for_index_job(api, workspace_id, index_id, job_id, batch, manifest_path, records, poll_seconds)

    ready = [records[identity] for identity in selected if records[identity].get("status") == "ready"]
    for start in range(0, len(ready), batch_size):
        batch = ready[start : start + batch_size]
        request = build_index_request(index_id, batch)
        response = api.call(
            lambda client: client.submit_index_add_documents_job(workspace_id, request),
            "SubmitIndexAddDocumentsJob",
        )
        job_id = _response_data(response, "SubmitIndexAddDocumentsJob").id
        indexed_batch = []
        for old in batch:
            updated = {**old, "status": "indexing", "index_job_id": job_id}
            records[updated["identity_key"]] = updated
            append_state(manifest_path, updated)
            indexed_batch.append(updated)
        print(f"submitted index batch size={len(batch)} job={job_id}", flush=True)
        wait_for_index_job(
            api,
            workspace_id,
            index_id,
            job_id,
            indexed_batch,
            manifest_path,
            records,
            poll_seconds,
        )


def verify_index(api: Api, workspace_id: str, index_id: str, records: dict[str, dict], selected: set[str]) -> dict:
    cloud = {}
    cloud_total = 0
    fetched = 0
    page_number = 1
    while True:
        request = bailian_models.ListIndexDocumentsRequest(
            index_id=index_id,
            page_number=page_number,
            page_size=100,
        )
        response = api.call(
            lambda client, request=request: client.list_index_documents(workspace_id, request),
            "ListIndexDocuments",
        )
        data = _response_data(response, "ListIndexDocuments")
        documents = data.documents or []
        cloud_total = int(data.total_count or 0)
        fetched += len(documents)
        cloud.update({document.name: document for document in documents})
        if not documents or fetched >= cloud_total:
            break
        page_number += 1
    selected_records = [records[identity] for identity in selected]
    found = sum(
        1
        for record in selected_records
        if Path(record.get("cloud_file_name") or "").stem in cloud
        and cloud[Path(record["cloud_file_name"]).stem].status == "FINISH"
    )
    return {
        "cloud_total": cloud_total,
        "selected": len(selected_records),
        "selected_finish": found,
        "uploaded_records": sum(1 for record in selected_records if record.get("status") == "uploaded"),
        "failed_records": sum(1 for record in selected_records if record.get("status") in {"upload_failed", "parse_failed", "index_failed"}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access-key-csv", type=Path, required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--index-id", required=True)
    parser.add_argument("--knowledge-base-root", type=Path, default=Path("knowledge_base"))
    parser.add_argument("--manifest", type=Path, default=Path("bailian_agent/upload_manifest.jsonl"))
    parser.add_argument("--identity-key")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--poll-seconds", type=int, default=15)
    args = parser.parse_args()

    access_key_id, access_key_secret = load_access_key_csv(args.access_key_csv)
    api = Api(access_key_id, access_key_secret)
    records = load_latest_manifest(args.manifest)
    eligible = [
        identity
        for identity, record in sorted(records.items())
        if record.get("status") not in {"uploaded", "conflict", "missing_local", "changed"}
    ]
    if args.identity_key:
        eligible = [identity for identity in eligible if identity == args.identity_key]
    if args.limit is not None:
        eligible = eligible[: args.limit]
    if not eligible:
        print(json.dumps({"selected": 0, "message": "nothing to upload"}, ensure_ascii=False))
        return
    selected = set(eligible)
    print(f"selected={len(selected)}", flush=True)

    upload_pending(api, args.workspace_id, args.knowledge_base_root, args.manifest, records, selected, args.workers)
    wait_for_parsing(api, args.workspace_id, args.manifest, records, selected, args.workers, args.poll_seconds)
    index_ready(
        api,
        args.workspace_id,
        args.index_id,
        args.manifest,
        records,
        selected,
        min(500, max(1, args.batch_size)),
        args.poll_seconds,
    )
    summary = verify_index(api, args.workspace_id, args.index_id, records, selected)
    print(json.dumps(summary, ensure_ascii=False), flush=True)

    build_manifest(args.knowledge_base_root, args.manifest, args.index_id)


if __name__ == "__main__":
    main()
