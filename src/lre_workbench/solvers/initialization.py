"""Structured evidence for preparation before a nonlinear iteration begins."""
from copy import deepcopy
from time import perf_counter
from traceback import extract_tb


def _exception_location(exc):
    frames = extract_tb(exc.__traceback__)
    if not frames:
        return None
    frame = frames[-1]
    return dict(path=frame.filename, function=frame.name, line=frame.lineno)


class SolverInitializationError(RuntimeError):
    """A failed initial attempt is not evidence that the equations have no root."""

    def __init__(self, message, diagnostics):
        super().__init__(message)
        self.initialization_diagnostics = deepcopy(diagnostics)


class InitializationTrace:
    def __init__(self, inputs):
        self.started = perf_counter()
        self.data = dict(schema_version=1, status="running", stage="initialization",
            formal_iteration_started=False, attempted_inputs=deepcopy(inputs),
            variable_names=[], variable_units=[], x=[], steps=[], failure=None,
            counts=dict(forward_attempted=0, forward_completed=0,
                        inversion_attempted=0, inversion_completed=0,
                        property_attempted=0, property_completed=0),
            counting_scope={"forward": "完整前向模型调用",
                            "inversion": "预燃室温度反求调用",
                            "property": "仅温度反求中的热化学调用；前向部件内部调用未计数"},
            component_internal_property_calls=None)

    def vector(self, x, names, units):
        self.data.update(x=list(x), variable_names=list(names), variable_units=list(units))

    def run(self, identifier, label, component, function, *, inputs=None, units=None, kind=None):
        step = dict(id=identifier, label=label, component=component, status="running",
                    attempted_inputs=deepcopy(inputs or {}), input_units=dict(units or {}),
                    calls_attempted=1, calls_completed=0)
        self.data["steps"].append(step)
        if kind:
            self.data["counts"][kind + "_attempted"] += 1
        try:
            result = function()
        except Exception as exc:
            # TaskCancelled inherits BaseException and must bypass this wrapper.
            explicit_component = getattr(exc, "component", "")
            located_component = explicit_component or component
            if kind == "forward" and not explicit_component:
                located_component += "（具体部件未定位）"
            failure = dict(stage=identifier, component=located_component,
                           component_identified=bool(explicit_component) or kind != "forward",
                           error_type=type(exc).__name__, message=str(exc),
                           location=_exception_location(exc))
            for key in ("variable", "equation"):
                if getattr(exc, key, None):
                    failure[key] = getattr(exc, key)
            step.update(status="failed", error_type=failure["error_type"], message=failure["message"])
            self.data.update(status="failed", failure=failure)
            raise SolverInitializationError(
                f"初始化失败（{label}）：{exc}。尚未开始正式迭代，不能据此判定物理无解。",
                self.snapshot()) from exc
        step.update(status="passed", calls_completed=1)
        if kind:
            self.data["counts"][kind + "_completed"] += 1
        return result

    def property_call(self, function, *, inputs=None, units=None, labels=None, component="热化学物性"):
        # The latest failed candidate remains available even when subsequent
        # candidates succeed but still cannot bracket the requested temperature.
        evidence = dict(component=component, status="running", attempted_inputs=deepcopy(inputs or {}),
                        input_units=dict(units or {}), input_labels=dict(labels or {}))
        self.data["last_property_call"] = evidence
        self.data["counts"]["property_attempted"] += 1
        try:
            result = function()
        except Exception as exc:
            evidence.update(status="failed", error_type=type(exc).__name__, message=str(exc),
                            location=_exception_location(exc))
            self.data["last_property_failure"] = deepcopy(evidence)
            raise
        evidence["status"] = "passed"
        self.data["counts"]["property_completed"] += 1
        return result

    def snapshot(self, status=None):
        result = deepcopy(self.data)
        if status is not None:
            result["status"] = status
        result["elapsed_seconds"] = perf_counter() - self.started
        return result
