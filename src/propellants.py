"""Explicit propellant/model identities shared by solver and user interfaces.

Kerosene is represented by pure n-dodecane, not by methane and not by a
validated multi-component RP-1 model. Property limits are those published by
CoolProp for n-dodecane (Lemmon & Huber, Energy & Fuels 18, 2004).
"""
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class Propellant:
    id: str
    label: str
    fuel_label: str
    fuel_species: str
    coolprop_fluid: str
    mechanism: str
    phase: str
    stoich_of: float
    supported_cycles: tuple[str, ...]
    inlet_temperature_min_K: float
    inlet_temperature_max_K: float
    fluid_pressure_max_Pa: float
    limitations: tuple[str, ...] = ()


_MODELS = {
    "lox_methane": Propellant(
        "lox_methane", "液氧 / 液态甲烷", "甲烷", "CH4", "Methane", "gri30.yaml", "gri30",
        4.0, ("ffsc", "gg"), 90.6941, 625.0, 1.0e9,
        ("气相采用所选甲烷热化学数据与理想气体平衡；纯推进剂采用 CoolProp 状态方程。高压燃气适用性仍待验证。",)),
    "lox_kerosene": Propellant(
        "lox_kerosene", "液氧 / 煤油（正十二烷替代模型）", "煤油（正十二烷替代）", "c12h26",
        "n-Dodecane", "nDodecane_Reitz.yaml", "nDodecane_IG",
        18.5 * 31.998 / 170.34, ("gg",), 263.6, 700.0, 200.0e6,
        ("煤油按单组分正十二烷计算，未标定为真实 RP-1 配方或 Merlin 实机。",
         "当前支持 GG 稳态系统计算；再生冷却采用给定吸热量与压降。",
         "液体物性适用范围为 263.6–700 K、压力不超过 200 MPa；超界报错。",
         "燃烧产物采用 nDodecane_IG 理想气相平衡；部分物种热力学多项式上限为 3000 K，超过时明确报告外推。",
         "不包含凝聚碳、积碳、热裂解、有限速率燃烧、喷注雾化和燃烧稳定性。")),
}


def get_propellant(pair: str = "lox_methane") -> Propellant:
    try:
        return _MODELS[pair]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"不支持的推进剂组合：{pair!r}") from exc


def propellant_models() -> list[dict]:
    return [asdict(model) for model in _MODELS.values()]


def normalize_fuel_species(species: str) -> str:
    if species in ("CH4", "Methane", "methane"):
        return "CH4"
    if species in ("c12h26", "C12H26", "RP1", "RP-1", "n-Dodecane", "nDodecane"):
        return "c12h26"
    raise ValueError(f"不支持的燃料物种：{species!r}")


def for_fuel(species: str = "CH4") -> Propellant:
    return get_propellant("lox_kerosene" if normalize_fuel_species(species) == "c12h26" else "lox_methane")


def validate_surrogate_state(temperature: float, pressure: float) -> None:
    """Enforce the published fluid envelope instead of using methane fallback."""
    model = _MODELS["lox_kerosene"]
    if not math.isfinite(temperature) or not model.inlet_temperature_min_K <= temperature <= model.inlet_temperature_max_K:
        raise ValueError(f"正十二烷物性温度 {temperature:g} K 超出 263.6–700 K 范围。")
    if not math.isfinite(pressure) or not 0 < pressure <= model.fluid_pressure_max_Pa:
        raise ValueError(f"正十二烷物性压力 {pressure:g} Pa 超出 (0, 200 MPa] 范围。")


def require_liquid_fuel_state(fuel_species: str, temperature: float, pressure: float,
                              context: str = "燃料供给") -> None:
    """Reject gas feeds before any surrogate liquid-pump/booster approximation.

The methane path retains its existing validation and numerical behavior.
"""
    if normalize_fuel_species(fuel_species) == "CH4":
        return
    validate_surrogate_state(temperature, pressure)
    try:
        from CoolProp.CoolProp import PhaseSI
    except ImportError as exc:
        raise RuntimeError("正十二烷液体供给系统需要 CoolProp 相态判断。") from exc
    phase = PhaseSI("T", temperature, "P", pressure, "n-Dodecane")
    if phase not in ("liquid", "supercritical_liquid"):
        raise ValueError(f"{context}必须为液态或压缩液体；正十二烷在 {temperature:g} K / {pressure:g} Pa 的相态为 {phase}。")
