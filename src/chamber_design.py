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
  3. 边界条件快照（BoundarySnapshot）：从收敛循环解冻结推力室边界——这是
     「双层循环显式握手」的下半环：系统级解出边界 → 部件级在冻结边界上快速
     迭代（marching_preview，不进 Newton）→ 设计定型后回写（Δp/几何）重跑设计点。
  4. 可行性校核与记分卡（compute_scorecard）：区域 × 判据的裕度矩阵。

依赖方向：本模块只被 cooling_marching（惰性 import resolve）与 GUI 调用；
本模块对 cooling_marching 的调用（marching_preview）也走惰性 import，无循环依赖。
"""

from __future__ import annotations

import json
import math
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
        if not (10 <= self.n <= 2000):
            raise ValueError(f"通道数 n={self.n} 超范围 [10, 2000]")
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
            n=int(rr.get("n", 300)),
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


def default_rows_from_inputs(inputs, cooling_outputs: Optional[Dict] = None) -> List[ChannelRow]:
    """从当前单段标量生成等效单行表（工作区「从当前标量生成」按钮用）。"""
    co = cooling_outputs or {}
    n = int(co.get("n_channels", 0) or getattr(inputs, "cooling_n_channels", 300))
    h_mm = float(co.get("channel_height_mm", 0.0) or 0.0)
    if h_mm <= 0.1:
        h_mm = float(getattr(inputs, "cooling_channel_height_m", 5.0e-3)) * 1000.0
    return [ChannelRow(
        anchor="throat",
        n=n,
        w_mm=float(getattr(inputs, "cooling_channel_width_m", 1.5e-3)) * 1000.0,
        h_mm=h_mm,
        t_mm=float(getattr(inputs, "cooling_wall_thickness_m", 0.5e-3)) * 1000.0,
    )]


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
    resolved = sorted(
        ((_resolve_anchor_x(r, x_throat=x_throat, L_cyl=L_cyl, x_exit=x_exit), r)
         for r in rows),
        key=lambda t: t[0])
    xs_bp = [t[0] for t in resolved]
    rws = [t[1] for t in resolved]

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


# 制造可行性阈值（AM 打印铜合金通道的工程量级）
RIB_MIN_HARD_MM = 0.3    # 肋宽硬下限：低于此无法成形/强度不足
RIB_MIN_SOFT_MM = 0.8    # 肋宽软下限：AM 打印态建议 ≥0.8 mm
ASPECT_MAX = 8.0         # 通道深宽比上限（高深宽比铣削/打印难度大）


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
    elif rib_min_mm < RIB_MIN_HARD_MM:
        out.append(("fail", f"最小肋宽 {rib_min_mm:.2f} mm @ x={x_min_mm:.0f} mm "
                            f"< 硬下限 {RIB_MIN_HARD_MM} mm（无法制造）"))
    elif rib_min_mm < RIB_MIN_SOFT_MM:
        out.append(("warn", f"最小肋宽 {rib_min_mm:.2f} mm @ x={x_min_mm:.0f} mm "
                            f"< AM 打印建议值 {RIB_MIN_SOFT_MM} mm"))
    else:
        out.append(("ok", f"最小肋宽 {rib_min_mm:.2f} mm @ x={x_min_mm:.0f} mm ✓"))
    # 深宽比
    aspects = [h / max(w, 1e-9) for h, w in zip(hs, ws)]
    a_max = max(aspects)
    if a_max > ASPECT_MAX:
        out.append(("warn", f"最大深宽比 h/w = {a_max:.1f} > {ASPECT_MAX:.0f}（制造难度大）"))
    else:
        out.append(("ok", f"最大深宽比 h/w = {a_max:.1f} ✓"))
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
    coolant_out_T_K: float = 0.0     # 单点模型出口（对照）
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
    try:
        s = results.summary
        comps = results.components
        noz = comps["nozzle"].outputs
        ch = comps["main_chamber"].outputs
        At = float(noz.get("At_m2") or 0.0)
        if At <= 0:
            return None
        fuel_pump_out = results.states.get("fuel_after_pump")
        fuel_cooled = results.states.get("fuel_after_cooling")
        inj_ox = results.states.get("ox_turbine_exhaust")
        inj_fu = results.states.get("fuel_turbine_exhaust")
        from datetime import datetime
        snap = BoundarySnapshot(
            solved_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            converged=float(s.get("solver_residual_norm", 1.0)) < 1e-3,
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
            r_c_m=float(ch.get("cc_diameter_m", 0.0) or 0.0) / 2.0,
            L_cyl_m=float(ch.get("cc_length_m", 0.0) or 0.0),
            L_conv_m=float(ch.get("cc_convergent_length_mm", 0.0) or 0.0) / 1000.0,
            expansion_ratio=float(getattr(results.inputs, "expansion_ratio", 0.0)),
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

    只比较**影响循环解/推力室边界**的字段——通道分布表/膜冷却/辐射发射率等
    marching 后处理参数不会改变冻结边界本身，改了它们照样可以「冻结边界重算沿程」。
    """
    POST_ONLY = {
        "cooling_channel_table_json", "film_cooling_fraction",
        "film_effectiveness_initial", "film_decay_length_m",
        "cooling_gas_emissivity", "cooling_wall_temp_limit_K",
        "cooling_marching_enable", "cooling_cover_eps",
        "cooling_htc_enhancement", "cooling_channel_rel_roughness",
        "cooling_channel_k_minor",
    }
    fp = snap.inputs_fingerprint or {}
    if not fp:
        return []
    stale: List[str] = []
    try:
        cur = asdict(current_inputs)
    except Exception:
        return []
    for k, v_old in fp.items():
        if k in POST_ONLY:
            continue
        v_new = cur.get(k, v_old)
        try:
            same = (abs(float(v_new) - float(v_old)) <=
                    1e-9 * max(abs(float(v_old)), 1.0))
        except (TypeError, ValueError):
            same = (v_new == v_old)
        if not same:
            stale.append(k)
    return stale


