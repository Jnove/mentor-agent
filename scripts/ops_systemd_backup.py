"""在 systemd 部署中安全停服、备份，并恢复原有服务状态。

该 runner 必须由有权管理目标 unit 的账户执行（生产模板使用 root）。归档命令会
降权到 ``MENTOR_RUN_AS_USER``，避免让备份脚本以 root 身份遍历应用可写目录。
所有命令都以参数数组调用，不经过 shell。
"""
from __future__ import annotations

import argparse
import fcntl
import os
import pwd
import re
import signal
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Sequence


class BackupCycleError(RuntimeError):
    """备份周期无法安全完成。"""


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
_SERVICE_RE = re.compile(r"[A-Za-z0-9_.@:-]+\.service")
_USER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\$?")
_STOPPED_STATES = {"inactive", "failed"}


@dataclass(frozen=True)
class BackupConfig:
    service: str
    root: Path
    output_dir: Path
    backup_script: Path
    python: str
    run_as_user: str
    systemctl: str
    runuser: str
    lock_file: Path


def _error_detail(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    return f": {detail[-1000:]}" if detail else ""


def _run(
    args: Sequence[str],
    *,
    runner: CommandRunner,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = runner(
        list(args),
        check=False,
        text=True,
        capture_output=capture_output,
    )
    if result.returncode != 0:
        raise BackupCycleError(
            f"命令失败（exit={result.returncode}）：{args[0]}{_error_detail(result)}"
        )
    return result


def _service_state(config: BackupConfig, runner: CommandRunner) -> str:
    result = runner(
        [config.systemctl, "is-active", config.service],
        check=False,
        text=True,
        capture_output=True,
    )
    state = (result.stdout or "").strip()
    # systemctl 对 inactive/failed 返回非零，这是预期状态；其它输出一律安全失败。
    if state == "active" and result.returncode == 0:
        return state
    if state in _STOPPED_STATES:
        return state
    raise BackupCycleError(
        f"无法确认 {config.service} 是否已安全停止：state={state or 'unknown'}"
        f" exit={result.returncode}{_error_detail(result)}"
    )


def _validate_config(config: BackupConfig) -> None:
    if not _SERVICE_RE.fullmatch(config.service):
        raise ValueError(f"非法 systemd unit 名称：{config.service!r}")
    if config.run_as_user and not _USER_RE.fullmatch(config.run_as_user):
        raise ValueError(f"非法运行用户：{config.run_as_user!r}")
    if config.run_as_user:
        try:
            account = pwd.getpwnam(config.run_as_user)
        except KeyError as exc:
            raise ValueError(f"运行用户不存在：{config.run_as_user!r}") from exc
        if account.pw_uid == 0:
            raise ValueError("归档进程禁止使用 UID 0")
    for label, executable in (
        ("python", config.python),
        ("systemctl", config.systemctl),
        ("runuser", config.runuser),
    ):
        if not Path(executable).is_absolute():
            raise ValueError(f"{label} 必须使用绝对路径：{executable!r}")
    if not config.root.is_dir():
        raise FileNotFoundError(f"部署根目录不存在：{config.root}")
    if not config.output_dir.is_dir():
        raise FileNotFoundError(
            f"备份目录不存在：{config.output_dir}；请先由管理员创建并设置最小权限"
        )
    if not config.backup_script.is_file():
        raise FileNotFoundError(f"备份脚本不存在：{config.backup_script}")


def _backup_command(config: BackupConfig) -> list[str]:
    command = [
        config.python,
        str(config.backup_script),
        "--root",
        str(config.root),
        "--output-dir",
        str(config.output_dir),
        "--confirm-app-stopped",
    ]
    if not config.run_as_user:
        if os.geteuid() == 0:
            raise BackupCycleError("root 运行 runner 时必须配置非特权 MENTOR_RUN_AS_USER")
        return command
    current_user = pwd.getpwuid(os.geteuid()).pw_name
    if current_user == config.run_as_user:
        return command
    if os.geteuid() != 0:
        raise BackupCycleError(
            f"当前用户 {current_user} 无法降权到 {config.run_as_user}；请由 root 运行 runner"
        )
    return [config.runuser, "--user", config.run_as_user, "--", *command]


def run_backup_cycle(
    config: BackupConfig,
    *,
    runner: CommandRunner = subprocess.run,
) -> None:
    """执行一次备份，并保证仅恢复 runner 启动前处于 active 的服务。"""
    _validate_config(config)
    initial_state = _service_state(config, runner)
    was_active = initial_state == "active"
    primary_error: BaseException | None = None
    restart_error: BaseException | None = None

    try:
        if was_active:
            _run(
                [config.systemctl, "stop", config.service],
                runner=runner,
                capture_output=True,
            )
            stopped_state = _service_state(config, runner)
            if stopped_state not in _STOPPED_STATES:
                raise BackupCycleError(
                    f"停止 {config.service} 后状态异常：{stopped_state}"
                )
        _run(_backup_command(config), runner=runner)
    except BaseException as exc:  # finally 必须覆盖 KeyboardInterrupt/终止信号
        primary_error = exc
    finally:
        if was_active:
            try:
                _run(
                    [config.systemctl, "start", config.service],
                    runner=runner,
                    capture_output=True,
                )
                state = _service_state(config, runner)
                if state != "active":
                    raise BackupCycleError(
                        f"恢复 {config.service} 后状态异常：{state}"
                    )
            except BaseException as exc:
                restart_error = exc

    if restart_error is not None:
        if primary_error is not None:
            raise BackupCycleError(
                f"备份失败，并且服务恢复也失败：{restart_error}；原始错误：{primary_error}"
            ) from restart_error
        raise BackupCycleError(f"备份完成，但服务恢复失败：{restart_error}") from restart_error
    if primary_error is not None:
        raise primary_error


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """获取非阻塞独占锁，防止 timer 和人工任务同时停服。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupCycleError(f"已有备份任务持有锁：{path}") from exc
        yield
    finally:
        os.close(fd)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _build_config(args: argparse.Namespace) -> BackupConfig:
    root = Path(args.root).expanduser().resolve()
    backup_script = Path(args.backup_script).expanduser().resolve()
    return BackupConfig(
        service=args.service,
        root=root,
        output_dir=Path(args.output_dir).expanduser().resolve(),
        backup_script=backup_script,
        python=args.python,
        run_as_user=args.run_as_user,
        systemctl=args.systemctl,
        runuser=args.runuser,
        lock_file=Path(args.lock_file).expanduser().resolve(),
    )


def _termination_handler(signum: int, _frame: object) -> None:
    raise InterruptedError(f"收到终止信号 {signum}")


def main(argv: Sequence[str] | None = None) -> int:
    default_root = _env("MENTOR_ROOT", "/opt/mentor-agent-production")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", default=_env("MENTOR_SERVICE", "mentor-agent.service"))
    parser.add_argument("--root", default=default_root)
    parser.add_argument(
        "--output-dir",
        default=_env("MENTOR_BACKUP_DIR", "/srv/mentor-backups"),
    )
    parser.add_argument(
        "--backup-script",
        default=_env("MENTOR_BACKUP_SCRIPT", str(Path(default_root) / "scripts/ops_backup.py")),
    )
    parser.add_argument("--python", default=_env("MENTOR_PYTHON", "/usr/bin/python3"))
    parser.add_argument("--run-as-user", default=_env("MENTOR_RUN_AS_USER", "txc"))
    parser.add_argument("--systemctl", default=_env("MENTOR_SYSTEMCTL", "/usr/bin/systemctl"))
    parser.add_argument("--runuser", default=_env("MENTOR_RUNUSER", "/usr/sbin/runuser"))
    parser.add_argument(
        "--lock-file",
        default=_env("MENTOR_BACKUP_LOCK", "/run/lock/mentor-agent-backup.lock"),
    )
    args = parser.parse_args(argv)
    config = _build_config(args)

    previous_term = signal.signal(signal.SIGTERM, _termination_handler)
    try:
        with exclusive_lock(config.lock_file):
            run_backup_cycle(config)
    except (OSError, ValueError, BackupCycleError, InterruptedError, KeyboardInterrupt) as exc:
        print(f"[ERROR] backup-cycle: {exc}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_term)
    print(f"[OK] backup-cycle: service={config.service} output={config.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
