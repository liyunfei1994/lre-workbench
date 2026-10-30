"""One isolated calculation per process; closing its controller pipe ends the worker."""
import argparse
import faulthandler
import json
import os
import sys
import threading
import time
import traceback
from webapp.store import Store
from webapp.solver import calculate, science_environment


def main():
    faulthandler.dump_traceback_later(120, repeat=True)
    print("Worker started.", flush=True)
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    store = Store(args.database)
    print("Workspace opened.", flush=True)
    job = store.one("SELECT * FROM jobs WHERE id=? AND status='running'", (args.job,))
    if not job:
        return 1
    # NumPy's Windows native import can deadlock when another thread is
    # already blocked reading a pipe. Load the scientific stack first.
    # No calculation starts until the controller watcher is connected.
    from gg_cycle import make_cycle_model
    science_environment()

    def controller_watch():
        # The controller holds stdin open without sending data. EOF also occurs
        # if the controller crashes, preventing an orphaned CPU-intensive job.
        if os.name == "nt":
            # A blocking CRT read on stdin holds a lock also needed by the
            # RocketCEA FORTRAN runtime. Probe the pipe with Win32 instead, so
            # native calculations can run while controller death is monitored.
            import ctypes
            from ctypes import wintypes
            import msvcrt
            peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
            peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                             wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID]
            peek.restype = wintypes.BOOL
            handle = msvcrt.get_osfhandle(sys.stdin.fileno())
            while peek(handle, None, 0, None, None, None):
                time.sleep(0.2)
        else:
            while os.read(sys.stdin.fileno(), 1):
                pass
        os._exit(70)
    threading.Thread(target=controller_watch, daemon=True).start()
    print("Controller connected. Starting calculation.", flush=True)

    def progress(iteration, maximum, residual):
        payload = json.dumps({"iteration": iteration, "maximum": maximum,
                              "residual": residual if abs(residual) < float("inf") else None},
                             allow_nan=False)
        store.execute("UPDATE jobs SET progress=? WHERE id=? AND status='running'",
                      (payload, args.job))
    try:
        if job.get("kind") in ("throttle", "transient", "feed_network", "chilldown", "ignition", "shutdown", "cooling_design"):
            if job["kind"] == "cooling_design":
                from webapp.cooling_design import calculate_cooling_design as calculate_study
            elif job["kind"] == "shutdown":
                from webapp.shutdown import calculate_shutdown as calculate_study
            elif job["kind"] == "ignition":
                from webapp.ignition import calculate_ignition as calculate_study
            elif job["kind"] == "chilldown":
                from webapp.chilldown import calculate_chilldown as calculate_study
            elif job["kind"] == "feed_network":
                from webapp.feed_network import calculate_feed_network as calculate_study
            elif job["kind"] == "transient":
                from webapp.transient import calculate_transient as calculate_study
            else:
                from webapp.throttle import calculate_throttle as calculate_study
            payload = json.loads(job["inputs"])
            source = store.one("SELECT * FROM jobs WHERE id=? AND kind='design' AND status='succeeded'",
                               (payload["source_job_id"],))
            if not source or source["project_id"] != job["project_id"]:
                raise ValueError("来源计算记录不存在或尚未完成。")
            source["inputs"], source["result"] = json.loads(source["inputs"]), json.loads(source["result"])

            def study_progress(status, partial):
                # Persist complete points atomically with progress. Cancellation,
                # timeout or controller shutdown must not erase prior solutions.
                store.execute("UPDATE jobs SET progress=?,result=? WHERE id=? AND status='running'",
                              (json.dumps(status, ensure_ascii=False, allow_nan=False),
                               json.dumps(partial, ensure_ascii=False, allow_nan=False), args.job))

            result = calculate_study(payload, source, study_progress)
        elif job.get("kind") == "validation":
            from webapp.validation import calculate_validation
            payload = json.loads(job["inputs"])
            source = store.one("SELECT * FROM jobs WHERE id=? AND kind='design' AND status='succeeded'",
                               (payload["source_job_id"],))
            if not source or source["project_id"] != job["project_id"]:
                raise ValueError("来源计算记录不存在或尚未完成。")
            source["inputs"], source["result"] = json.loads(source["inputs"]), json.loads(source["result"])
            def validation_progress(done, total, point):
                store.execute("UPDATE jobs SET progress=? WHERE id=? AND status='running'",
                              (json.dumps({"iteration": done, "maximum": total, "phase": "热化学对比"}), args.job))
            result = calculate_validation(payload, source, validation_progress)
        else:
            result = calculate(json.loads(job["inputs"]), progress, saved_snapshot=True)
        store.execute("UPDATE jobs SET status='succeeded',result=?,finished=? "
                      "WHERE id=? AND status='running'",
                      (json.dumps(result, ensure_ascii=False, allow_nan=False), time.time(), args.job))
        return 0
    except Exception as exc:
        traceback.print_exc()
        store.execute("UPDATE jobs SET status='failed',error=?,finished=? WHERE id=? AND status='running'",
                      (f"{type(exc).__name__}: {exc}"[:1600], time.time(), args.job))
        return 1
    finally:
        faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    raise SystemExit(main())