# ════════════════════════════════════════════════════════════════════
# 4. 冻结边界的 marching 预览（不进 Newton、不改动主结果）
# ════════════════════════════════════════════════════════════════════

class _ResultsShell:
    """run_marching_postpass 需要的最小 CycleResults 外壳（浅拷贝容器，
    使 postpass 写入不污染真正的 last_results）。"""

    def __init__(self, base):
        self.inputs = base.inputs
        self.summary = dict(base.summary)
        self.states = base.states
        self.components = dict(base.components)
        self.warnings: List[str] = []


def marching_preview(base_results, inputs) -> Dict[str, Any]:
    """在冻结边界（base_results 的收敛解）上用**新的推力室参数** inputs 重算
    沿程 marching。返回 {"component", "profile", "warnings", "summary_delta"}；
    不修改 base_results。

    这是双层循环握手的部件级内环：改通道分布/膜冷却/材料 → 秒级看新剖面，
    不用重解 Newton。注意：冷却剂入口态/热力参数仍取冻结解——若新设计的
    Δp/吸热与单点模型差异大，应「回写循环重跑设计点」闭合。
    """
    from cooling_marching import run_marching_postpass
    shell = _ResultsShell(base_results)
    run_marching_postpass(shell, inputs)
    comp = shell.components.get("cooling_marching")
    profile = getattr(comp, "profile", None) if comp is not None else None
    delta: Dict[str, float] = {}
    if comp is not None:
        base_cm = base_results.components.get("cooling_marching")
        if base_cm is not None:
            for k in ("Q_total_MW", "delta_P_MPa", "T_coolant_out_K",
                      "T_wall_hot_max_K", "T_wall_cold_max_K", "V_coolant_max_ms"):
                v_new = float(comp.outputs.get(k, 0.0) or 0.0)
                v_old = float(base_cm.outputs.get(k, 0.0) or 0.0)
                delta[k] = v_new - v_old
    return {"component": comp, "profile": profile,
            "warnings": shell.warnings, "summary_delta": delta}


