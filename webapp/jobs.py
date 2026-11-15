"""Bounded single-host queue with cancellation, timeouts and process isolation."""
import os
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
BM_EOS = "Peng-Robinson-Boston-Mathias"
DEFAULT_TIMEOUT_SECONDS = 600
BM_DESIGN_TIMEOUT_SECONDS = 3600
BM_COMPARISON_TIMEOUT_SECONDS = 4200


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return value if isinstance(value, dict) else {}


class JobQueue:
    def __init__(self, store, timeout=None):
        # Omitted means bounded scientific budgets. An explicit number, even
        # 0 or 600, remains the operator/test's uniform hard limit.
        if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                    or not math.isfinite(timeout) or timeout < 0):
            raise ValueError("任务时间上限须为有限非负秒数；省略时使用有界科学任务策略。")
        self.store = store
        self.timeout_override = timeout
        self.timeout = DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout
        self.stop_event = threading.Event()
        self.thread = None
        self.lock_file = None

    def timeout_policy(self):
        override = self.timeout_override
        return {"version": "bounded_scientific_jobs_v1",
                "mode": "explicit_uniform_limit" if override is not None else "scientific_job_type",
                "explicit_override_seconds": override,
                "default_seconds": self.timeout,
                "bm_design_seconds": BM_DESIGN_TIMEOUT_SECONDS if override is None else override,
                "bm_eos_comparison_seconds": BM_COMPARISON_TIMEOUT_SECONDS if override is None else override,
                "bm_eos": BM_EOS,
                "basis": "saved_job_inputs_and_same_project_revision_source",
                "queue_wait_included": False}

    def timeout_for_job(self, job):
        if self.timeout_override is not None:
            return self.timeout_override
        values = _object(job.get("inputs"))
        if job.get("kind") == "design" and values.get("gas_eos") == BM_EOS:
            return BM_DESIGN_TIMEOUT_SECONDS
        if (job.get("kind") == "validation"
                and _object(values.get("gas_eos_comparison")).get("enabled") is True
                and isinstance(values.get("source_job_id"), str)):
            source = self.store.one(
                "SELECT inputs,result FROM jobs WHERE id=? AND project_id=? AND revision=? "
                "AND kind='design' AND status='succeeded' AND result IS NOT NULL",
                (values["source_job_id"], job.get("project_id"), job.get("revision")))
            if source:
                inputs, result = _object(source.get("inputs")), _object(source.get("result"))
                states = _object(_object(result.get("calculation")).get("states"))
                model = _object(_object(_object(states.get("ox_preburner_outlet")).get("metadata")).get("model"))
                # The comparison uses saved station identity before legacy inputs.
                if model.get("eos", inputs.get("gas_eos", "ideal-gas")) == BM_EOS:
                    return BM_COMPARISON_TIMEOUT_SECONDS
        return DEFAULT_TIMEOUT_SECONDS

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
            if job.get("kind") == "throttle":
                # An old queued design rematch must not resume as an operating
                # analysis after an upgrade. Keep any partial historical result.
                self.store.execute(
                    "UPDATE jobs SET status='failed',finished=?,error=? WHERE id=? AND status='queued'",
                    (time.time(), "旧设计匹配扫描已停止执行；请重新提交固定几何节流分析。", job["id"]))
                continue
            started = time.time()
            if not self.store.execute(
                "UPDATE jobs SET status='running',started=? WHERE id=? AND status='queued'",
                (started, job["id"])):
                continue
            process = None
            log_path = self.store.path.parent / "job-logs" / f"{job['id']}.log"
            try:
                timeout = self.timeout_for_job(job)
                elapsed_start = time.monotonic()
                log_path.parent.mkdir(exist_ok=True)
                env = dict(os.environ, PYTHONUTF8="1", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
                with log_path.open("wb") as log:
                    log.write(f"Execution budget: {timeout:g} seconds ({self.timeout_policy()['mode']}).\n".encode("utf-8"))
                    log.flush()
                    process = subprocess.Popen(
                        [sys.executable, "-m", "webapp.worker", "--database", str(self.store.path),
                         "--job", job["id"]], cwd=ROOT, env=env, stdin=subprocess.PIPE,
                        stdout=log, stderr=subprocess.STDOUT,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    while process.poll() is None:
                        row = self.store.one("SELECT status FROM jobs WHERE id=?", (job["id"],))
                        if row["status"] != "running":
                            break
                        if self.stop_event.is_set() or time.monotonic() - elapsed_start > timeout:
                            status = "interrupted" if self.stop_event.is_set() else "timed_out"
                            message = "服务停止，任务已中断。" if self.stop_event.is_set() else f"计算超过本任务时间上限（{timeout:g} 秒）。"
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
                message = "计算进程意外退出，请查看服务器任务日志。"
                if process:
                    exit_code = process.poll()
                    if exit_code is None:
                        process.terminate()
                        try:
                            exit_code = process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            exit_code = process.wait(timeout=3)
                    if process.stdin:
                        process.stdin.close()
                    if exit_code is not None:
                        status = str(exit_code)
                        if exit_code > 255 or (os.name == "nt" and exit_code < 0):
                            status += f" (0x{exit_code & 0xFFFFFFFF:08X})"
                        message = f"计算进程意外退出（退出码 {status}），请查看服务器任务日志。"
                        try:
                            with log_path.open("ab") as log:
                                log.write(f"\nWorker PID {process.pid}, exit code {status}.\n".encode("ascii"))
                        except OSError:
                            pass  # The exit code is still included in a failed job's error field.
                self.store.execute(
                    "UPDATE jobs SET status='failed',finished=?,error=? WHERE id=? AND status='running'",
                    (time.time(), message, job["id"]))
