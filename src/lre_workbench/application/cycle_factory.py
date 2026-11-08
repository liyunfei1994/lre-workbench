"""Desktop/research cycle selection without loading historical cycles for FFSC."""


def make_cycle_model(inputs):
    """Preserve existing research selection; web scope is enforced by its adapter."""
    if str(getattr(inputs, "cycle_type", "ffsc")) == "gg":
        from lre_workbench.legacy.gg_cycle import GGCycleModel
        return GGCycleModel(inputs)
    from lre_workbench.systems.ffsc import FFSCCycleModel
    return FFSCCycleModel(inputs)