# ════════════════════════════════════════════════════════════════════
# 5. 校核记分卡（区域 × 判据）
# ════════════════════════════════════════════════════════════════════

def compute_scorecard(wf, results, inputs) -> List[Dict[str, Any]]:
    """区域 × 判据的裕度记分卡。

    wf：thermal_structural.WallField（domain="full"，marching 剖面来源）。
    返回行列表：{zone, zone_label, criterion, value, limit, margin_text, status}。
    status ∈ ok / warn / fail / na。
    """
    rows: List[Dict[str, Any]] = []
    if wf is None or not getattr(wf, "x", None):
        return rows
    s = results.summary if results is not None else {}
    T_limit = float(getattr(inputs, "cooling_wall_temp_limit_K", 800.0))
    T_coking = 950.0
    V_hard = 300.0
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
            limit=f"≤ {T_limit:.0f} K",
            margin_text=f"{T_limit - T_max:+.0f} K",
            status=_st(T_max <= T_limit, T_max <= T_limit * 1.10)))
        # ② 冷面壁温（结焦）
        i_Tc = max(idx, key=lambda i: wf.T_wc[i])
        Twc = wf.T_wc[i_Tc]
        rows.append(dict(
            zone=zkey, zone_label=zlabel, criterion="冷面壁温 T_wc（甲烷结焦）",
            value=f"{Twc:.0f} K",
            limit=f"≤ {T_coking:.0f} K",
            margin_text=f"{T_coking - Twc:+.0f} K",
            status=_st(Twc <= T_coking, Twc <= T_coking + 50.0)))
        # ③ 冷却剂流速
        if wf.v_coolant:
            i_V = max(idx, key=lambda i: wf.v_coolant[i])
            V = wf.v_coolant[i_V]
            rows.append(dict(
                zone=zkey, zone_label=zlabel, criterion="冷却剂流速 V",
                value=f"{V:.0f} m/s",
                limit=f"≤ {V_hard:.0f}（宜 ≤150）",
                margin_text=f"{V_hard - V:+.0f} m/s",
                status=_st(V <= 150.0, V <= V_hard)))
        # ④ 内壁结构 SF
        if wf.SF:
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
                    limit=f"≥ {RIB_MIN_SOFT_MM} mm（AM）",
                    margin_text=f"{(rib_min - RIB_MIN_SOFT_MM*1e-3)*1e3:+.2f} mm",
                    status=_st(rib_min >= RIB_MIN_SOFT_MM * 1e-3,
                               rib_min >= RIB_MIN_HARD_MM * 1e-3)))

    # 全程判据（不分区）
    if wf.p_coolant:
        p_in = max(wf.p_coolant)
        dp = p_in - min(wf.p_coolant)
        rows.append(dict(
            zone="global", zone_label="全程", criterion="冷却回路压降 Δp",
            value=f"{dp/1e6:.1f} MPa（入口 {p_in/1e6:.0f} MPa）",
            limit="≤ 0.45×入口压",
            margin_text=f"{(0.45*p_in - dp)/1e6:+.1f} MPa",
            status=_st(dp <= 0.45 * p_in, dp <= 0.60 * p_in)))
    dp_cycle = float(s.get("cooling_dp_MPa", 0.0) or 0.0)
    dp_march = float(s.get("cooling_marching_dp_MPa", 0.0) or 0.0)
    if dp_cycle > 0:
        mismatch = abs(dp_march - dp_cycle)
        rows.append(dict(
            zone="global", zone_label="全程", criterion="双层握手：Δp 口径一致性",
            value=f"marching {dp_march:.1f} vs 循环预算 {dp_cycle:.1f} MPa",
            limit="偏差 ≤ 30%",
            margin_text=f"{mismatch:.1f} MPa（{100.0*mismatch/max(dp_cycle,1e-9):.0f}%）",
            status=_st(mismatch <= 0.30 * dp_cycle, mismatch <= 0.60 * dp_cycle)))
    return rows
