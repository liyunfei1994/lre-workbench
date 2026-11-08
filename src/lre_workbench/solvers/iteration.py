"""Typed, named residual-model adapter and an engine-independent numerical check.

Models are assembled by server-side Python code. This module does not parse
formulas, load user code, supply component characteristics, or certify a physical
model. A successful solve means only that the supplied residual tolerances were
met. In particular, equal equation/variable counts do not prove independence.
"""
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
import json
import math
from numbers import Real
from typing import Protocol

from lre_workbench.solvers.nonlinear import solve_nonlinear


def _text(value, name):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a nonempty, trimmed string")
    return value


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    try:
        value = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{name} must be {'positive and ' if positive else ''}finite")
    return value


@dataclass(frozen=True)
class VariableSpec:
    name: str
    label: str
    unit: str
    initial: float
    lower: float
    upper: float
    scale: float

    def __post_init__(self):
        for key in ("name", "label", "unit"):
            _text(getattr(self, key), f"variable.{key}")
        for key in ("initial", "lower", "upper", "scale"):
            object.__setattr__(self, key, _number(
                getattr(self, key), f"{self.name}.{key}", positive=key == "scale"))
        if self.lower >= self.upper:
            raise ValueError(f"{self.name}: lower must be smaller than upper")
        if not self.lower <= self.initial <= self.upper:
            raise ValueError(f"{self.name}: initial must lie within the supplied bounds")


@dataclass(frozen=True)
class ResidualSpec:
    name: str
    label: str
    unit: str
    scale: float
    tolerance: float
    group: str

    def __post_init__(self):
        for key in ("name", "label", "unit", "group"):
            _text(getattr(self, key), f"residual.{key}")
        for key in ("scale", "tolerance"):
            object.__setattr__(self, key, _number(
                getattr(self, key), f"{self.name}.{key}", positive=True))
        _number(self.scale * self.tolerance,
                f"{self.name}.scale*tolerance", positive=True)


class ResidualModel(Protocol):
    """In-process interface; model discovery/authorization belongs to the caller."""

    model_id: str
    variables: Sequence[VariableSpec]
    residuals: Sequence[ResidualSpec]

    def evaluate(self, named_state: dict[str, float]) -> dict:
        """Return exactly {'residuals': {name: raw_value}, 'outputs': {...}}."""
        ...


def _specs(values, expected_type, name):
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        raise ValueError(f"model.{name} must be a nonempty sequence of {expected_type.__name__}")
    result = tuple(values)
    if any(not isinstance(value, expected_type) for value in result):
        raise ValueError(f"model.{name} must contain only {expected_type.__name__}")
    if len({value.name for value in result}) != len(result):
        raise ValueError(f"model.{name} names must be unique")
    return result


