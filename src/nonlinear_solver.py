"""Bounded, scaled nonlinear equation solvers with auditable accepted iterates.

Both backends solve exactly the same normalized equations.  Optimizer success
is deliberately separate from equation acceptance: every component must meet
``abs(residual / residual_scale) <= tol``.  Function probes and rejected trial
steps count as work, but never appear as accepted iterations in the trace.
"""
from dataclasses import dataclass
from typing import Any

import numpy as np

from task_control import checkpoint


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
                    max_nfev=None):
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
    """
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
    if backend not in ("newton", "trf"):
        raise ValueError("backend must be 'newton' or 'trf'")
    labels = list(names) if names is not None else [f"r{j}" for j in range(m)]
    if len(labels) != m or len(set(labels)) != m or any(not isinstance(k, str) for k in labels):
        raise ValueError("names must be unique strings, one per residual")
    x = np.clip(x, lo, hi)
    points = []
    nfev = njev = rejected = 0
    accepted = None
    last_error = None

    def evaluate(at):
        nonlocal nfev, last_error
        checkpoint()
        if nfev >= evaluation_budget:
            raise _EvaluationBudgetReached()
        nfev += 1
        try:
            raw, payload = fun(at.tolist())
            raw = np.asarray(raw, dtype=float)
            if raw.shape != (m,):
                raise ValueError(f"model returned {raw.shape}; expected {(m,)} residuals")
            with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
                scaled = raw / rs
            if not np.all(np.isfinite(raw)) or not np.all(np.isfinite(scaled)):
                raise ValueError("nonfinite residual")
            # A finite residual too large for the merit calculation is unusable.
            with np.errstate(over="ignore", invalid="ignore"):
                merit = float(0.5 * np.dot(scaled, scaled))
            if not np.isfinite(merit):
                raise ValueError("nonfinite squared residual norm")
            return _Evaluation(at.copy(), raw.copy(), scaled, payload)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            raise _EvaluationFailure(last_error) from exc

    def record(value, **detail):
        nonlocal accepted
        checkpoint()
        accepted = value
        if points and points[-1]["x"] == value.x.tolist():
            return
        point = dict(iteration=len(points), residual_norm=value.norm,
                     residuals=dict(zip(labels, value.scaled.tolist())),
                     x=value.x.tolist(), nfev=nfev, merit=value.merit, **detail)
        points.append(point)
        if progress_callback is not None:
            progress_callback(dict(point, residuals=dict(point["residuals"]), x=list(point["x"])))

    def finish(reason, *, backend_status=None):
        passed = accepted is not None and accepted.norm <= tol
        return NonlinearResult(
            x=(accepted.x if accepted is not None else x).tolist(),
            residuals=accepted.raw.tolist() if accepted is not None else [],
            payload=accepted.payload if accepted is not None else None,
            passed=passed, reason="tolerance_met" if passed else reason,
            points=points, nfev=nfev, njev=njev, rejected_steps=rejected,
            residual_scales=rs.tolist(), variable_scales=vs.tolist(),
            backend=backend, error=last_error, backend_status=backend_status,
            evaluation_budget=evaluation_budget)

    def jacobian(base):
        """Jacobian of normalized residuals with respect to scaled variables."""
        nonlocal njev
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
            for sign in signs:
                room = forward if sign > 0 else backward
                if room <= 0:
                    continue
                probe = base.x.copy()
                probe[j] = np.clip(base.x[j] + sign * min(step, room), lo[j], hi[j])
                if probe[j] == base.x[j]:
                    probe[j] = np.nextafter(base.x[j], np.inf if sign > 0 else -np.inf)
                if not lo[j] <= probe[j] <= hi[j] or not np.isfinite(probe[j]):
                    continue
                delta = probe[j] - base.x[j]
                if delta == 0:
                    continue
                try:
                    trial = evaluate(probe)
                except _EvaluationFailure:
                    continue
                with np.errstate(over="ignore", invalid="ignore"):
                    candidate = (trial.scaled - base.scaled) * (vs[j] / delta)
                if np.all(np.isfinite(candidate)):
                    column = candidate
                    break
            if column is None:
                raise _JacobianFailure(f"no valid finite-difference probe for variable {j}")
            jac[:, j] = column
        return jac

    try:
        initial = evaluate(x)
    except _EvaluationFailure:
        return finish("initial_evaluation_failed")
    record(initial, step_length=0.0, step_norm=0.0)
    if initial.norm <= tol:
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
            current_base = evaluate(np.asarray(at, dtype=float))
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
            return jacobian(find_value(at)) / vs[np.newaxis, :]

        def scipy_callback(intermediate_result):
            nonlocal stopped_reason
            value = find_value(intermediate_result.x)
            previous = accepted.x.copy()
            record(value, step_norm=float(np.max(np.abs((value.x - previous) / vs))))
            if value.norm <= tol:
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
    for _ in range(max_iter):
        checkpoint()
        base = accepted
        try:
            jac = jacobian(base)
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
            return finish("stationary_nonzero_residual")

        def line_search(candidate):
            nonlocal rejected
            slope = float(gradient @ candidate)
            if not np.isfinite(slope) or slope >= 0:
                return None
            physical_step = candidate * vs
            alpha = 1.0
            for j, step in enumerate(physical_step):
                if step > 0:
                    alpha = min(alpha, float((hi[j] - base.x[j]) / step))
                elif step < 0:
                    alpha = min(alpha, float((lo[j] - base.x[j]) / step))
            for _backtrack in range(24):
                checkpoint()
                at = np.clip(base.x + alpha * physical_step, lo, hi)
                if alpha <= 0 or np.array_equal(at, base.x):
                    break
                try:
                    trial = evaluate(at)
                except _EvaluationFailure:
                    rejected += 1
                    alpha *= 0.5
                    continue
                if trial.merit <= base.merit + 1e-4 * alpha * slope:
                    return trial, alpha
                rejected += 1
                alpha *= 0.5
            return None

        try:
            found = line_search(direction)
            recovery = False
            if found is None and not np.array_equal(direction, gradient_direction):
                found = line_search(gradient_direction)
                recovery = found is not None
        except _EvaluationBudgetReached:
            return finish("max_evaluations")
        if found is None:
            return finish("line_search_failed")
        trial, alpha = found
        step_norm = float(np.max(np.abs((trial.x - base.x) / vs)))
        record(trial, step_length=alpha, step_norm=step_norm,
               direction="projected_gradient" if recovery else "newton")
        if trial.norm <= tol:
            return finish("tolerance_met")
        size = 1 + float(np.max(np.abs(trial.x / vs)))
        if step_norm <= 1e-12 * size:
            return finish("step_stagnation")
        roundoff_merit = 32 * np.finfo(float).eps * max(base.merit, np.finfo(float).tiny)
        if base.merit - trial.merit <= roundoff_merit and step_norm <= 1e-8 * size:
            stagnation_count += 1
        else:
            stagnation_count = 0
        if stagnation_count >= 3:
            return finish("residual_stagnation")
    return finish("max_iterations")
