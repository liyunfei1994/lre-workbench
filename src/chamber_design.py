"""
chamber_design.py — 推力室部件级详细设计的数据模型与计算引擎（V19，阶段1+2）
================================================================================

本模块是「推力室设计工作区」的**无界面**内核（纯数据/计算，不 import Qt），
职责：

  1. 轴向分区模型（圆柱段/收敛段/喉部区/扩张段/延伸段）——分区是部件级设计的
     基本数据结构：每个区域有自己的几何、通道、载荷与判据。
  2. 冷却通道**沿程分布断点表**（ChannelRow）：N/宽/深/内衬厚 沿 x 的分布。
     N 分段常数（断点即分叉/合并站，对标 Raptor/RS-25 HARCC 变截面策略），
     w/h/t 分段线性。序列化为 JSON 存 EngineInputs.cooling_channel_table_json，
     随导出 JSON 一起持久化。
  3. 边界条件快照（BoundarySnapshot）：记录当前循环解和输入指纹。
     冷却沿程直接读取 cooling_channel.profile；修改通道、换热或损失参数后，
     必须重算完整循环，不能只回写压降或另行计算一套沿程结果。
  4. 可行性校核与记分卡（compute_scorecard）：区域 × 判据的裕度矩阵。

依赖方向：本模块提供通道分布与保存结果读取接口，不调用独立冷却后处理求解器。
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from collections.abc import Mapping
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# ════════════════════════════════════════════════════════════════════
# 1. 轴向分区
# ════════════════════════════════════════════════════════════════════

#: 分区定义（key, 中文名）。喉部区取 x_t ± 1·r_t（与 marching 曲率强化窗口同口径）。
ZONE_DEFS: List[Tuple[str, str]] = [
    ("cylinder",   "圆柱段"),
    ("convergent", "收敛段"),
    ("throat",     "喉部区"),
    ("divergent",  "扩张段"),
    ("extension",  "延伸段"),
]

ZONE_LABELS = dict(ZONE_DEFS)


def zone_of_x(x: float, *, L_cyl: float, x_throat: float, r_t: float,
              x_cover_end: float = float("inf")) -> str:
    """站位 x（喷注面=0）→ 分区 key。x_cover_end = 再生冷却覆盖终点。"""
    if x > x_cover_end:
        return "extension"
    if x < L_cyl:
        return "cylinder"
    if x < x_throat - r_t:
        return "convergent"
    if x <= x_throat + r_t:
        return "throat"
    return "divergent"


# ════════════════════════════════════════════════════════════════════
# 2. 冷却通道沿程分布断点表
# ════════════════════════════════════════════════════════════════════

#: 断点锚点：物理站位关键词（几何变化时自动跟随），或显式 x_mm。
ANCHORS = ("injector", "conv_start", "throat", "exit", "x_mm")
ANCHOR_LABELS = {
    "injector":   "喷注面",
    "conv_start": "收敛起点",
    "throat":     "喉部",
    "exit":       "喷管出口",
    "x_mm":       "x [mm]",
}


@dataclass
class ChannelRow:
    """通道分布断点行：该锚点站位处的通道截面设计。

    行与行之间：w/h/t 沿 x 线性插值；N 取上游断点值（分段常数——N 只在断点处
    跳变，物理上即分叉/合并歧管站）。首行之前 / 末行之后取端值。
    """
    anchor: str = "throat"      # ANCHORS 之一
    x_mm: float = 0.0           # 仅 anchor == "x_mm" 时使用（喷注面=0）
    n: int = 300                # 通道数 N
    w_mm: float = 1.5           # 通道周向宽 [mm]
    h_mm: float = 5.0           # 通道径向深 [mm]
    t_mm: float = 0.5           # 内衬热壁厚 [mm]

    def validate(self) -> None:
        # 范围与 core.EngineInputs 对应标量字段的硬校验一致（V19 审查修复：
        # 表格与标量对同一物理量必须同口径，否则表格能填出标量禁填的值）。
        if self.anchor not in ANCHORS:
            raise ValueError(f"anchor={self.anchor!r} 不在 {ANCHORS}")
        if (isinstance(self.n, bool) or not isinstance(self.n, (int, float))
                or not math.isfinite(self.n) or int(self.n) != self.n or not (10 <= self.n <= 2000)):
            raise ValueError(f"通道数 n={self.n} 超范围 [10, 2000]")
        if not math.isfinite(self.x_mm) or self.x_mm < 0:
            raise ValueError("通道断点 x 必须为有限非负坐标。")
        if not (0.3 <= self.w_mm <= 12.0):
            raise ValueError(f"通道宽 w={self.w_mm} mm 超范围 [0.3, 12]")
        if not (0.5 <= self.h_mm <= 20.0):
            raise ValueError(f"通道深 h={self.h_mm} mm 超范围 [0.5, 20]")
        if not (0.2 <= self.t_mm <= 3.0):
            raise ValueError(f"内衬厚 t={self.t_mm} mm 超范围 [0.2, 3]")


def parse_channel_table(json_str: str) -> Optional[List[ChannelRow]]:
    """JSON → ChannelRow 列表（含结构校验）。空串/空表返回 None（单段模式）。"""
    s = (json_str or "").strip()
    if not s:
        return None
    data = json.loads(s)
    raw_rows = data.get("rows") if isinstance(data, dict) else data
    if not raw_rows:
        return None
    rows: List[ChannelRow] = []
    for i, rr in enumerate(raw_rows):
        row = ChannelRow(
            anchor=str(rr.get("anchor", "x_mm")),
            x_mm=float(rr.get("x_mm", 0.0) or 0.0),
            n=rr.get("n", 300),
            w_mm=float(rr.get("w_mm", 1.5)),
            h_mm=float(rr.get("h_mm", 5.0)),
            t_mm=float(rr.get("t_mm", 0.5)),
        )
        try:
            row.validate()
        except ValueError as exc:
            raise ValueError(f"分布表第 {i + 1} 行：{exc}")
        rows.append(row)
    return rows


def serialize_channel_table(rows: Optional[List[ChannelRow]]) -> str:
    """ChannelRow 列表 → JSON（写回 EngineInputs.cooling_channel_table_json）。"""
    if not rows:
        return ""
    return json.dumps(
        {"version": 1, "rows": [asdict(r) for r in rows]},
        ensure_ascii=False)


def default_rows_from_inputs(inputs, cooling_outputs: Optional[Dict] = None,
                             *, cooling_profile: Optional[Dict] = None) -> List[ChannelRow]:
    """Snapshot the solved channel geometry as an explicit piecewise-linear table.

    Every original node is retained. This freezes a sampled representation at
    absolute x coordinates; it is not an analytic radius-proportional rule on
    a different contour/grid. A throat-only row would silently change that rule
    into constant width everywhere and is deliberately no longer generated.
    """
    if (not isinstance(cooling_profile, dict)
            or cooling_profile.get("model_id") != "regen_quasi1d_ph_v1"):
        raise ValueError("生成节点采样表需要完整的已解冷却剖面，请先运行当前设计点。")
    nodes = (cooling_profile.get("geometry") or {}).get("nodes")
    if not isinstance(nodes, list) or len(nodes) < 2:
        raise ValueError("冷却剖面没有完整几何节点，不能从喉部标量假造全域通道。")
    rows = []
    previous_x = -math.inf
    for node in sorted(nodes, key=lambda row: row["x_m"]):
        if not math.isfinite(node["x_m"]) or node["x_m"] <= previous_x:
            raise ValueError("冷却几何节点坐标必须有限且严格递增。")
        previous_x = node["x_m"]
        row = ChannelRow(anchor="x_mm", x_mm=1000 * node["x_m"],
                         n=node["n_channels"], w_mm=1000 * node["width_m"],
                         h_mm=1000 * node["height_m"], t_mm=1000 * node["wall_thickness_m"])
        row.validate()
        rows.append(row)
    return rows


def _resolve_anchor_x(row: ChannelRow, *, x_throat: float, L_cyl: float,
                      x_exit: float) -> float:
    if row.anchor == "injector":
        return 0.0
    if row.anchor == "conv_start":
        return L_cyl
    if row.anchor == "throat":
        return x_throat
    if row.anchor == "exit":
        return x_exit
    return row.x_mm / 1000.0


def resolve_channel_distribution(
    rows: List[ChannelRow],
    x_arr: List[float], r_arr: List[float],
    *, x_throat: float, L_cyl: float, x_exit: float,
) -> Dict[str, List[float]]:
    """断点表 → 与站位数组对齐的沿程分布。

    返回 dict：n[]（分段常数）、w_m[]、h_m[]、t_m[]（分段线性）、
    pitch_m[]、rib_m[]（由局部半径与 N 导出）。
    """
    if not rows:
        raise ValueError("分布表为空")
    for row in rows:
        row.validate()
    if len(x_arr) != len(r_arr):
        raise ValueError("通道插值坐标与半径数组长度不一致。")
    resolved = sorted(
        ((_resolve_anchor_x(r, x_throat=x_throat, L_cyl=L_cyl, x_exit=x_exit), r)
         for r in rows),
        key=lambda t: t[0])
    xs_bp = [t[0] for t in resolved]
    rws = [t[1] for t in resolved]
    if (any(not math.isfinite(x) or x < 0 or x > x_exit + 1e-12 for x in xs_bp)
            or any(b <= a for a, b in zip(xs_bp, xs_bp[1:]))):
        raise ValueError("通道断点必须位于当前轮廓内，且不能重复或重合。")

    n_out: List[float] = []
    w_out: List[float] = []
    h_out: List[float] = []
    t_out: List[float] = []
    pitch_out: List[float] = []
    rib_out: List[float] = []
    for x, r_loc in zip(x_arr, r_arr):
        # 定位区间
        if x <= xs_bp[0]:
            n_v, w_v, h_v, t_v = rws[0].n, rws[0].w_mm, rws[0].h_mm, rws[0].t_mm
        elif x >= xs_bp[-1]:
            n_v, w_v, h_v, t_v = rws[-1].n, rws[-1].w_mm, rws[-1].h_mm, rws[-1].t_mm
        else:
            j = 0
            while j < len(xs_bp) - 1 and not (xs_bp[j] <= x <= xs_bp[j + 1]):
                j += 1
            a, b = rws[j], rws[j + 1]
            span = max(xs_bp[j + 1] - xs_bp[j], 1e-12)
            f = (x - xs_bp[j]) / span
            n_v = a.n                       # N 分段常数：取上游断点值（分叉发生在断点处）
            w_v = a.w_mm + f * (b.w_mm - a.w_mm)
            h_v = a.h_mm + f * (b.h_mm - a.h_mm)
            t_v = a.t_mm + f * (b.t_mm - a.t_mm)
        w_m, h_m, t_m = w_v / 1000.0, h_v / 1000.0, t_v / 1000.0
        pitch = 2.0 * math.pi * max(r_loc, 1e-6) / max(int(n_v), 1)
        n_out.append(float(int(n_v)))
        w_out.append(w_m)
        h_out.append(h_m)
        t_out.append(t_m)
        pitch_out.append(pitch)
        rib_out.append(pitch - w_m)
    return {"n": n_out, "w_m": w_out, "h_m": h_out, "t_m": t_out,
            "pitch_m": pitch_out, "rib_m": rib_out}


def channel_feasibility(dist: Dict[str, List[float]],
                        x_arr: List[float], x_throat: float) -> List[Tuple[str, str]]:
    """通道分布可行性清单。返回 [(级别 'ok'/'warn'/'fail', 消息)]。"""
    out: List[Tuple[str, str]] = []
    if not dist or not dist.get("rib_m"):
        return [("fail", "分布数据为空")]
    ribs = dist["rib_m"]
    ws = dist["w_m"]
    hs = dist["h_m"]
    i_min = min(range(len(ribs)), key=lambda i: ribs[i])
    rib_min_mm = ribs[i_min] * 1000.0
    x_min_mm = x_arr[i_min] * 1000.0
    if rib_min_mm <= 0.0:
        out.append(("fail", f"通道宽 > 节距（x={x_min_mm:.0f} mm 处肋宽 {rib_min_mm:.2f} mm ≤ 0）"
                            "——周向排布不可能，需减少 N 或减小宽度"))
    else:
        out.append(("ok", f"最小肋宽 {rib_min_mm:.2f} mm @ x={x_min_mm:.0f} mm；周向几何未重叠"))
        out.append(("warn", "肋宽制造适用性尚未评估；需要具体工艺、材料状态及供应方可实现尺寸依据。"))
    # 深宽比
    aspects = [h / max(w, 1e-9) for h, w in zip(hs, ws)]
    a_max = max(aspects)
    out.append(("warn", f"最大深宽比 h/w = {a_max:.1f}；制造适用性尚未评估，未采用通用深宽比上限。"))
    # 喉部通道数（信息）
    i_thr = min(range(len(x_arr)), key=lambda i: abs(x_arr[i] - x_throat))
    out.append(("ok", f"喉部：N={int(dist['n'][i_thr])}，w={ws[i_thr]*1e3:.2f} mm，"
                      f"h={hs[i_thr]*1e3:.2f} mm，肋={ribs[i_thr]*1e3:.2f} mm，"
                      f"节距={dist['pitch_m'][i_thr]*1e3:.2f} mm"))
    return out


# ════════════════════════════════════════════════════════════════════
# 3. 边界条件快照（双层循环握手）
# ════════════════════════════════════════════════════════════════════

@dataclass
class BoundarySnapshot:
    """从收敛循环解冻结的推力室边界条件（部件级设计在其上迭代）。"""
    solved_at: str = ""
    converged: bool = False
    # 主燃室
    pc_Pa: float = 0.0
    T_c_K: float = 0.0
    gamma: float = 1.19
    molar_mass: float = 22.0
    mdot_total: float = 0.0
    mdot_fuel: float = 0.0
    mdot_ox: float = 0.0
    of_ratio: float = 0.0
    thrust_N: float = 0.0
    isp_s: float = 0.0
    # 冷却剂（CH4 全流量）
    coolant_in_T_K: float = 0.0
    coolant_in_p_Pa: float = 0.0
    coolant_out_T_K: float = 0.0     # 本次循环采用的冷却出口
    coolant_out_p_Pa: float = 0.0
    # 主喷注器入口（两股涡轮排气）
    inj_ox_T_K: float = 0.0
    inj_ox_p_Pa: float = 0.0
    inj_ox_mdot: float = 0.0
    inj_fuel_T_K: float = 0.0
    inj_fuel_p_Pa: float = 0.0
    inj_fuel_mdot: float = 0.0
    # 几何基准
    r_t_m: float = 0.0
    r_c_m: float = 0.0
    L_cyl_m: float = 0.0
    L_conv_m: float = 0.0
    expansion_ratio: float = 0.0
    # 膜气源
    film_T_K: float = 850.0
    # 输入指纹（staleness 校验：与当前界面输入对比）
    inputs_fingerprint: Dict[str, Any] = field(default_factory=dict)


def snapshot_from_results(results) -> Optional[BoundarySnapshot]:
    """从 CycleResults 提取边界快照；数据不足返回 None。"""
    if results is None:
        return None
    from convergence import numerical_convergence
    try:
        s = results.summary
        comps = results.components
        noz = comps["nozzle"].outputs
        ch = comps["main_chamber"].outputs
        At = float(noz.get("At_m2") or 0.0)
        if At <= 0:
            return None
        cooling = comps.get("cooling_channel")
        profile = getattr(cooling, "profile", None)
        geometry = {}
        if profile is not None:
            if (not isinstance(profile, dict)
                    or profile.get("model_id") != "regen_quasi1d_ph_v1"
                    or not isinstance(profile.get("geometry"), dict)
                    or not profile["geometry"].get("nodes")):
                return None
            geometry = profile["geometry"]
            At = math.pi * float(geometry["r_t_m"]) ** 2
        fuel_pump_out = results.states.get("fuel_after_pump")
        fuel_cooled = results.states.get("fuel_after_cooling")
        inj_ox = results.states.get("ox_turbine_exhaust")
        inj_fu = results.states.get("fuel_turbine_exhaust")
        from datetime import datetime
        snap = BoundarySnapshot(
            solved_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            converged=numerical_convergence(results)["status"] == "pass",
            pc_Pa=float(results.inputs.pc),
            T_c_K=float(s.get("chamber_temperature_K", 0.0) or 0.0),
            gamma=float(s.get("chamber_gamma", 1.19) or 1.19),
            molar_mass=float(s.get("chamber_molar_mass", 22.0) or 22.0),
            mdot_total=float(s.get("mdot_total_kg_s", 0.0) or 0.0),
            mdot_fuel=float(s.get("mdot_fuel_kg_s", 0.0) or 0.0),
            mdot_ox=float(s.get("mdot_ox_kg_s", 0.0) or 0.0),
            of_ratio=float(getattr(results.inputs, "of_ratio", 0.0)),
            thrust_N=float(s.get("thrust_N", 0.0) or 0.0),
            isp_s=float(s.get("Isp_s", 0.0) or 0.0),
            r_t_m=math.sqrt(At / math.pi),
            r_c_m=float(geometry.get("r_c_m", float(ch.get("cc_diameter_m", 0.0) or 0.0) / 2.0)),
            L_cyl_m=float(geometry.get("L_cyl_m", ch.get("cc_length_m", 0.0)) or 0.0),
            L_conv_m=float(geometry.get("L_conv_m",
                float(ch.get("cc_convergent_length_mm", 0.0) or 0.0) / 1000.0)),
            expansion_ratio=float(geometry.get("expansion_ratio", getattr(results.inputs, "expansion_ratio", 0.0))),
            film_T_K=float(s.get("fuel_turbine_exhaust_T_K", 850.0) or 850.0),
        )
        if fuel_pump_out is not None:
            snap.coolant_in_T_K = float(fuel_pump_out.temperature)
            snap.coolant_in_p_Pa = float(fuel_pump_out.pressure)
        if fuel_cooled is not None:
            snap.coolant_out_T_K = float(fuel_cooled.temperature)
            snap.coolant_out_p_Pa = float(fuel_cooled.pressure)
        if inj_ox is not None:
            snap.inj_ox_T_K = float(inj_ox.temperature)
            snap.inj_ox_p_Pa = float(inj_ox.pressure)
            snap.inj_ox_mdot = float(inj_ox.mass_flow)
        if inj_fu is not None:
            snap.inj_fuel_T_K = float(inj_fu.temperature)
            snap.inj_fuel_p_Pa = float(inj_fu.pressure)
            snap.inj_fuel_mdot = float(inj_fu.mass_flow)
        try:
            snap.inputs_fingerprint = asdict(results.inputs)
        except Exception:
            snap.inputs_fingerprint = {}
        return snap
    except Exception:
        return None


def snapshot_stale_fields(snap: BoundarySnapshot, current_inputs) -> List[str]:
    """快照过期检查：当前输入与快照指纹不一致的字段名（空 = 快照仍然有效）。

    冷却几何、粗糙度、换热及边界输入均属于当前循环记录。任何输入改变后，
    保存结果仍可只读查看，但不能宣称与修改后的设计状态一致。
    """
    fp = snap.inputs_fingerprint or {}
    if not fp:
        return ["__missing_input_fingerprint__"]
    stale: List[str] = []
    try:
        cur = dict(current_inputs) if isinstance(current_inputs, Mapping) else asdict(current_inputs)
    except Exception:
        return ["__invalid_current_inputs__"]
    for k in sorted(set(fp) | set(cur)):
        if k not in fp or k not in cur:
            stale.append(k)
            continue
        v_old, v_new = fp[k], cur[k]
        try:
            same = (abs(float(v_new) - float(v_old)) <=
                    1e-9 * max(abs(float(v_old)), 1.0))
        except (TypeError, ValueError):
            same = (v_new == v_old)
        if not same:
            stale.append(k)
    return stale


# ════════════════════════════════════════════════════════════════════
# 4. 当前循环冷却结果的只读展示接口
# ════════════════════════════════════════════════════════════════════

def saved_cooling_result(base_results) -> Dict[str, Any]:
    """Copy the profile actually used by the cycle; never rerun heat/hydraulics."""
    comp = getattr(base_results, "components", {}).get("cooling_channel")
    profile = getattr(comp, "profile", None) if comp is not None else None
    if (not isinstance(profile, dict) or profile.get("model_id") != "regen_quasi1d_ph_v1"
            or profile.get("version") != 1 or len(profile.get("nodes") or []) < 2
            or not profile.get("cells")):
        raise ValueError("当前记录没有统一冷却沿程解；请按当前输入重算完整循环，不能从预算或历史后处理生成替代曲线。")
    return {"component": deepcopy(comp), "profile": deepcopy(profile),
            "warnings": list(profile.get("validity", {}).get("warnings", [])),
            "summary_delta": {}, "source": "saved_cycle_cooling_profile"}


def marching_preview(base_results, inputs) -> Dict[str, Any]:
    """Compatibility entry point: read-only saved profile, never marching again."""
    saved_inputs = getattr(base_results, "inputs", None)
    try:
        snap = BoundarySnapshot(inputs_fingerprint=asdict(saved_inputs))
    except (TypeError, ValueError):
        raise ValueError("当前记录缺少输入指纹；请重算完整循环。")
    if snapshot_stale_fields(snap, inputs):
        raise ValueError("参数已修改：必须重算完整循环；不能只重算冷却沿程或回写压降。")
    return saved_cooling_result(base_results)


# ════════════════════════════════════════════════════════════════════
# 5. 校核记分卡（区域 × 判据）
# ════════════════════════════════════════════════════════════════════

def compute_scorecard(wf, results, inputs) -> List[Dict[str, Any]]:
    """区域 × 判据的裕度记分卡。

    wf：thermal_structural.WallField（domain="full"，保存的冷却剖面来源）。
    返回行列表：{zone, zone_label, criterion, value, limit, margin_text, status}。
    status ∈ ok / warn / fail / na。
    """
    rows: List[Dict[str, Any]] = []
    if wf is None or not getattr(wf, "x", None):
        return rows
    s = results.summary if results is not None else {}
    from core import wall_temperature_constraint
    constraint = wall_temperature_constraint(inputs)
    T_limit = constraint["limit_K"]
    valid_limit = (isinstance(T_limit, (int, float)) and math.isfinite(T_limit) and T_limit > 0
                   and (constraint["status"] != "documented" or bool(constraint["basis"])))
    x = wf.x
    r_t = min(wf.r) if wf.r else 0.0
    x_t = x[wf.throat_index] if wf.x else 0.0
    ch = results.components.get("main_chamber").outputs if results is not None and \
        "main_chamber" in results.components else {}
    L_cyl = float(ch.get("cc_length_m", 0.0) or 0.0)
    cover_eps = float(getattr(inputs, "cooling_cover_eps", 0.0) or 0.0)
    eps_full = float(getattr(inputs, "expansion_ratio", 0.0) or 0.0)

    # 站位 → 分区
    zones_idx: Dict[str, List[int]] = {k: [] for k, _ in ZONE_DEFS}
    for i, xi in enumerate(x):
        zones_idx[zone_of_x(xi, L_cyl=L_cyl, x_throat=x_t, r_t=r_t)].append(i)

    def _st(margin_ok: bool, margin_warn: bool) -> str:
        return "ok" if margin_ok else ("warn" if margin_warn else "fail")

    for zkey, zlabel in ZONE_DEFS:
        idx = zones_idx.get(zkey) or []
        if zkey == "extension":
            if cover_eps and eps_full and cover_eps < eps_full:
                rows.append(dict(
                    zone=zkey, zone_label=zlabel, criterion="热防护覆盖",
                    value=f"再生冷却止于 ε={cover_eps:.0f}",
                    limit=f"全喷管 ε={eps_full:.0f}",
                    margin_text="延伸段无热模型（辐射冷却待建）", status="warn"))
            continue
        if not idx:
            continue
        # ① 热面壁温
        i_T = max(idx, key=lambda i: wf.T_wg[i])
        T_max = wf.T_wg[i_T]
        rows.append(dict(
            zone=zkey, zone_label=zlabel, criterion="热面壁温 T_wg",
            value=f"{T_max:.0f} K @ x={x[i_T]*1e3:.0f} mm",
            limit=f"≤ {T_limit:.0f} K" if valid_limit else "未设定有效方案上限",
            margin_text=((f"{T_limit - T_max:+.0f} K" + (" · 暂定限值待依据" if constraint["status"] == "provisional" else ""))
                         if valid_limit else "该项尚未评估"),
            status=("na" if not valid_limit else "fail" if T_max > T_limit else
                    "warn" if constraint["status"] == "provisional" else "ok")))
        # ② Report cold-wall temperature without inventing a coking threshold.
        i_Tc = max(idx, key=lambda i: wf.T_wc[i])
        Twc = wf.T_wc[i_Tc]
        rows.append(dict(
            zone=zkey, zone_label=zlabel, criterion="冷面壁温 T_wc（甲烷结焦）",
            value=f"{Twc:.0f} K",
            limit="未建立适用本燃料、压力及停留时间的结焦判据",
            margin_text="结焦尚未评估；当前温度不代表积碳预测", status="na"))
        # ③ 冷却剂流速
        if wf.v_coolant:
            i_V = max(idx, key=lambda i: wf.v_coolant[i])
            V = wf.v_coolant[i_V]
            rows.append(dict(
                zone=zkey, zone_label=zlabel, criterion="冷却剂流速 V",
                value=f"{V:.0f} m/s",
                limit="未设定本方案的流速约束",
                margin_text="需结合压降、换热及具体制造条件判断", status="na"))
        # ④ 内壁结构 SF
        if getattr(wf, "structural_status", "legacy_screening") == "not_assessed":
            rows.append(dict(
                zone=zkey, zone_label=zlabel, criterion="内壁安全系数 SF",
                value="未评估", limit="需对应的结构材料与制造状态数据",
                margin_text=getattr(wf, "structural_reason", "材料结构数据未对应"), status="na"))
        elif wf.SF:
            i_sf = min(idx, key=lambda i: wf.SF[i])
            sf = wf.SF[i_sf]
            rows.append(dict(
                zone=zkey, zone_label=zlabel, criterion="内壁安全系数 SF",
                value=f"{sf:.2f} @ x={x[i_sf]*1e3:.0f} mm",
                limit="≥ 1.0（弹性）",
                margin_text=f"{sf - 1.0:+.2f}",
                status=_st(sf >= 1.0, sf >= 0.8)))
        # ⑤ 肋宽可行性（有沿程通道数据时）
        if wf.w_chan and wf.n_chan and any(v > 0 for v in wf.n_chan):
            ribs = []
            for i in idx:
                if wf.n_chan[i] > 0:
                    pitch = 2.0 * math.pi * wf.r[i] / wf.n_chan[i]
                    ribs.append((pitch - wf.w_chan[i], i))
            if ribs:
                rib_min, i_rib = min(ribs)
                rows.append(dict(
                    zone=zkey, zone_label=zlabel, criterion="最小肋宽",
                    value=f"{rib_min*1e3:.2f} mm @ x={x[i_rib]*1e3:.0f} mm",
                    limit="几何非重叠要求 > 0；制造最小肋宽未设定",
                    margin_text="周向几何重叠" if rib_min <= 0 else "几何未重叠；制造适用性尚未评估",
                    status="fail" if rib_min <= 0 else "na"))

    # 全程判据（不分区）
    cool = results.components.get("cooling_channel") if results is not None else None
    profile = getattr(cool, "profile", {}) or {}
    recorded = profile.get("summary", {})
    nodes = profile.get("nodes") or []
    dp = recorded.get("dp_total_Pa")
    p_in = nodes[0].get("p_Pa") if nodes else None
    if isinstance(dp, (int, float)) and math.isfinite(dp) and isinstance(p_in, (int, float)) and math.isfinite(p_in):
        rows.append(dict(
            zone="global", zone_label="全程", criterion="冷却回路压降 Δp",
            value=f"{dp/1e6:.1f} MPa（入口 {p_in/1e6:.0f} MPa）",
            limit="保存的入口端口压力 − 出口端口压力",
            margin_text="不是 max(p)−min(p)；不以任意入口压比例判可行", status="na"))
    else:
        rows.append(dict(zone="global", zone_label="全程", criterion="冷却回路压降 Δp",
                         value="未保存统一冷却端口压降", limit="需重算完整循环", margin_text="—", status="na"))
    return rows
