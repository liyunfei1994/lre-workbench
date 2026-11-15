"""Bounded, nested control matching for server-owned numerical models.

This module contains no engine model, characteristics, sizing, or design entry
point. Its bundled examples are dimensionless algebra. A local sensitivity
rank is a numerical diagnostic, not a global reachability certificate.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from numbers import Real
import math

import numpy as np

from lre_workbench.solvers.iteration import ResidualSpec, VariableSpec, solve_model, _json_outputs
from lre_workbench.runtime.tasks import checkpoint


SCENARIOS = ("reachable", "bound_limited", "inner_failure", "rank_deficient")


def _text(value, label):
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{label} must be a nonempty trimmed string")
    return value


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be finite")
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


@dataclass(frozen=True)
class ControlSpec:
    name: str
    label: str
    unit: str
    lower: float
    upper: float
    initial: float

    def __post_init__(self):
        for key in ("name", "label", "unit"):
            _text(getattr(self, key), key)
        for key in ("lower", "upper", "initial"):
            object.__setattr__(self, key, _number(getattr(self, key), key))
        if not self.lower < self.upper or not self.lower <= self.initial <= self.upper:
            raise ValueError("control requires lower < upper and an initial value inside bounds")
        _number(self.upper - self.lower, "control span")


@dataclass(frozen=True)
class TargetSpec:
    name: str
    label: str
    unit: str
    value: float
    tolerance: float

    def __post_init__(self):
        for key in ("name", "label", "unit"):
            _text(getattr(self, key), key)
        for key in ("value", "tolerance"):
            object.__setattr__(self, key, _number(getattr(self, key), key))
        if self.tolerance <= 0:
            raise ValueError("target tolerance must be positive")


def _specs(items, cls, label, *, empty=False):
    if not isinstance(items, (list, tuple)) or (not empty and not items):
        raise ValueError(f"{label} must be a {'possibly empty ' if empty else 'nonempty '}sequence")
    if len(items) > 8 or any(not isinstance(item, cls) for item in items):
        raise ValueError(f"{label} must contain at most 8 {cls.__name__} values")
    if len({item.name for item in items}) != len(items):
        raise ValueError(f"{label} names must be unique")
    return tuple(items)


def _settings(settings):
    settings = {} if settings is None else settings
    if not isinstance(settings, dict) or set(settings) - {"max_iter", "max_nfev"}:
        raise ValueError("only max_iter and max_nfev settings are supported")
    result = {"max_iter": 30, "max_nfev": 200} | settings
    for key, lower, upper in (("max_iter", 0, 30), ("max_nfev", 1, 200)):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise ValueError(f"{key} must be an integer in [{lower}, {upper}]")
    return result


class _Stop(Exception):
    def __init__(self, reason, message):
        self.reason, self.message = reason, message


def solve_trim(model, *, settings=None, progress_callback=None):
    """Match named targets using only explicitly bounded controls.

    The trusted model supplies model_id, controls, targets and solve_inner(dict).
    Each inner response supplies status='converged', valid=True,
    domain_valid=True, outputs, iterations, residual_norm <= 1 and points.
    A failed/invalid inner response supplies no outer residual. Every actual
    inner call is recorded, including sensitivity probes and rejected trials.
    Points contain only accepted states. Cancellation is allowed to propagate.

    This intentionally conservative solver stops at a failed inner evaluation;
    it does not infer physical infeasibility or continue using stale outputs.
    """
    model_id = _text(getattr(model, "model_id", None), "model_id")
    controls = _specs(getattr(model, "controls", None), ControlSpec, "controls", empty=True)
    targets = _specs(getattr(model, "targets", None), TargetSpec, "targets")
    if not callable(getattr(model, "solve_inner", None)):
        raise ValueError("solve_inner must be a server-defined callable")
    if progress_callback is not None and not callable(progress_callback):
        raise ValueError("progress_callback must be callable")
    options = _settings(settings)
    report = {
        "schema_version": 1, "analysis_type": "control_trim_iteration",
        "scope": "numerical_framework_only", "is_engine_result": False,
        "model_id": model_id, "status": "blocked", "reason": "not_started", "message": "尚未求解。",
        "controls": [asdict(item) for item in controls], "targets": [asdict(item) for item in targets],
        "dof": {"control_count": len(controls), "target_count": len(targets), "rank": None,
                "independence": "not_checked", "singular_values": [],
                "checked_at_iteration": None, "inner_evaluation_id": None, "controls": None},
        "points": [], "evaluations": [], "nfev": 0, "iterations": 0,
        "final_controls": None, "final_outputs": None, "last_valid_point": None,
        "evidence": None, "settings": options, "tolerance": 1.0,
        "limitations": [
            "仅为通用数值配平框架；不包含发动机物理、部件特性或定尺寸逻辑。",
            "控制灵敏度按控制范围与目标容差缩放；局部差分 SVD 满行秩不证明全局可达性或解的唯一性。",
            "边界未达只记录目标未满足；无独立证据时不认定物理无解。内层失败不参与外层残差。",
            "曲线中的接受步与全部内层调用分别记录；差分探针和拒绝步不是接受迭代。",
        ],
    }

    def finish(status, reason, message):
        report.update(status=status, reason=reason, message=message,
                      nfev=len(report["evaluations"]), iterations=max(0, len(report["points"]) - 1))
        if report["points"]:
            report["last_valid_point"] = deepcopy(report["points"][-1])
            if status != "numerical_failure":
                report["final_controls"] = dict(report["points"][-1]["controls"])
                report["final_outputs"] = dict(report["points"][-1]["outputs"])
        return report

    if len(controls) < len(targets):
        return finish("blocked", "insufficient_controls", "独立目标数超过可调控制数，尚未调用内层。")
    lower = np.array([item.lower for item in controls])
    upper = np.array([item.upper for item in controls])
    span = np.array([item.upper - item.lower for item in controls])
    names = [item.name for item in controls]
    z = (np.array([item.initial for item in controls]) - lower) / span

    def active_bounds(values):
        return [{"name": item.name, "bound": "lower" if value <= 1e-10 else "upper"}
                for item, value in zip(controls, values) if value <= 1e-10 or value >= 1 - 1e-10]

    def evaluate(values, phase):
        checkpoint()
        if len(report["evaluations"]) >= options["max_nfev"]:
            raise _Stop("evaluation_limit", "达到内层调用次数上限；未证明目标不可达。")
        if not np.all(np.isfinite(values)) or np.any(values < 0) or np.any(values > 1):
            raise _Stop("invalid_control", "外层试探值超出明确控制边界，未调用内层。")
        physical = np.minimum(np.maximum(lower + values * span, lower), upper)
        supplied = dict(zip(names, map(float, physical)))
        entry = {"id": len(report["evaluations"]) + 1, "phase": phase, "controls": supplied,
                 "inner_status": "not_returned", "inner_iterations": None, "inner_residual_norm": None,
                 "inner_points": [], "status": "failed", "error": None}
        report["evaluations"].append(entry)
        try:
            inner = _json_outputs(model.solve_inner(dict(supplied)))
            entry["inner_status"] = inner.get("status", "missing")
            inner_points = inner.get("points", [])
            if not isinstance(inner_points, list) or len(inner_points) > 100:
                raise ValueError("inner points must be a list of at most 100 accepted states")
            entry["inner_points"] = inner_points
            iterations = inner.get("iterations")
            if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 0:
                raise ValueError("inner iteration count must be a nonnegative integer")
            entry["inner_iterations"] = iterations
            norm = _number(inner.get("residual_norm"), "inner residual norm")
            entry["inner_residual_norm"] = norm
            if (inner.get("status") != "converged" or inner.get("valid") is not True
                    or inner.get("domain_valid") is not True or not 0 <= norm <= 1):
                raise ValueError(inner.get("error") or "inner convergence, validity or domain check failed")
            outputs = inner.get("outputs")
            if not isinstance(outputs, dict):
                raise ValueError("inner outputs must be a dictionary")
            selected = {item.name: _number(outputs.get(item.name), item.name) for item in targets}
            residuals = np.array([_number((selected[item.name] - item.value) / item.tolerance,
                                         item.name + " residual") for item in targets])
            entry["status"] = "valid"
            entry["outputs"] = selected
            return residuals, selected, entry["id"]
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            raise _Stop("inner_evaluation_failed", "内层未返回有效收敛解，外层已停止；此为数值/模型失败，不是目标不可达。") from exc

    def accept(values, residuals, outputs, evaluation_id):
        point = {"iteration": len(report["points"]),
                 "controls": dict(report["evaluations"][evaluation_id - 1]["controls"]),
                 "outputs": dict(outputs), "residuals": dict(zip([item.name for item in targets], map(float, residuals))),
                 "residual_norm": float(np.max(np.abs(residuals))),
                 "active_bounds": active_bounds(values), "inner_evaluation_id": evaluation_id}
        report["points"].append(point)
        if progress_callback is not None:
            progress_callback(deepcopy(point))

    try:
        residuals, outputs, evaluation_id = evaluate(z, "initial")
        accept(z, residuals, outputs, evaluation_id)
        for iteration in range(options["max_iter"] + 1):
            checkpoint()
            jacobian = np.empty((len(targets), len(controls)))
            for index in range(len(controls)):
                probe = z.copy()
                h = 1e-5 if z[index] <= 0.5 else -1e-5
                probe[index] += h
                probed, _, _ = evaluate(probe, "sensitivity")
                jacobian[:, index] = (probed - residuals) / h
            if not np.all(np.isfinite(jacobian)):
                raise _Stop("nonfinite_sensitivity", "控制灵敏度不是有限数值；未证明目标不可达。")
            u, singular, vh = np.linalg.svd(jacobian, full_matrices=False)
            threshold = max(1e-12, float(singular[0]) * 1e-8)
            rank = int(np.sum(singular > threshold))
            report["dof"].update(rank=rank, singular_values=singular.tolist(),
                                 independence="full_row_rank" if rank == len(targets) else "rank_deficient",
                                 checked_at_iteration=report["points"][-1]["iteration"],
                                 inner_evaluation_id=report["points"][-1]["inner_evaluation_id"],
                                 controls=dict(report["points"][-1]["controls"]))
            if rank < len(targets):
                return finish("blocked", "rank_deficient", "局部控制灵敏度不满行秩，不能将这些目标视为可独立调节；不等同于全局无解。")
            if np.max(np.abs(residuals)) <= 1:
                return finish("converged", "target_tolerances_met", "内层有效且所列目标均满足各自容差。")
            if iteration == options["max_iter"]:
                return finish("numerical_failure", "iteration_limit", "达到外层迭代上限；未证明目标不可达。")
            step = -vh.T @ ((u.T @ residuals) / singular)
            if not np.all(np.isfinite(step)):
                raise _Stop("nonfinite_step", "控制步长无效；未证明目标不可达。")
            # Backtracking on bounded trial values, with a max-norm merit. This
            # cannot expand control authority or modify the supplied hardware.
            advanced = False
            for backtrack in range(16):
                trial = np.clip(z + (0.5 ** backtrack) * step, 0.0, 1.0)
                if np.max(np.abs(trial - z)) <= 1e-12:
                    continue
                new_residuals, new_outputs, new_id = evaluate(trial, "trial")
                if np.max(np.abs(new_residuals)) < np.max(np.abs(residuals)):
                    z, residuals, outputs = trial, new_residuals, new_outputs
                    accept(z, residuals, outputs, new_id)
                    advanced = True
                    break
            if not advanced:
                if active_bounds(z):
                    return finish("target_not_met", "bound_limited", "控制量已触及限位且本次局部搜索未满足目标；尚无全域不可达证明。")
                return finish("numerical_failure", "line_search_failed", "局部搜索未找到改善步；不能据此判定物理无解。")
    except _Stop as exc:
        return finish("numerical_failure", exc.reason, exc.message)
    except np.linalg.LinAlgError:
        return finish("numerical_failure", "sensitivity_factorization_failed", "控制灵敏度分解失败；未证明目标不可达。")


class _AlgebraTrim:
    """Self-check equations only; controls are not engine valve identities."""
    model_id = "dimensionless_nested_control_v1"
    controls = (ControlSpec("u_a", "代数控制 u_a", "1", 0, 1, .2),
                ControlSpec("u_b", "代数控制 u_b", "1", 0, 1, .2))

    def __init__(self, scenario):
        self.scenario = scenario
        self.model_id = f"dimensionless_nested_control_v1:{scenario}"
        self.targets = (TargetSpec("x", "代数输出 x", "1", 1.6 if scenario == "bound_limited" else 1.25, 1e-6),
                        TargetSpec("y", "代数输出 y", "1", 2.5 if scenario == "rank_deficient" else 1.15, 1e-6))

    def solve_inner(self, controls):
        from types import SimpleNamespace
        a, b = controls["u_a"], controls["u_b"]
        coupled = self.scenario == "rank_deficient"
        if self.scenario == "inner_failure" and a > .3:
            return {"status": "failed", "valid": False, "domain_valid": True,
                    "outputs": {}, "iterations": 0, "residual_norm": 2.0, "points": [],
                    "error": "故意注入的代数内层失败，用于检查失败隔离。"}
        model = SimpleNamespace(
            model_id="nested_algebra_inner_v1",
            variables=(VariableSpec("x", "内层变量 x", "1", 1.1, .5, 2, 1),
                       VariableSpec("y", "内层变量 y", "1", 1.1, .5, 4, 1)),
            residuals=(ResidualSpec("r_x", "x² − (1 + u_a)", "1", 1, 1e-12, "algebra"),
                       ResidualSpec("r_y", "第二条代数残差", "1", 1, 1e-12, "algebra")),
            evaluate=lambda state: {"residuals": {
                "r_x": state["x"] ** 2 - (1 + a + (b if coupled else 0)),
                "r_y": state["y"] - 2 * state["x"] if coupled else state["y"] ** 2 - (1 + b),
            }, "outputs": dict(state)})
        inner = solve_model(model, settings={"max_iter": 15, "max_nfev": 100})
        return {"status": "converged" if inner["status"] == "pass" else "failed",
                "valid": inner["status"] == "pass", "domain_valid": True,
                "outputs": inner["outputs"] or {}, "iterations": inner["iterations"],
                "residual_norm": inner["points"][-1]["residual_norm"] if inner["points"] else 2.0,
                "points": inner["points"], "error": inner["error"]}


def run_trim_framework_check(scenario="reachable", *, settings=None, progress_callback=None):
    """Run one bounded server-owned algebra example, without engine inputs."""
    if scenario not in SCENARIOS:
        raise ValueError("unknown control trim framework scenario")
    report = solve_trim(_AlgebraTrim(scenario), settings=settings, progress_callback=progress_callback)
    report.update(analysis_type="control_trim_framework_check", scenario=scenario)
    report["limitations"].insert(0, "双层无量纲代数自检；u_a、u_b 不是发动机阀门，x、y 不是推力、混合比或温度。")
    # This certificate is specific to the published algebra equations and the
    # complete control box. No generic adapter or browser input can assert it.
    if scenario == "bound_limited" and report["status"] == "target_not_met":
        report.update(status="target_unreachable", reason="certified_algebra_bound",
                      message="此代数自检目标不可达：全控制范围内 x ≤ √2，而目标为 1.6，超出容差。")
        report["evidence"] = {"kind": "analytic_global_bound", "model_id": report["model_id"], "scenario": scenario,
                              "domain": {"u_a": [0, 1], "u_b": [0, 1]}, "output": "x",
                              "upper": math.sqrt(2), "target": 1.6, "tolerance": 1e-6,
                              "statement": "x² = 1 + u_a，x > 0 且 0 ≤ u_a ≤ 1，因此 x ≤ √2。"}
    return report
