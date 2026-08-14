"""将备份恢复到一个新的空目录；绝不覆盖现有生产数据。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path, PurePosixPath

if __package__:
    from .ops_backup import SOURCE_DIRS
else:
    from ops_backup import SOURCE_DIRS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def restore_backup(archive_path: Path, destination: Path) -> dict:
    archive_path = archive_path.resolve()
    destination = destination.resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"备份不存在：{archive_path}")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("恢复目标必须是新建或空目录，禁止覆盖现有生产数据")
    destination.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"备份包含不安全路径：{member.name}")
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"备份包含不允许的成员类型：{member.name}")
        manifest_member = archive.getmember("backup-manifest.json")
        stream = archive.extractfile(manifest_member)
        if stream is None:
            raise ValueError("无法读取 backup-manifest.json")
        manifest = json.loads(stream.read().decode("utf-8"))
        if manifest.get("schema") != 1:
            raise ValueError("不支持的备份格式")

        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"无法读取：{member.name}")
                with target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                os.chmod(target, member.mode & 0o777)

    missing = [name for name in SOURCE_DIRS if not (destination / name).is_dir()]
    if missing:
        raise ValueError("恢复结果缺少目录：" + ", ".join(missing))
    for item in manifest.get("files", []):
        path = destination / item["path"]
        if (not path.is_file() or path.stat().st_size != item["size"]
                or _sha256(path) != item["sha256"]):
            raise ValueError(f"恢复完整性校验失败：{item['path']}")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = restore_backup(args.archive, args.destination)
    except (OSError, ValueError, KeyError, json.JSONDecodeError, tarfile.TarError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    print(f"restored={args.destination.resolve()}")
    print(f"git_revision={manifest.get('git_revision', 'unknown')}")
    print("请先完成数据校验，再人工切换生产目录。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
