"""为停止状态的单机部署创建权限为 0600 的一致性备份包。"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SOURCE_DIRS = ("knowledge_base", "chroma_db", "data")


def _git_revision(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def create_backup(root: Path, output_dir: Path, *, include_env: bool = False,
                  confirm_app_stopped: bool = False) -> Path:
    if not confirm_app_stopped:
        raise ValueError("必须确认 app 已停止，避免复制到不一致的 SQLite/Chroma 文件")
    root = root.resolve()
    output_dir = output_dir.resolve()
    missing = [name for name in SOURCE_DIRS if not (root / name).is_dir()]
    if missing:
        raise FileNotFoundError("缺少备份目录：" + ", ".join(missing))
    if include_env and not (root / ".env").is_file():
        raise FileNotFoundError("要求包含 .env，但文件不存在")
    for source in (root / name for name in SOURCE_DIRS):
        if output_dir == source or source in output_dir.parents:
            raise ValueError("备份输出目录不能放在被备份的数据目录内部")

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    final_path = output_dir / f"mentor-agent-{stamp}.tar.gz"
    fd, temp_name = tempfile.mkstemp(prefix=".mentor-agent-", suffix=".partial",
                                     dir=output_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    files: list[dict] = []
    try:
        with tarfile.open(temp_path, "w:gz") as archive:
            sources = [root / name for name in SOURCE_DIRS]
            if include_env:
                sources.append(root / ".env")
            for source in sources:
                paths = [source]
                if source.is_dir():
                    paths.extend(sorted(source.rglob("*")))
                for path in paths:
                    if path.is_symlink():
                        raise ValueError(f"备份源中禁止符号链接：{path}")
                    arcname = path.relative_to(root).as_posix()
                    archive.add(path, arcname=arcname, recursive=False)
                    if path.is_file():
                        files.append({
                            "path": arcname,
                            "size": path.stat().st_size,
                            "sha256": _sha256(path),
                        })
            manifest = {
                "schema": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "git_revision": _git_revision(root),
                "included_env": include_env,
                "files": files,
            }
            payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            info = tarfile.TarInfo("backup-manifest.json")
            info.size = len(payload)
            info.mode = 0o600
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            archive.addfile(info, io.BytesIO(payload))

        os.chmod(temp_path, 0o600)
        with tarfile.open(temp_path, "r:gz") as archive:
            names = set(archive.getnames())
            required = {"backup-manifest.json", *SOURCE_DIRS}
            if not required.issubset(names):
                raise RuntimeError("备份包自检失败：缺少必需目录或 manifest")
        # hard link 不会覆盖同名文件；创建成功后再移除临时名，实现同文件系统原子发布。
        os.link(temp_path, final_path)
        temp_path.unlink()
        return final_path
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--include-env", action="store_true",
                        help="仅在备份存储已加密时使用")
    parser.add_argument("--confirm-app-stopped", action="store_true")
    args = parser.parse_args()
    try:
        output = create_backup(args.root, args.output_dir,
                               include_env=args.include_env,
                               confirm_app_stopped=args.confirm_app_stopped)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"[ERROR] {exc}")
        return 1
    print(f"backup={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
