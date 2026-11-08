"""Evidence for failures outside the common nonlinear solver's lifetime."""
from time import perf_counter

from lre_workbench.solvers.nonlinear import initial_evaluation_diagnostics
from lre_workbench.solvers.initialization import SolverInitializationError


class SolverAttempt:
    """Count only actual residual calls, including the cached initial call."""

    def __init__(self, backend):
        self.backend = backend
        self.calls = 0
        self.stage = "input_validation"
        self.x = []
        self.started = perf_counter()
        self.model_error = None

    def evaluate(self, function, values):
        self.x = list(values)
        self.calls += 1
        self.stage = "initialization" if self.calls == 1 else "model_evaluation"
        try:
            result = function(*values)
        except Exception as exc:
            # Store the exact exception and actual projected model input. An
            # unrelated later processing exception must not inherit its label.
            self.model_error = exc
            raise
        self.stage = "solver_processing"
        return result

    def failure(self, exc):
        diagnostics = initial_evaluation_diagnostics(exc, self.x, backend=self.backend)
        diagnostics.update(model_evaluations=self.calls, elapsed_seconds=perf_counter() - self.started,
                           evaluation_counts={"recorded_model_calls": self.calls},
                           evaluation_count_known=True)
        diagnostics["failures"][0]["stage"] = self.stage
        if self.calls > 1:
            # Residual-call instrumentation cannot reconstruct the generic
            # solver's internal Jacobian or rejected-step counters.
            diagnostics.update(jacobian_rebuilds=None, jacobian_attempts=None,
                               approximate_updates=None)
        if exc is not self.model_error and self.calls:
            diagnostics["failures"][0]["x"] = []
        return diagnostics


def uninstrumented_failure(exc, backend):
    """An overridden entry point has no trustworthy model-call evidence."""
    diagnostics = initial_evaluation_diagnostics(exc, [], backend=backend)
    diagnostics.update(model_evaluations=None, evaluation_counts={}, evaluation_count_known=False,
                       jacobian_rebuilds=None, jacobian_attempts=None, approximate_updates=None)
    diagnostics["failures"][0]["stage"] = "outside_instrumented_solver"
    return diagnostics


def recorded_total(runs, key, extra=0):
    """Do not turn an explicitly unknown work counter into zero."""
    values = [run.get(key, 0) for run in runs]
    return float(sum(values) + extra) if all(value is not None for value in values) else None
