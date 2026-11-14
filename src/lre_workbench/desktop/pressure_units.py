"""Migrate raw desktop recovery text when its pressure display unit changes.

Case files and solver inputs use Pa and must not pass through this conversion.
Only three former bar input controls changed units; incomplete text is retained.
"""
from decimal import Decimal, InvalidOperation


LEGACY_BAR_INPUTS = (
    "tank_pressure_pa", "feed_line_dp_pa", "gg_turbine_backpressure_pa",
)


def recovery_pressure_text(raw_inputs, source_unit):
    if source_unit not in ("bar", "MPa"):
        raise ValueError("草稿压力显示单位不受支持")
    converted = dict(raw_inputs)
    if source_unit == "MPa":
        return converted
    for key in LEGACY_BAR_INPUTS:
        text = converted.get(key)
        if not isinstance(text, str) or not text.strip():
            continue
        try:
            value = Decimal(text)
        except InvalidOperation:
            continue
        if value.is_finite():
            converted[key] = str(value / Decimal(10))
    return converted
