"""Bounded, scaled nonlinear equation solvers with auditable accepted iterates.

All backends solve exactly the same normalized equations. Optimizer success
is deliberately separate from equation acceptance: every component must meet
its physical tolerance (default ``residual_scale * tol``). Function probes and rejected trial
steps count as work, but never appear as accepted iterations in the trace.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

import numpy as np

from lre_workbench.runtime.tasks import checkpoint


@dataclass
class NonlinearResult:
    x: list[float]
    residuals: list[float]
    payload: Any
    passed: bool
    reason: str
    points: list[dict]
    nfev: int
    njev: int
    rejected_steps: int
    residual_scales: list[float]
    variable_scales: list[float]
    backend: str = "newton"
    error: str | None = None
    backend_status: int | None = None
    evaluation_budget: int = 0
    diagnostics: dict = field(default_factory=dict)

    @property
    def iterations(self):
        return max(0, len(self.points) - 1)


@dataclass
class _Evaluation:
    x: np.ndarray
    raw: np.ndarray
    scaled: np.ndarray
    payload: Any

    @property
    def norm(self):
        return float(np.max(np.abs(self.scaled)))

    @property
    def merit(self):
        return float(0.5 * np.dot(self.scaled, self.scaled))


class _EvaluationFailure(Exception):
    pass


class _JacobianFailure(Exception):
    pass


class _EvaluationBudgetReached(Exception):
    pass


class ModelDomainError(ValueError):
    """A model-declared domain violation; generic failures are not domain proof."""

    def __init__(self, message, *, component="", variable=None, equation=""):
        super().__init__(message)
        self.component = component
        self.variable = variable
        self.equation = equation


def initial_evaluation_diagnostics(exc, values, *, backend="newton"):
    """Schema-compatible evidence when a caller fails before choosing scales.

    The caller has attempted exactly one model evaluation. No residual,
    tolerance, Jacobian, timing, or component identity is inferred.
    """
    domain_error = isinstance(exc, ModelDomainError)
    return dict(
        schema_version=1, elapsed_seconds=None, jacobian_rebuilds=0,
        jacobian_attempts=0, approximate_updates=0, model_evaluations=1,
        evaluation_counts={"initialization": 1}, step_reductions=[],
        step_reduction_counts={}, events=[], jacobian=None,
        equations=[], variables=[], acceptance_norm=None, acceptance_mode="unavailable",
        per_variable_step_limits_supported=backend != "trf",
        step_reduction_observability="backend_internal_unavailable" if backend == "trf" else "recorded",
        domain_status="violation_observed" if domain_error else "not_independently_checked",
        failures=[dict(stage="initialization", variable=getattr(exc, "variable", None),
                       probe_variable=None, error=f"{type(exc).__name__}: {exc}",
                       kind="model_domain_violation" if domain_error else "model_evaluation_failed",
                       component=str(getattr(exc, "component", "")),
                       equation=str(getattr(exc, "equation", "")), iteration=0,
                       x=[float(v) if np.isfinite(v) else None for v in values])],
        total_failure_count=1, total_event_count=0,
        truncated_records={"events": 0, "failures": 0, "step_reductions": 0})


def _vector(value, size, name, *, positive=False, finite=True):
    values = np.asarray(value, dtype=float)
    if values.ndim == 0:
        values = np.full(size, float(values))
    if values.shape != (size,):
        raise ValueError(f"{name} must contain {size} values")
    if np.any(np.isnan(values)) or (finite and not np.all(np.isfinite(values))):
        raise ValueError(f"{name} must contain finite values")
    if positive and np.any(values <= 0):
        raise ValueError(f"{name} must be strictly positive")
    return values.copy()


def solve_nonlinear(fun, x0, lower, upper, *, residual_scales,
                    variable_scales, diff_steps=None, tol=1e-4, max_iter=50,
                    backend="newton", names=None, progress_callback=None,
                    max_nfev=None, variable_names=None, variable_units=None,
                    equation_units=None, equation_components=None, equation_labels=None,
                    max_steps=None, residual_tolerances=None,
                    broyden_refresh_interval=8, diff_step_shrink_attempts=0):
    """Solve bounded equations, returning the last accepted physical state.

    ``fun(x)`` returns ``(raw_residuals, payload)``.  Scales remain fixed for
    this invocation. ``diff_steps`` are absolute steps in physical variable
    units; ``None`` chooses sqrt(machine epsilon) times the larger of the
    current absolute variable and its scale.  Inward/shorter probes are used
    at bounds. ``progress_callback(point)`` receives accepted trace points.

    Invalid input raises ValueError.  Ordinary model failures return a failed
    result with the last accepted state; cooperative cancellation propagates.
    Initial guesses outside the bounds are projected before first evaluation.
    ``max_nfev`` budgets all actual model calls, including finite-difference
    probes, rejected trials, and failed evaluations, identically for both
    backends. Its default is ``max(1, 2 * (n_variables + 1) * (max_iter + 1))``.
    Accepted-iteration and model-call limits are independent stopping criteria.

    ``residual_tolerances`` optionally supplies absolute physical tolerances,
    independent of numerical scaling. The compatibility default is scale*tol.
    ``max_steps`` limits each accepted change in physical variable units in
    Newton/Broyden; SciPy TRF does not expose that control and rejects it.
    Broyden updates the scaled Jacobian only after accepted steps and rebuilds
    on poor progress, invalid updates, refresh interval, or failed searches.
    Diagnostics describe local numerical rank, not global equation independence
    or validation of an engine/component model. Model exceptions may expose
    ``component`` and ``equation`` attributes for more precise failure location.
    ``diff_step_shrink_attempts`` enables extra halved probes after both original
    directions fail; zero preserves the original probe budget and behavior.
    """
    started = perf_counter()
    checkpoint()
    x = np.asarray(x0, dtype=float)
    if x.ndim != 1 or not len(x) or not np.all(np.isfinite(x)):
        raise ValueError("x0 must be a nonempty finite vector")
    n = len(x)
    lo = _vector(lower, n, "lower", finite=False)
    hi = _vector(upper, n, "upper", finite=False)
    if np.any(lo >= hi) or np.any(np.isposinf(lo)) or np.any(np.isneginf(hi)):
        raise ValueError("each lower bound must be smaller than its upper bound")
    vs = _vector(variable_scales, n, "variable_scales", positive=True)
    rs = np.asarray(residual_scales, dtype=float)
    if rs.ndim != 1 or not len(rs) or not np.all(np.isfinite(rs)) or np.any(rs <= 0):
        raise ValueError("residual_scales must be a positive finite vector")
    rs = rs.copy()
    m = len(rs)
    ds = None if diff_steps is None else _vector(diff_steps, n, "diff_steps", positive=True)
    limits = None if max_steps is None else _vector(max_steps, n, "max_steps", positive=True)
    if isinstance(tol, bool) or not np.isfinite(tol) or tol <= 0:
        raise ValueError("tol must be positive and finite")
    if isinstance(max_iter, bool) or int(max_iter) != max_iter or max_iter < 0:
        raise ValueError("max_iter must be a nonnegative integer")
    max_iter = int(max_iter)
    if max_nfev is None:
        evaluation_budget = max(1, 2 * (n + 1) * (max_iter + 1))
    else:
        if (isinstance(max_nfev, bool) or not np.isfinite(max_nfev)
                or int(max_nfev) != max_nfev or max_nfev < 1):
            raise ValueError("max_nfev must be a positive integer")
        evaluation_budget = int(max_nfev)
    if backend not in ("newton", "trf", "broyden"):
        raise ValueError("backend must be 'newton', 'broyden', or 'trf'")
    if backend == "trf" and limits is not None:
        raise ValueError("TRF does not support per-variable max_steps; use Newton/Broyden")
    for value, label, minimum in ((broyden_refresh_interval, "broyden_refresh_interval", 1),
                                   (diff_step_shrink_attempts, "diff_step_shrink_attempts", 0)):
        if isinstance(value, bool) or not np.isfinite(value) or int(value) != value or value < minimum:
            raise ValueError(f"{label} must be an integer >= {minimum}")
    absolute_tolerances = (rs * tol if residual_tolerances is None else
                           _vector(residual_tolerances, m, "residual_tolerances", positive=True))
    if not np.all(np.isfinite(absolute_tolerances)) or np.any(absolute_tolerances <= 0):
        raise ValueError("absolute residual tolerances must be finite and positive")
    labels = list(names) if names is not None else [f"r{j}" for j in range(m)]
    if len(labels) != m or len(set(labels)) != m or any(not isinstance(k, str) for k in labels):
        raise ValueError("names must be unique strings, one per residual")
    def metadata(value, size, default, label, unique=False):
        result = list(value) if value is not None else list(default)
        if len(result) != size or any(not isinstance(item, str) for item in result):
            raise ValueError(f"{label} must contain {size} strings")
        if unique and len(set(result)) != size:
            raise ValueError(f"{label} must be unique")
        return result
    vnames = metadata(variable_names, n, [f"x{j}" for j in range(n)], "variable_names", True)
    vunits = metadata(variable_units, n, [""] * n, "variable_units")
    eunits = metadata(equation_units, m, [""] * m, "equation_units")
    components = metadata(equation_components, m, [""] * m, "equation_components")
    elabels = metadata(equation_labels, m, labels, "equation_labels")
    x = np.clip(x, lo, hi)
    points = []
    nfev = njev = rejected = 0
    accepted = None
    last_error = None
    rebuilds = updates = 0
    events, failures, reductions = [], [], []
    evaluation_counts, reduction_counts = {}, {}
    jacobian_info = None
    used_diff_steps = [None] * n
    total_failure_count = total_event_count = 0
    domain_violation_seen = False

    def finite_number(value):
        return float(value) if value is not None and np.isfinite(value) else None

    def event(kind, reason, **detail):
        nonlocal total_event_count
        total_event_count += 1
        # Bounds keep diagnostic payloads manageable on long failed solves.
        if len(events) < 1000:
            events.append(dict(iteration=max(0, len(points) - 1), kind=kind, reason=reason, **detail))

    def reduce_step(reason, **detail):
        reduction_counts[reason] = reduction_counts.get(reason, 0) + 1
        if len(reductions) < 1000:
            reductions.append(dict(iteration=max(0, len(points) - 1), reason=reason, **detail))

    def passes(value):
        return value is not None and bool(np.all(np.abs(value.raw) <= absolute_tolerances))

    def acceptance_norm(value):
        with np.errstate(over="ignore"):
            return finite_number(np.max(np.abs(value.raw) / absolute_tolerances))

    def evaluate(at, stage="trial", variable=None):
        nonlocal nfev, last_error, total_failure_count, domain_violation_seen
        checkpoint()
        if nfev >= evaluation_budget:
            raise _EvaluationBudgetReached()
        nfev += 1
        evaluation_counts[stage] = evaluation_counts.get(stage, 0) + 1
        try:
            raw, payload = fun(at.tolist())
            raw = np.asarray(raw, dtype=float)
            if raw.shape != (m,):
                raise ValueError(f"model returned {raw.shape}; expected {(m,)} residuals")
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                scaled = raw / rs
            if not np.all(np.isfinite(raw)) or not np.all(np.isfinite(scaled)):
                bad = [labels[j] for j in range(m) if not np.isfinite(raw[j]) or not np.isfinite(scaled[j])]
                exc = ValueError("nonfinite residual: " + ", ".join(bad))
                exc.equation = ", ".join(bad)
                raise exc
            # A finite residual too large for the merit calculation is unusable.
            with np.errstate(over="ignore", invalid="ignore"):
                merit = float(0.5 * np.dot(scaled, scaled))
            if not np.isfinite(merit):
                raise ValueError("nonfinite squared residual norm")
            return _Evaluation(at.copy(), raw.copy(), scaled, payload)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            total_failure_count += 1
            domain_violation_seen = domain_violation_seen or isinstance(exc, ModelDomainError)
            if len(failures) < 1000:
                failures.append(dict(stage=stage, variable=getattr(exc, "variable", None) or variable,
                                     probe_variable=variable, error=last_error,
                                     kind="model_domain_violation" if isinstance(exc, ModelDomainError) else "model_evaluation_failed",
                                     component=str(getattr(exc, "component", "")),
                                     equation=str(getattr(exc, "equation", "")),
                                     iteration=max(0, len(points) - 1), x=at.tolist()))
            raise _EvaluationFailure(last_error) from exc

    def record(value, **detail):
        nonlocal accepted
        checkpoint()
        accepted = value
        if points and points[-1]["x"] == value.x.tolist():
            return
        point = dict(iteration=len(points), residual_norm=value.norm,
                     residuals=dict(zip(labels, value.scaled.tolist())),
                     raw_residuals=dict(zip(labels, value.raw.tolist())),
                     acceptance_norm=acceptance_norm(value),
                     jacobian_rebuilds=rebuilds, approximate_updates=updates,
                     elapsed_seconds=perf_counter() - started,
                     x=value.x.tolist(), nfev=nfev, merit=value.merit, **detail)
        points.append(point)
        if progress_callback is not None:
            progress_callback(deepcopy(point))

    def finish(reason, *, backend_status=None):
        passed = passes(accepted)
        final_x = accepted.x if accepted is not None else x
        diagnostics = dict(
            schema_version=1, elapsed_seconds=perf_counter() - started,
            jacobian_rebuilds=rebuilds, jacobian_attempts=njev, approximate_updates=updates,
            model_evaluations=nfev, evaluation_counts=dict(evaluation_counts),
            step_reductions=reductions, step_reduction_counts=dict(reduction_counts),
            events=events, failures=failures, jacobian=jacobian_info,
            total_failure_count=total_failure_count, total_event_count=total_event_count,
            truncated_records=dict(events=total_event_count - len(events),
                                   failures=total_failure_count - len(failures),
                                   step_reductions=sum(reduction_counts.values()) - len(reductions)),
            acceptance_mode="explicit_physical_tolerances" if residual_tolerances is not None else "scale_times_tolerance",
            acceptance_norm=acceptance_norm(accepted) if accepted else None,
            per_variable_step_limits_supported=backend != "trf",
            step_reduction_observability="backend_internal_unavailable" if backend == "trf" else "recorded",
            domain_status="violation_observed" if domain_violation_seen else "not_independently_checked",
            equations=[dict(name=labels[j], label=elabels[j], component=components[j], unit=eunits[j],
                            raw_residual=finite_number(accepted.raw[j]) if accepted else None,
                            scaled_residual=finite_number(accepted.scaled[j]) if accepted else None,
                            scale=float(rs[j]), absolute_tolerance=float(absolute_tolerances[j]),
                            passed=bool(abs(accepted.raw[j]) <= absolute_tolerances[j]) if accepted else False)
                       for j in range(m)],
            variables=[dict(name=vnames[j], unit=vunits[j], value=float(final_x[j]), scale=float(vs[j]),
                            lower=finite_number(lo[j]), upper=finite_number(hi[j]),
                            diff_step=used_diff_steps[j], requested_diff_step=float(ds[j]) if ds is not None else None,
                            max_step=float(limits[j]) if limits is not None else None,
                            at_bound="lower" if final_x[j] <= lo[j] else "upper" if final_x[j] >= hi[j] else None)
                       for j in range(n)])
        return NonlinearResult(
            x=(accepted.x if accepted is not None else x).tolist(),
            residuals=accepted.raw.tolist() if accepted is not None else [],
            payload=accepted.payload if accepted is not None else None,
            passed=passed, reason="tolerance_met" if passed else reason,
            points=points, nfev=nfev, njev=njev, rejected_steps=rejected,
            residual_scales=rs.tolist(), variable_scales=vs.tolist(),
            backend=backend, error=last_error, backend_status=backend_status,
            evaluation_budget=evaluation_budget, diagnostics=diagnostics)

    def describe_jacobian(jac, base, source):
        nonlocal jacobian_info
        try:
            left, singular, right = np.linalg.svd(jac, full_matrices=True)
            threshold = max(jac.shape) * np.finfo(float).eps * (singular[0] if len(singular) else 0)
            rank = int(np.sum(singular > threshold))
            condition = float(singular[0] / singular[-1]) if len(singular) and singular[-1] > threshold else None
            # List coordinates participating in numerical nullspaces; these
            # are coupled modes, not uniquely redundant equations/variables.
            weak = [vnames[j] for j in range(n) if rank < n and np.max(np.abs(right[rank:, j])) > .25]
            dependent = [labels[j] for j in range(m) if rank < m and np.max(np.abs(left[j, rank:])) > .25]
            jacobian_info = dict(rank=rank, rows=m, columns=n, condition_number=finite_number(condition),
                                 singular_values=singular.tolist(), weak_variables=weak,
                                 dependent_equations=dependent, source=source, at_x=base.x.tolist(),
                                 local_only=True, rank_threshold=float(threshold))
            return rank == min(m, n) and condition is not None and condition < 1e12
        except np.linalg.LinAlgError:
            jacobian_info = dict(rank=None, rows=m, columns=n, condition_number=None,
                                 singular_values=[], weak_variables=[], dependent_equations=[],
                                 source=source, at_x=base.x.tolist(), local_only=True)
            return False

    def jacobian(base, reason="full_newton"):
        """Jacobian of normalized residuals with respect to scaled variables."""
        nonlocal njev, rebuilds
        checkpoint()
        njev += 1
        jac = np.empty((m, n), dtype=float)
        for j in range(n):
            checkpoint()
            step = ds[j] if ds is not None else np.sqrt(np.finfo(float).eps) * max(abs(base.x[j]), vs[j])
            forward = hi[j] - base.x[j]
            backward = base.x[j] - lo[j]
            # Prefer the specified forward probe; use a full inward probe at
            # the upper bound, then try the opposite side on a model failure.
            signs = (1, -1) if forward >= step or forward >= backward else (-1, 1)
            column = None
            probes = [(sign, step * .5 ** shrink) for shrink in range(int(diff_step_shrink_attempts) + 1) for sign in signs]
            for sign, probe_step in probes:
                room = forward if sign > 0 else backward
                if room <= 0:
                    continue
                probe = base.x.copy()
                probe[j] = np.clip(base.x[j] + sign * min(probe_step, room), lo[j], hi[j])
                if probe[j] == base.x[j]:
                    probe[j] = np.nextafter(base.x[j], np.inf if sign > 0 else -np.inf)
                if not lo[j] <= probe[j] <= hi[j] or not np.isfinite(probe[j]):
                    continue
                delta = probe[j] - base.x[j]
                if delta == 0:
                    continue
                try:
                    trial = evaluate(probe, "finite_difference", vnames[j])
                except _EvaluationFailure:
                    continue
                with np.errstate(over="ignore", invalid="ignore"):
                    candidate = (trial.scaled - base.scaled) * (vs[j] / delta)
                if np.all(np.isfinite(candidate)):
                    column = candidate
                    used_diff_steps[j] = float(delta)
                    event("finite_difference_probe", "bounded_one_sided", variable=vnames[j],
                          requested_step=float(step), actual_step=float(delta))
                    break
            if column is None:
                raise _JacobianFailure(f"no valid finite-difference probe for variable {j} ({vnames[j]})")
            jac[:, j] = column
        rebuilds += 1
        describe_jacobian(jac, base, "finite_difference")
        event("jacobian_rebuild", reason, rebuild=rebuilds, at_x=base.x.tolist())
        return jac

    try:
        initial = evaluate(x, "initial")
    except _EvaluationFailure:
        return finish("initial_evaluation_failed")
    record(initial, step_length=0.0, step_norm=0.0)
    if passes(initial):
        return finish("tolerance_met")
    if max_iter == 0:
        return finish("max_iterations")

    if backend == "trf":
        # SciPy is optional for the default Newton backend.  The workbench
        # pins a version supporting least_squares callback(intermediate_result).
        from scipy.optimize import least_squares

        current_base = initial
        stopped_reason = None
        trf_function_calls = 0

        def find_value(at):
            nonlocal current_base
            if np.array_equal(at, current_base.x):
                return current_base
            if np.array_equal(at, accepted.x):
                return accepted
            current_base = evaluate(np.asarray(at, dtype=float), "trust_region_trial")
            return current_base

        def scipy_fun(at):
            nonlocal current_base, trf_function_calls
            try:
                current_base = find_value(at)
            except _EvaluationBudgetReached:
                raise
            except _EvaluationFailure:
                trf_function_calls += 1
                raise
            trf_function_calls += 1
            return current_base.scaled.copy()

        def scipy_jac(at):
            return jacobian(find_value(at), "trust_region") / vs[np.newaxis, :]

        def scipy_callback(intermediate_result):
            nonlocal stopped_reason
            value = find_value(intermediate_result.x)
            previous = accepted.x.copy()
            record(value, step_norm=float(np.max(np.abs((value.x - previous) / vs))))
            if passes(value):
                stopped_reason = "tolerance_met"
                raise StopIteration()
            if len(points) - 1 >= max_iter:
                stopped_reason = "max_iterations"
                raise StopIteration()

        try:
            optimized = least_squares(
                scipy_fun, initial.x, jac=scipy_jac, bounds=(lo, hi),
                method="trf", loss="linear", x_scale=vs,
                ftol=1e-12, xtol=1e-12, gtol=1e-12,
                max_nfev=evaluation_budget, callback=scipy_callback)
            final = find_value(optimized.x)
            record(final, step_norm=float(np.max(np.abs((final.x - accepted.x) / vs))))
            # SciPy counts its own trial evaluations, but excludes our probes.
            # Accepted steps can be deduplicated by x, including its final call.
            rejected = max(0, int(optimized.nfev) - len(points))
            reason = stopped_reason or {
                0: "max_evaluations", 1: "stationary_nonzero_residual",
                2: "cost_stagnation", 3: "step_stagnation",
                4: "cost_and_step_stagnation", -2: "callback_stopped",
            }.get(int(optimized.status), "optimizer_failed")
            return finish(reason, backend_status=int(optimized.status))
        except _EvaluationBudgetReached:
            rejected = max(0, trf_function_calls - len(points))
            return finish("max_evaluations")
        except (_EvaluationFailure, _JacobianFailure) as exc:
            rejected = max(0, trf_function_calls - len(points))
            last_error = str(exc)
            return finish("evaluation_failed" if isinstance(exc, _EvaluationFailure) else "jacobian_failed")
        except (ValueError, np.linalg.LinAlgError, FloatingPointError) as exc:
            rejected = max(0, trf_function_calls - len(points))
            last_error = f"{type(exc).__name__}: {exc}"
            return finish("optimizer_failed")

    # Newton operates in y = x / variable_scale, so both the linear solve and
    # step convergence test are independent of the units chosen by the caller.
    stagnation_count = 0
    jac = None
    matrix_age = 0
    rebuild_reason = "initial"
    while len(points) - 1 < max_iter:
        checkpoint()
        base = accepted
        if backend == "newton" or jac is None:
            try:
                jac = jacobian(base, "full_newton" if backend == "newton" else rebuild_reason)
                matrix_age = 0
            except _EvaluationBudgetReached:
                return finish("max_evaluations")
            except _JacobianFailure as exc:
                last_error = str(exc)
                return finish("jacobian_failed")
        with np.errstate(over="ignore", invalid="ignore"):
            gradient = jac.T @ base.scaled
        if not np.all(np.isfinite(gradient)):
            last_error = "nonfinite scaled gradient"
            return finish("jacobian_failed")
        try:
            direction = np.linalg.lstsq(jac, -base.scaled, rcond=None)[0]
        except np.linalg.LinAlgError:
            direction = -gradient

        def feasible_direction(candidate):
            candidate = candidate.copy()
            candidate[(base.x <= lo) & (candidate < 0)] = 0
            candidate[(base.x >= hi) & (candidate > 0)] = 0
            return candidate

        direction = feasible_direction(direction)
        gradient_direction = feasible_direction(-gradient)
        # A unit-scaled gradient direction provides a useful recovery even
        # when the Jacobian is large; backtracking need not undo an enormous
        # raw gradient before it can take a meaningful feasible step.
        gradient_direction /= max(1.0, float(np.max(np.abs(gradient_direction))))
        if not np.all(np.isfinite(direction)) or float(gradient @ direction) >= 0:
            direction = gradient_direction
        if not np.all(np.isfinite(direction)) or float(gradient @ direction) >= 0:
            if backend == "broyden" and matrix_age > 0:
                jac = None
                rebuild_reason = "approximate_direction_failed"
                continue
            return finish("stationary_nonzero_residual")

        reduction_start = dict(reduction_counts)
        def current_reduction_reasons():
            return [reason for reason, count in reduction_counts.items() if count > reduction_start.get(reason, 0)]
        def line_search(candidate):
            nonlocal rejected
            slope = float(gradient @ candidate)
            if not np.isfinite(slope) or slope >= 0:
                return None
            physical_step = candidate * vs
            alpha = 1.0
            for j, step in enumerate(physical_step):
                previous_alpha = alpha
                if limits is not None and abs(step) > 0:
                    alpha = min(alpha, float(limits[j] / abs(step)))
                    if alpha < previous_alpha:
                        reduce_step("variable_step_limit", variable=vnames[j], factor=alpha)
                previous_alpha = alpha
                if step > 0:
                    alpha = min(alpha, float((hi[j] - base.x[j]) / step))
                elif step < 0:
                    alpha = min(alpha, float((lo[j] - base.x[j]) / step))
                if alpha < previous_alpha:
                    reduce_step("variable_bound", variable=vnames[j], factor=alpha)
            for _backtrack in range(24):
                checkpoint()
                at = np.clip(base.x + alpha * physical_step, lo, hi)
                if alpha <= 0 or np.array_equal(at, base.x):
                    break
                try:
                    trial = evaluate(at)
                except _EvaluationFailure:
                    rejected += 1
                    reduce_step("model_evaluation_failed", factor=alpha * .5)
                    alpha *= 0.5
                    continue
                if trial.merit <= base.merit + 1e-4 * alpha * slope:
                    return trial, alpha
                rejected += 1
                reduce_step("insufficient_merit_decrease", factor=alpha * .5)
                alpha *= 0.5
            return None

        try:
            found = line_search(direction)
            recovery = False
            # A stale approximation must be retried with a real Jacobian at
            # the same accepted point before gradient recovery or failure.
            if found is None and backend == "broyden" and matrix_age > 0:
                jac = None
                rebuild_reason = "approximate_line_search_failed"
                continue
            if found is None and not np.array_equal(direction, gradient_direction):
                found = line_search(gradient_direction)
                recovery = found is not None
        except _EvaluationBudgetReached:
            return finish("max_evaluations")
        if found is None:
            return finish("line_search_failed")
        trial, alpha = found
        step_norm = float(np.max(np.abs((trial.x - base.x) / vs)))
        matrix_source = "broyden" if backend == "broyden" and matrix_age > 0 else "finite_difference"
        if backend == "broyden" and not passes(trial):
            delta_x = (trial.x - base.x) / vs
            delta_residual = trial.scaled - base.scaled
            denominator = float(delta_x @ delta_x)
            # A tiny step cannot define a reliable secant. Full reconstruction
            # also handles poor reduction and changes in active bounds/domain.
            invalid_trials = "model_evaluation_failed" in current_reduction_reasons()
            if invalid_trials:
                rebuild_reason = "model_domain_or_evaluation_change"
                jac = None
            elif trial.merit >= .9 * base.merit:
                rebuild_reason = "poor_progress"
                jac = None
            elif matrix_age + 1 >= broyden_refresh_interval:
                rebuild_reason = "refresh_interval"
                jac = None
            elif denominator <= np.finfo(float).eps ** 2 or not np.isfinite(denominator):
                rebuild_reason = "secant_step_too_small"
                jac = None
            else:
                with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                    candidate_jac = jac + np.outer(delta_residual - jac @ delta_x, delta_x) / denominator
                if not np.all(np.isfinite(candidate_jac)):
                    rebuild_reason = "nonfinite_update"
                    jac = None
                else:
                    previous_info = jacobian_info
                    if not describe_jacobian(candidate_jac, trial, "broyden"):
                        # A discarded candidate is not the matrix used by the
                        # solver; preserve the last usable matrix evidence.
                        jacobian_info = previous_info
                        rebuild_reason = "ill_conditioned_update"
                        jac = None
                    else:
                        jac = candidate_jac
                        matrix_age += 1
                        updates += 1
                        event("jacobian_update", "accepted_secant", update=updates)
            if jac is None:
                event("jacobian_refresh_scheduled", rebuild_reason)
        record(trial, step_length=alpha, step_norm=step_norm,
               direction="projected_gradient" if recovery else backend,
               jacobian_source=matrix_source,
               reduction_reasons=current_reduction_reasons())
        if passes(trial):
            return finish("tolerance_met")
        size = 1 + float(np.max(np.abs(trial.x / vs)))
        if step_norm <= 1e-12 * size:
            if backend == "broyden" and matrix_source == "broyden":
                jac = None
                rebuild_reason = "approximate_step_stagnation"
                continue
            return finish("step_stagnation")
        roundoff_merit = 32 * np.finfo(float).eps * max(base.merit, np.finfo(float).tiny)
        if base.merit - trial.merit <= roundoff_merit and step_norm <= 1e-8 * size:
            stagnation_count += 1
        else:
            stagnation_count = 0
        if stagnation_count >= 3:
            if backend == "broyden" and matrix_source == "broyden":
                jac = None
                rebuild_reason = "approximate_residual_stagnation"
                stagnation_count = 0
                continue
            return finish("residual_stagnation")
    return finish("max_iterations")
