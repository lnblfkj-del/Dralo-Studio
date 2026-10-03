"""Run and monitor the local API and general Worker as one application unit."""

import argparse
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from app.core.config import PROJECT_ROOT, settings
from app.core.runtime_environment import windows_token_restricted
from app.runtime import control_channel

LOCK_PATH = control_channel.SUPERVISOR_LOCK_PATH


def process_alive(pid: int) -> bool:
    return control_channel.process_alive(pid)


def acquire_lock() -> int:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        try:
            existing = int(LOCK_PATH.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            existing = 0
        if existing and process_alive(existing):
            raise RuntimeError(f"本地后台已经运行 (PID {existing})")
        LOCK_PATH.unlink(missing_ok=True)
    descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(descriptor, str(os.getpid()).encode("ascii"))
    return descriptor


def terminate_child(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    if os.name == "nt":
        from app.runtime.windows_process import terminate_process_tree

        terminate_process_tree(child.pid)
    else:
        child.terminate()


def start_child(command: list[str], environment: dict[str, str]) -> subprocess.Popen:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=environment,
        creationflags=creationflags,
    )


def request_worker_stop(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    if os.name == "nt":
        from app.runtime.windows_process import terminate_process_tree

        terminate_process_tree(child.pid)
    else:
        child.terminate()


def restart_worker(
    children: dict[str, subprocess.Popen],
    commands: dict[str, list[str]],
    environment: dict[str, str],
    failures: dict[str, int],
    started_at: dict[str, float],
) -> None:
    command = control_channel.read_json(control_channel.COMMAND_PATH)
    if not command:
        return
    request_id = command.get("request_id")
    state = {**command, "state": "restarting", "updated_at": control_channel.utc_iso()}
    try:
        if command.get("action") != "restart_worker" or not isinstance(request_id, str):
            raise RuntimeError("不支持的本地运行时控制命令")
        if "worker" not in children or "worker" not in commands:
            raise RuntimeError("当前 Supervisor 未启用 Worker")
        control_channel.write_json(control_channel.STATE_PATH, state)
        old_worker = children["worker"]
        request_worker_stop(old_worker)
        try:
            old_worker.wait(timeout=15)
        except subprocess.TimeoutExpired:
            terminate_child(old_worker)
            old_worker.wait(timeout=5)
        new_worker = start_child(commands["worker"], environment)
        children["worker"] = new_worker
        failures["worker"] = 0
        started_at["worker"] = time.monotonic()
        state.update({
            "state": "worker_started",
            "worker_pid": new_worker.pid,
            "updated_at": control_channel.utc_iso(),
        })
        control_channel.write_json(control_channel.STATE_PATH, state)
    except Exception as exc:
        state.update({
            "state": "failed",
            "message": str(exc),
            "updated_at": control_channel.utc_iso(),
        })
        control_channel.write_json(control_channel.STATE_PATH, state)
    finally:
        control_channel.remove_command()


def wait_until_ready(children: dict[str, subprocess.Popen], *, worker_enabled: bool) -> None:
    """Fail startup when the API/database/Worker unit never becomes ready."""
    host = settings.server_host
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    deadline = time.monotonic() + 30
    last_error = "服务尚未响应"
    while time.monotonic() < deadline:
        exited = {
            name: child.returncode
            for name, child in children.items()
            if child.poll() is not None
        }
        if exited:
            raise RuntimeError(f"后台子进程启动失败: {exited}")
        connection = http.client.HTTPConnection(host, settings.server_port, timeout=2)
        try:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            body = json.loads(response.read())
            database_ready = body.get("database") == "ok"
            worker_ready = body.get("execution", {}).get("ready") is True
            if response.status == 200 and database_ready and (worker_ready or not worker_enabled):
                return
            last_error = (
                f"health={body.get('status')}, database={body.get('database')}, "
                f"worker={body.get('execution', {}).get('status')}"
            )
        except (ConnectionError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        finally:
            connection.close()
        time.sleep(0.5)
    raise RuntimeError(f"后台启动超时: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev", action="store_true", help="API 开启代码自动重载")
    parser.add_argument("--no-worker", action="store_true", help="只用于不允许执行生成任务的维护场景")
    parser.add_argument("--stop-file", type=Path, help="本机启动器通过此文件请求停止")
    options = parser.parse_args()
    if windows_token_restricted():
        raise RuntimeError("当前进程受到 Windows 运行限制, 不能作为联网后台启动器")
    lock_descriptor = acquire_lock()
    children: dict[str, subprocess.Popen] = {}
    stopping = False

    def stop(_signum=None, _frame=None):
        nonlocal stopping
        stopping = True
        for child in children.values():
            terminate_child(child)

    try:
        from app.services.storage_service import apply_pending_local_path
        try:
            apply_pending_local_path()
        except Exception:
            # A failed migration must not make the existing installation unusable.
            print("存储迁移未完成，继续使用原目录。原件保留，请在存储设置核对迁移目标。", flush=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(PROJECT_ROOT / "apps/server/alembic.ini"),
                "upgrade",
                "head",
            ],
            cwd=PROJECT_ROOT,
            check=True,
        )
        commands = {
            "api": [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--app-dir",
                "apps/server",
                "--host",
                settings.server_host,
                "--port",
                str(settings.server_port),
            ],
        }
        if options.dev:
            commands["api"] += ["--reload", "--reload-dir", "apps/server/app"]
        if not options.no_worker:
            commands["worker"] = [sys.executable, "-m", "app.jobs.worker"]
        environment = {
            **os.environ,
            "PYTHONPATH": str(PROJECT_ROOT / "apps/server"),
            "PYTHONUTF8": "1",
        }
        for name, command in commands.items():
            children[name] = start_child(command, environment)
        for event in (signal.SIGINT, signal.SIGTERM):
            signal.signal(event, stop)
        failures = dict.fromkeys(children, 0)
        started_at = dict.fromkeys(children, time.monotonic())
        wait_until_ready(children, worker_enabled=not options.no_worker)
        while not stopping:
            if options.stop_file and options.stop_file.exists():
                stop()
                break
            restart_worker(children, commands, environment, failures, started_at)
            for name, child in list(children.items()):
                code = child.poll()
                if code is None:
                    continue
                if time.monotonic() - started_at[name] >= 60:
                    failures[name] = 0
                failures[name] += 1
                if failures[name] > 5:
                    raise RuntimeError(f"{name} 连续启动失败, 已停止自动重启 (最后退出码 {code})")
                time.sleep(min(8, failures[name] * 2))
                children[name] = start_child(commands[name], environment)
                started_at[name] = time.monotonic()
            time.sleep(0.5)
    finally:
        stop()
        deadline = time.monotonic() + 15
        for child in children.values():
            remaining = max(0, deadline - time.monotonic())
            try:
                child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                child.kill()
        os.close(lock_descriptor)
        LOCK_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
