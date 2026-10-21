"""Bounded single-host queue with cancellation, timeouts and process isolation."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent


class JobQueue:
    def __init__(self, store, timeout=600):
        self.store, self.timeout = store, timeout
        self.stop_event = threading.Event()
        self.thread = None
        self.lock_file = None

    def start(self):
        self.lock_file = (self.store.path.parent / "server.lock").open("a+b")
        try:
            if self.lock_file.tell() == 0:
                self.lock_file.write(b"0")
                self.lock_file.flush()
            self.lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError("此数据目录已有网页服务运行。")
        self.store.execute("UPDATE jobs SET status='interrupted',finished=?,error=? WHERE status='running'",
                           (time.time(), "服务曾中断，请重新提交任务。"))
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)
        if self.lock_file:
            self.lock_file.close()
            self.lock_file = None

    def run(self):
        while not self.stop_event.wait(0.25):
            job = self.store.one("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1")
            if not job:
                continue
            started = time.time()
            if not self.store.execute(
                "UPDATE jobs SET status='running',started=? WHERE id=? AND status='queued'",
                (started, job["id"])):
                continue
            process = None
            try:
                logs = self.store.path.parent / "job-logs"
                logs.mkdir(exist_ok=True)
                env = dict(os.environ, PYTHONUTF8="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
                with (logs / f"{job['id']}.log").open("wb") as log:
                    process = subprocess.Popen(
                        [sys.executable, "-m", "webapp.worker", "--database", str(self.store.path),
                         "--job", job["id"]], cwd=ROOT, env=env, stdin=subprocess.PIPE,
                        stdout=log, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    while process.poll() is None:
                        row = self.store.one("SELECT status FROM jobs WHERE id=?", (job["id"],))
                        if row["status"] != "running":
                            break
                        if self.stop_event.is_set() or time.time() - started > self.timeout:
                            status = "interrupted" if self.stop_event.is_set() else "timed_out"
                            message = "服务停止，任务已中断。" if self.stop_event.is_set() else "计算超过时间上限。"
                            self.store.execute(
                                "UPDATE jobs SET status=?,finished=?,error=? WHERE id=? AND status='running'",
                                (status, time.time(), message, job["id"]))
                            break
                        self.stop_event.wait(0.2)
            except Exception as exc:
                self.store.execute(
                    "UPDATE jobs SET status='failed',finished=?,error=? WHERE id=? AND status='running'",
                    (time.time(), f"无法执行任务：{type(exc).__name__}", job["id"]))
            finally:
                if process:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                    if process.stdin:
                        process.stdin.close()
                self.store.execute(
                    "UPDATE jobs SET status='failed',finished=?,error=? WHERE id=? AND status='running'",
                    (time.time(), "计算进程意外退出，请查看服务器任务日志。", job["id"]))
