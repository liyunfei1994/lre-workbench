"""Explicit, scoped methane thermochemical data selection.

This selects species data for equilibrium/frozen-flow calculations. It does
not validate high-pressure EOS, transport or finite-rate reaction mechanisms.
Old inputs retain GRI30; changing data must be recorded in a new calculation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps


METHANE_DATASETS = {
    "gri30": {"file": "gri30.yaml", "phase": "gri30", "label": "GRI30（历史热化学数据）"},
    "gri30_highT": {"file": "gri30_highT.yaml", "phase": "gri30", "label": "GRI30 高温热化学数据"},
}
_ACTIVE = ContextVar("methane_thermo_dataset", default="gri30")


def active_methane_dataset():
    return _ACTIVE.get()


def saved_input_identity(values):
    """Resolve an explicitly identified saved snapshot without modifying it.

    Snapshots written before the dataset field existed used GRI30. Callers must
    identify a persisted record or a recognized saved-file format; a generic
    partial parameter update is not evidence that an input is historical.
    """
    resolved = dict(values)
    resolved.setdefault("methane_thermo_dataset", "gri30")
    return resolved


@contextmanager
def methane_dataset_scope(dataset):
    if dataset not in METHANE_DATASETS:
        raise ValueError(f"不支持的甲烷热化学数据集：{dataset!r}")
    token = _ACTIVE.set(dataset)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def model_thermo_scope(function):
    """Keep all nested energy evaluations on the model's declared data."""
    @wraps(function)
    def wrapped(model, *args, **kwargs):
        with methane_dataset_scope(getattr(model.inputs, "methane_thermo_dataset", "gri30")):
            return function(model, *args, **kwargs)
    return wrapped
