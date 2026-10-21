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
        result = calculate(json.loads(job["inputs"]), progress)
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
