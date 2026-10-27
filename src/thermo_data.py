"""Explicit, scoped methane thermochemical data selection.

This selects species data for equilibrium/frozen-flow calculations. It does
not validate high-pressure EOS, transport or finite-rate reaction mechanisms.
Old inputs retain GRI30; changing data must be recorded in a new calculation.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
from copy import deepcopy
from pathlib import Path
import hashlib


METHANE_DATASETS = {
    "gri30": {"file": "gri30.yaml", "phase": "gri30", "label": "GRI30（历史热化学数据）"},
    "gri30_highT": {"file": "gri30_highT.yaml", "phase": "gri30", "label": "GRI30 高温热化学数据"},
}
_ACTIVE = ContextVar("methane_thermo_dataset", default="gri30")


@lru_cache(maxsize=2)
def _bundled_dataset_metadata(dataset):
    """Describe the exact bundled species data; do not infer its validity."""
    import cantera as ct

    definition = METHANE_DATASETS[dataset]
    raw = (Path(ct.__file__).resolve().parent / "data" / definition["file"]).read_bytes()
    # The Python read also avoids Cantera's Windows non-ASCII path lookup.
    gas = ct.Solution(yaml=raw.decode("utf-8"), name=definition["phase"])
    species = gas.species()
    return {
        "id": dataset, **definition,
        "cantera_version": ct.__version__,
        "source_label": "Cantera 官方随附数据",
        "source_url": f"https://github.com/Cantera/cantera/blob/v{ct.__version__}/data/{definition['file']}",
        "file_description": str(gas.input_header.get("description", "")),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "species_count": len(species), "gas_eos": gas.thermo_model,
        "common_temperature_range_K": [max(s.thermo.min_temp for s in species),
                                       min(s.thermo.max_temp for s in species)],
        "historical": dataset == "gri30",
    }


def methane_dataset_catalog():
    """Return fresh read-only UI descriptions, separate from editable inputs."""
    return [deepcopy(_bundled_dataset_metadata(key)) for key in METHANE_DATASETS]


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
