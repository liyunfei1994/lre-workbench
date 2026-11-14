"""Observation-only iteration events, independent of solver return payloads.

Every point is emitted where it is computed. Run boundaries distinguish nested
solves and model-evaluation thermal closure from accepted engine iterates.
"""
from contextlib import contextmanager
from functools import wraps
import math
from time import perf_counter


class MonitorCallbackError(Exception):
    """A failed observation sink is not a model-domain or convergence failure."""


def _finite(value):
    if isinstance(value, dict):
        return {str(k): _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class IterationMonitor:
    def __init__(self, callback):
        self.callback = callback
        self.started = perf_counter()
        self.serial = 0
        self.runs = []
        self.failed = False

    def emit(self, model, kind, layer, *, values=None, units=None, **detail):
        if self.failed:
            return  # Never mask the original sink error while unwinding layers.
        run = next((r for r in reversed(self.runs) if r["layer"] == layer), None)
        if run is None:
            return
        context = dict(getattr(model, "_convergence_context", {}))
        context.update(run["context"])
        context.update(detail.pop("context", {}))
        iteration = detail.pop("iteration", None)
        if iteration is None:
            iteration = run.get("iteration", 0)
        run["iteration"] = iteration
        try:
            self.callback(_finite(dict(schema_version=1, kind=kind, layer=layer,
                run_id=run["id"], elapsed_seconds=perf_counter()-self.started,
                context=context, iteration=iteration,
                values=values or {}, units=units or {}, **detail)))
        except Exception as exc:
            self.failed = True
            raise MonitorCallbackError(f"迭代监控记录失败（{layer}/{run['id']}）：{exc}") from exc


def emit_monitor(model, kind, layer, **detail):
    monitor = getattr(model, "_iteration_monitor", None)
    if monitor is not None:
        monitor.emit(model, kind, layer, **detail)


@contextmanager
def monitor_run(model, layer, **context):
    monitor = getattr(model, "_iteration_monitor", None)
    if monitor is None:
        yield
        return
    monitor.serial += 1
    parent_context = monitor.runs[-1]["context"] if monitor.runs else {}
    context = {**parent_context, **{r["layer"] + "_run_id": r["id"] for r in monitor.runs},
               "parent_run_id": monitor.runs[-1]["id"] if monitor.runs else None, **context}
    if layer == "thermal" and context.get("scope") == "model_evaluation" and "inner_run_id" not in context:
        context.setdefault("evaluation_role", "initialization")
    run = dict(id=f"{layer}-{monitor.serial}", layer=layer, context=context)
    monitor.runs.append(run)
    try:
        monitor.emit(model, "start", layer)
        yield
    except BaseException as exc:
        monitor.emit(model, "end", layer, status="interrupted" if not isinstance(exc, Exception) else "fail",
                     termination_reason=type(exc).__name__, error=str(exc))
        raise
    finally:
        monitor.runs.remove(run)


def monitored(layer, **context):
    """Wrap a solver run without changing its numerical inputs or exceptions."""
    def decorate(function):
        @wraps(function)
        def wrapped(model, *args, **kwargs):
            with monitor_run(model, layer, **context):
                result = function(model, *args, **kwargs)
                payload = result[1] if isinstance(result, tuple) else result
                state = (getattr(payload, "convergence", None) or {}).get("layers", {}).get(layer, {})
                emit_monitor(model, "end", layer, status=state.get("status", "pass"),
                    termination_reason=state.get("termination_reason", "completed"),
                    iteration=state.get("iterations"))
                return result
        return wrapped
    return decorate


def set_evaluation_context(model, detail):
    monitor = getattr(model, "_iteration_monitor", None)
    if monitor is not None:
        run = next((r for r in reversed(monitor.runs) if r["layer"] == "inner"), None)
        if run is not None:
            run["context"].update(evaluation_role=detail["stage"], model_evaluation=detail["evaluation"],
                                  probe_variable=detail.get("variable"))