def _integer(value, name, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _settings(settings, size, *, capped=False):
    if settings is None:
        settings = {}
    if not isinstance(settings, Mapping):
        raise ValueError("settings must be a mapping")
    unknown = set(settings) - {"backend", "max_iter", "max_nfev"}
    if unknown:
        raise ValueError(f"unsupported solver settings: {sorted(map(str, unknown))}")
    backend = settings.get("backend", "newton")
    if backend not in ("newton", "broyden", "trf"):
        raise ValueError("backend must be 'newton', 'broyden' or 'trf'")
    max_iter = _integer(settings.get("max_iter", 50), "max_iter", 0)
    max_nfev = _integer(settings.get("max_nfev", max(1, 2 * (size + 1) * (max_iter + 1))),
                        "max_nfev", 1)
    if capped and (max_iter > 50 or max_nfev > 400):
        raise ValueError("framework check is limited to 50 iterations and 400 evaluations")
    return dict(backend=backend, max_iter=max_iter, max_nfev=max_nfev)


def _json_outputs(outputs):
    """Snapshot outputs without silently coercing non-JSON keys or values."""
    if not isinstance(outputs, dict):
        raise ValueError("model outputs must be a JSON-compatible dict")

    def validate(value):
        if value is None or isinstance(value, (str, bool)):
            return
        if isinstance(value, int):
            return
        if isinstance(value, float) and math.isfinite(value):
            return
        if isinstance(value, list):
            for item in value:
                validate(item)
            return
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            for item in value.values():
                validate(item)
            return
        raise ValueError("model outputs must contain only finite JSON values and string keys")

    validate(outputs)
    return json.loads(json.dumps(outputs, allow_nan=False))


def solve_model(model: ResidualModel, progress_callback=None, settings=None):
    """Iterate a server-defined model; return only actual accepted trace points.

    Invalid specifications/settings raise ValueError before any evaluation.
    Failed model evaluations and non-convergence retain the last accepted trace
    and state. Cooperative TaskCancelled is intentionally not caught. A callback
    receives an isolated copy of each accepted point, including the initial one.

    ResidualSpec.tolerance applies to raw/scale, so each underlying solver scale
    is scale*tolerance and the common acceptance threshold is exactly 1.
    """
    model_id = _text(getattr(model, "model_id", None), "model_id")
    variables = _specs(getattr(model, "variables", None), VariableSpec, "variables")
    residual_specs = _specs(getattr(model, "residuals", None), ResidualSpec, "residuals")
    if len(variables) != len(residual_specs):
        raise ValueError("a square model is required: variable and residual counts must match")
    if not callable(getattr(model, "evaluate", None)):
        raise ValueError("model.evaluate must be a server-defined callable")
    if progress_callback is not None and not callable(progress_callback):
        raise ValueError("progress_callback must be callable")
    settings = _settings(settings, len(variables))
    variable_names = [spec.name for spec in variables]
    residual_names = [spec.name for spec in residual_specs]
    expected_keys = set(residual_names)
    groups = {}
    for spec in residual_specs:
        groups.setdefault(spec.group, []).append(spec.name)

    def checked_evaluate(values):
        response = model.evaluate(dict(zip(variable_names, values)))
        if not isinstance(response, dict) or set(response) != {"residuals", "outputs"}:
            raise ValueError("model response must contain exactly residuals and outputs")
        residuals = response["residuals"]
        if not isinstance(residuals, dict) or set(residuals) != expected_keys:
            actual = set(residuals) if isinstance(residuals, dict) else set()
            missing = sorted(expected_keys - actual)
            extra = sorted(map(str, actual - expected_keys))
            raise ValueError(f"residual keys do not match specifications; missing={missing}; extra={extra}")
        raw = [_number(residuals[spec.name], f"residual {spec.name}") for spec in residual_specs]
        # Preserve the meaning of raw/scale even when tolerance is unusually large.
        for spec, value in zip(residual_specs, raw):
            _number(value / spec.scale, f"scaled residual {spec.name}")
        return raw, _json_outputs(response["outputs"])

    last_model_error = None
    model_error_count = 0

    def evaluate(values):
        nonlocal last_model_error, model_error_count
        try:
            return checked_evaluate(values)
        except Exception as exc:
            # The numerical solver can replace its error with a higher-level
            # Jacobian/line-search failure. Retain the originating model error.
            last_model_error = f"{type(exc).__name__}: {exc}"
            model_error_count += 1
            raise

    points = []

    def accepted(point):
        ratios = dict(point["residuals"])
        converted = {
            "iteration": point["iteration"],
            "variables": dict(zip(variable_names, point["x"])),
            "raw_residuals": dict(point["raw_residuals"]),
            "scaled_residuals": {
                spec.name: ratios[spec.name] * spec.tolerance for spec in residual_specs},
            "residuals": ratios,
            "residual_norm": point["residual_norm"],
            "nfev": point["nfev"],
        }
        for key in ("merit", "step_length", "step_norm", "direction", "acceptance_norm",
                    "jacobian_rebuilds", "approximate_updates", "elapsed_seconds", "step_reductions", "reduction_reasons"):
            if key in point:
                converted[key] = point[key]
        points.append(converted)
        if progress_callback is not None:
            progress_callback(deepcopy(converted))

    result = solve_nonlinear(
        evaluate,
        [spec.initial for spec in variables],
        [spec.lower for spec in variables],
        [spec.upper for spec in variables],
        residual_scales=[spec.scale * spec.tolerance for spec in residual_specs],
        variable_scales=[spec.scale for spec in variables],
        variable_names=variable_names, variable_units=[spec.unit for spec in variables],
        equation_labels=[spec.label for spec in residual_specs],
        equation_units=[spec.unit for spec in residual_specs],
        equation_components=[spec.group for spec in residual_specs],
        residual_tolerances=[spec.scale * spec.tolerance for spec in residual_specs],
        names=residual_names, tol=1.0, progress_callback=accepted, **settings)
    return {
        "schema_version": 1,
        "analysis_type": "residual_model_iteration",
        "scope": "numerical_framework_only",
        "is_engine_result": False,
        "model_id": model_id,
        "status": "pass" if result.passed else "fail",
        "reason": result.reason,
        "error": result.error,
        "last_model_error": last_model_error,
        "model_error_count": model_error_count,
        "variables": [asdict(spec) for spec in variables],
        "residual_specs": [asdict(spec) for spec in residual_specs],
        "groups": groups,
        "points": points,
        "final_state": dict(zip(variable_names, result.x)) if points else None,
        "outputs": result.payload,
        "nfev": result.nfev,
        "njev": result.njev,
        "rejected_steps": result.rejected_steps,
        "iterations": result.iterations,
        "settings": settings,
        "tolerance": 1.0,
        "backend_status": result.backend_status,
        "diagnostics": result.diagnostics,
        "equation_count": len(residual_specs),
        "variable_count": len(variables),
        "equation_independence": "local_jacobian_only" if (result.diagnostics.get("jacobian") or {}).get("rank") is not None else "not_checked",
        "limitations": [
            "仅检查所提供方程的数值残差，不证明部件模型或发动机结果有效。",
            "雅可比数值秩仅描述已计算点附近的线性敏感性，不证明全局方程独立性或解的唯一性；未构建雅可比时不作秩判定。",
            "曲线只包含初始点和接受步；差分探针、拒绝步与失败调用计入 nfev，不绘作接受迭代。",
        ],
    }


class _FrameworkCheckModel:
    """Dimensionless coupled algebra, deliberately unrelated to engine physics."""

    model_id = "dimensionless_coupled_algebra_v1"
    variables = (
        VariableSpec("x", "无量纲变量 x", "1", 1.5, 1.5, 2.5, 1.0),
        VariableSpec("y", "无量纲变量 y", "1", 1.4, 0.5, 1.5, 1.0),
    )
    residuals = (
        ResidualSpec("equation_x", "x² + y − 5", "1", 1.0, 1e-8, "algebra"),
        ResidualSpec("equation_y", "y² + x − 3", "1", 1.0, 1e-8, "algebra"),
    )

    def evaluate(self, named_state):
        x, y = named_state["x"], named_state["y"]
        return {"residuals": {"equation_x": x * x + y - 5,
                              "equation_y": y * y + x - 3},
                "outputs": {"x": x, "y": y}}


def run_framework_check(settings=None, progress_callback=None):
    """Run fixed dimensionless equations, never accepting user model code/data.

    Known root: x=2, y=1. Positive bounds exclude the other real roots. Resource
    settings may reduce work but cannot exceed 50 iterations / 400 model calls.
    This checks numerical plumbing, not any engine operating point.
    """
    settings = _settings(settings, 2, capped=True)
    result = solve_model(_FrameworkCheckModel(), progress_callback, settings)
    result["analysis_type"] = "numerical_framework_check"
    result["known_root"] = {"x": 2.0, "y": 1.0}
    result["limitations"].insert(0, "无量纲代数自检；不包含发动机部件特性、物理方程或发动机计算结果。")
    return result
