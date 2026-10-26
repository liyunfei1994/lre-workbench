"""
fea_chamber.py — 主燃烧室壁 2D 有限元（scikit-fem）初步版本
================================================================

把 thermal_structural.py 的"1D 穿壁串联热阻 + 解析薄壁应力"升级为
**真二维有限元**：在喉部取一个冷却通道的横截面（周向 s × 径向 y），求解

  ① 2D 稳态导热温度场  T(s, y)
  ② 2D 平面应变热弹性应力场  σ(s, y)（热应力 + 通道冷却剂压力 + 燃气压力）

为什么是通道横截面（r-θ）而不是轴对称（r-x）：
  推力室壁很薄、轴向尺度大，轴向导热可忽略——1D 已抓住径向梯度。真正 1D
  抓不到、却是低周疲劳裂纹萌生点的，是**周向**的肋/通道结构效应：
    · 肋背后的燃气壁跑得更热（热量要绕过通道、沿肋向下传到冷却剂）；
    · 通道底角是应力集中点（热应力 + 冷却剂压差弯曲叠加）。
  这些只有 2D 横截面模型能给出。

边界条件（载荷来自已确定的载荷清单 ChamberLoadSet）：
  温度：燃气面给热流 q；通道三壁 Robin 对流 (h_c, T_co)；对称面/外侧绝热。
  结构：对称面 u_s=0；外侧(夹套界面) u_y=0；热应变 + 通道壁冷却剂压 + 燃气面室压。

定位：初步版（preliminary）。单通道半节距、平面应变、P1 三角元、线弹性、
温度无关的 E/α（取峰温值）。后续可扩温变物性、塑性、轴对称耦合、网格无关性。

依赖：scikit-fem（BSD，pip 可装）+ numpy/scipy。
"""

from __future__ import annotations

from task_control import checkpoint

import math
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class FEAResult:
    """2D FEA 结果（喉部通道横截面）。"""
    # 网格
    p: Any = None              # 节点坐标 (2, N) [m]
    t: Any = None              # 三角连接 (3, M)
    # 节点场
    T: Any = None              # 温度 [K]  (N,)
    # 单元场
    tri_T: Any = None          # 单元平均温度 [K] (M,)
    tri_vm: Any = None         # von Mises 应力 [MPa] (M,)
    tri_SF: Any = None         # 安全系数 (M,)
    # 几何标量 [mm]
    t_liner_mm: float = 0.0
    w_channel_mm: float = 0.0
    h_channel_mm: float = 0.0
    pitch_mm: float = 0.0
    # 峰值
    T_max_K: float = 0.0       # 峰值壁温（2D）
    T_gas_rib_K: float = 0.0   # 肋背燃气面温度
    T_gas_chan_K: float = 0.0  # 通道中心燃气面温度
    vm_max_MPa: float = 0.0    # 峰值 von Mises
    vm_p90_MPa: float = 0.0    # 90 分位（避开角点奇异看整体量级）
    min_SF: float = 0.0        # 最小安全系数（2D）
    x_vm_max_mm: tuple = (0.0, 0.0)   # 峰值应力位置 (s, y)
    plastic: bool = False      # 热衬里是否进入塑性（弹性 σ_vm > σ_y）
    q_gas_MW_m2: float = 0.0   # 燃气面有效热流（Robin 口径下自洽算出）
    material: str = ""
    material_key: str = ""
    T_max_use_K: float = 0.0   # 材料长期使用温度上限
    level: str = ""            # ok / marginal / gap（对比判定）
    verdict_zh: str = ""       # 中文判定
    notes: List[str] = field(default_factory=list)
    ok: bool = False


# ════════════════════════════════════════════════════════════════════
# 网格：单通道半节距的缺口矩形（周向 s × 径向 y）
# ════════════════════════════════════════════════════════════════════

def _build_channel_mesh(t_liner, h_chan, t_close, w_chan, pitch,
                        n_liner=4, n_chan_y=6, n_close=4,
                        n_chan_s=6, n_rib_s=6):
    """构造缺口矩形网格（半节距，对称）。

    域：s∈[0, pitch/2], y∈[0, H]，H = t_liner + h_chan + t_close。
    通道空腔（挖掉）：s∈[0, w_chan/2], y∈[t_liner, t_liner+h_chan]。
    返回 (p, t)：节点 (2,N)、三角 (3,M)。
    """
    checkpoint()
    import numpy as np

    Hs = 0.5 * pitch
    s_ch = 0.5 * w_chan
    y0, y1, y2 = t_liner, t_liner + h_chan, t_liner + h_chan + t_close

    # 网格线（确保通道边界落在网格线上）
    s_lines = np.unique(np.concatenate([
        np.linspace(0.0, s_ch, n_chan_s + 1),
        np.linspace(s_ch, Hs, n_rib_s + 1),
    ]))
    y_lines = np.unique(np.concatenate([
        np.linspace(0.0, y0, n_liner + 1),
        np.linspace(y0, y1, n_chan_y + 1),
        np.linspace(y1, y2, n_close + 1),
    ]))

    ns, ny = len(s_lines), len(y_lines)
    # 节点编号网格
    idx = -np.ones((ns, ny), dtype=int)
    pts = []

    def _in_void(sc, yc):
        return (sc < s_ch - 1e-12) and (y0 + 1e-12 < yc < y1 - 1e-12)

    # 先标记将被用到的节点（属于任一非空腔单元）
    used = np.zeros((ns, ny), dtype=bool)
    cells = []
    for i in range(ns - 1):
        checkpoint()
        for j in range(ny - 1):
            checkpoint()
            sc = 0.5 * (s_lines[i] + s_lines[i + 1])
            yc = 0.5 * (y_lines[j] + y_lines[j + 1])
            if _in_void(sc, yc):
                continue
            cells.append((i, j))
            used[i, j] = used[i + 1, j] = used[i, j + 1] = used[i + 1, j + 1] = True

    for i in range(ns):
        checkpoint()
        for j in range(ny):
            checkpoint()
            if used[i, j]:
                idx[i, j] = len(pts)
                pts.append((s_lines[i], y_lines[j]))

    tris = []
    for (i, j) in cells:
        checkpoint()
        a = idx[i, j]; b = idx[i + 1, j]; c = idx[i + 1, j + 1]; d = idx[i, j + 1]
        # 两个三角（对角 a-c）
        tris.append((a, b, c))
        tris.append((a, c, d))

    p = np.array(pts).T.astype(float)          # (2, N)
    t = np.array(tris).T.astype(np.int64)      # (3, M)
    return p, t, (Hs, s_ch, y0, y1, y2)


# ════════════════════════════════════════════════════════════════════
# 求解
# ════════════════════════════════════════════════════════════════════

def solve_throat_channel_2d(
    *,
    t_liner_m: float, h_channel_m: float, w_channel_m: float,
    pitch_m: float, t_close_m: float = 1.0e-3,
    q_gas_W_m2: float = 0.0, h_c_W_m2K: float, T_coolant_K: float, p_coolant_Pa: float,
    p_gas_Pa: float,
    E_Pa: float, nu: float, alpha_1perK: float, k_WmK: float,
    sigma_y_func=None, T_ref_K: float = 300.0,
    material_name: str = "GRCop-84",
    T_aw_K: float = 0.0, h_g_W_m2K: float = 0.0,
) -> FEAResult:
    """求解喉部通道横截面 2D 热-结构场。sigma_y_func(T)->Pa 给屈服（缺则常数）。

    燃气侧边界两种口径：
      · 给 h_g_W_m2K>0 + T_aw_K → 用 Robin 对流边界 h_g·(T_aw−T)，**材料无关**
        （不同 k 的材料自洽得到各自的 T_wg 与热流，用于公平对比候选材料）；
      · 否则 → 用固定热流 q_gas_W_m2（与某一材料的 1D 解同口径）。
    """
    checkpoint()
    import numpy as np
    from skfem import (MeshTri, Basis, ElementTriP1, ElementVector,
                       FacetBasis, BilinearForm, LinearForm, asm, solve, condense)
    from skfem.helpers import dot, grad, div

    res = FEAResult(material=material_name)
    try:
        p, t, (Hs, s_ch, y0, y1, y2) = _build_channel_mesh(
            t_liner_m, h_channel_m, t_close_m, w_channel_m, pitch_m)
        m = MeshTri(p, t)

        elem_s = ElementTriP1()
        elem_v = ElementVector(ElementTriP1())
        sb = Basis(m, elem_s, intorder=2)
        vb = Basis(m, elem_v, intorder=2)

        # ── 边界 facet 分类（按中点坐标）──
        bf = m.boundary_facets()
        fmid = m.p[:, m.facets[:, bf]].mean(axis=1)   # (2, nbf)
        sm, ym = fmid[0], fmid[1]
        tol = 1e-9
        f_gas = bf[ym < 1e-7]                                   # 燃气面 y≈0
        # 通道三壁：底(y≈y0,s<s_ch) 顶(y≈y1,s<s_ch) 侧(s≈s_ch, y0<y<y1)
        f_ch_bot = bf[(np.abs(ym - y0) < 1e-7) & (sm < s_ch - 1e-9)]
        f_ch_top = bf[(np.abs(ym - y1) < 1e-7) & (sm < s_ch - 1e-9)]
        f_ch_side = bf[(np.abs(sm - s_ch) < 1e-7) & (ym > y0 + 1e-9) & (ym < y1 - 1e-9)]
        f_chan = np.concatenate([f_ch_bot, f_ch_top, f_ch_side])
        f_top = bf[np.abs(ym - y2) < 1e-7]                     # 外侧(夹套界面) y≈H
        f_sym0 = bf[sm < 1e-7]                                  # 对称面 s≈0
        f_symH = bf[np.abs(sm - Hs) < 1e-7]                    # 对称面 s≈pitch/2

        # ── ① 温度场：k∇²T，燃气面给热流 q，通道壁 Robin(h_c,T_co)──
        fb_gas = FacetBasis(m, elem_s, facets=f_gas, intorder=2)
        fb_chan = FacetBasis(m, elem_s, facets=f_chan, intorder=2)

        @BilinearForm
        def conduction(u, v, w):
            return k_WmK * dot(grad(u), grad(v))

        @BilinearForm
        def robin(u, v, w):
            return h_c_W_m2K * u * v

        @LinearForm
        def robin_rhs(v, w):
            return h_c_W_m2K * T_coolant_K * v

        @LinearForm
        def flux_rhs(v, w):
            return q_gas_W_m2 * v

        checkpoint()
        K_T = asm(conduction, sb) + asm(robin, fb_chan)
        checkpoint()
        f_T = asm(robin_rhs, fb_chan)
        checkpoint()
        if h_g_W_m2K > 0.0 and T_aw_K > 0.0:
            # 燃气侧 Robin（材料无关）：h_g·(T_aw − T)
            @BilinearForm
            def robin_gas(u, v, w):
                return h_g_W_m2K * u * v

            @LinearForm
            def robin_gas_rhs(v, w):
                return h_g_W_m2K * T_aw_K * v

            checkpoint()
            K_T = K_T + asm(robin_gas, fb_gas)
            checkpoint()
            f_T = f_T + asm(robin_gas_rhs, fb_gas)
            checkpoint()
        else:
            checkpoint()
            f_T = f_T + asm(flux_rhs, fb_gas)     # 固定热流口径
            checkpoint()
        T = solve(K_T, f_T)                        # Robin 项保证非奇异
        checkpoint()

        # 单元面积（应力恢复 + 应力参考温度 用）
        pe = m.p[:, m.t]                          # (2,3,M)
        x1, y1c = pe[0, 0], pe[1, 0]; x2, y2c = pe[0, 1], pe[1, 1]; x3, y3c = pe[0, 2], pe[1, 2]
        area = 0.5 * np.abs((x2 - x1) * (y3c - y1c) - (x3 - x1) * (y2c - y1c))   # (M,)
        tri_T = T[m.t].mean(axis=0)               # 单元平均温度
        # 关键：热应力参考温度取截面**面积加权平均温度**——壁体自由径向膨胀
        # （随室壁一起涨）是无应力的，应力只来自温度相对均值的偏差（梯度）。
        # 若误用 300 K 绝对参考，会把整体升温(~900 K)的"虚假约束应力"算进去。
        T_ref_eff = float((area * tri_T).sum() / max(area.sum(), 1e-30))

        # ── ② 平面应变热弹性 ──
        lam = E_Pa * nu / ((1.0 + nu) * (1.0 - 2.0 * nu))
        mu = E_Pa / (2.0 * (1.0 + nu))
        beta = E_Pa * alpha_1perK / (1.0 - 2.0 * nu)   # (3λ+2μ)α

        @BilinearForm
        def elasticity(u, v, w):
            # 平面应变各向同性刚度
            du = grad(u); dv = grad(v)
            exx = du[0][0]; eyy = du[1][1]; exy = 0.5 * (du[0][1] + du[1][0])
            fxx = dv[0][0]; fyy = dv[1][1]; fxy = 0.5 * (dv[0][1] + dv[1][0])
            tr_e = exx + eyy; tr_f = fxx + fyy
            return (lam * tr_e * tr_f
                    + 2.0 * mu * (exx * fxx + eyy * fyy + 2.0 * exy * fxy))

        dT_q = sb.interpolate(T - T_ref_eff)

        @LinearForm
        def thermal_load(v, w):
            return beta * w["dT"] * div(v)

        checkpoint()
        K_u = asm(elasticity, vb)
        checkpoint()
        f_u = asm(thermal_load, vb, dT=dT_q)
        checkpoint()

        # 压力面载荷（牵引 = -p·n，压入固体）
        for facets, pval in ((f_chan, p_coolant_Pa), (f_gas, p_gas_Pa)):
            checkpoint()
            if len(facets) == 0:
                continue
            fbv = FacetBasis(m, elem_v, facets=facets, intorder=2)

            @LinearForm
            def press(v, w, _p=pval):
                return -_p * dot(w.n, v)

            checkpoint()
            f_u = f_u + asm(press, fbv)
            checkpoint()

        # 约束：对称面 u_s=0（x 分量），外侧 u_y=0（y 分量）
        # ElementVector 自由度交错排列 [u_x0,u_y0,u_x1,u_y1,...]
        dofs_symx = vb.get_dofs(facets=np.concatenate([f_sym0, f_symH])).nodal["u^1"]
        dofs_topy = vb.get_dofs(facets=f_top).nodal["u^2"]
        D = np.concatenate([dofs_symx, dofs_topy])
        checkpoint()
        u = solve(*condense(K_u, f_u, D=D))
        checkpoint()

        # ── 应力恢复（P1 → 单元常应变）──
        uf = vb.interpolate(u)
        g = uf.grad                       # (2,2,nelem,nqp)
        exx = g[0, 0].mean(axis=1); eyy = g[1, 1].mean(axis=1)
        exy = 0.5 * (g[0, 1] + g[1, 0]).mean(axis=1)
        dT_e = tri_T - T_ref_eff                           # (nelem,) 相对均值

        tr = exx + eyy
        sxx = lam * tr + 2 * mu * exx - beta * dT_e
        syy = lam * tr + 2 * mu * eyy - beta * dT_e
        szz = lam * tr - beta * dT_e                       # 平面应变 ε_zz=0
        sxy = 2 * mu * exy
        vm = np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                     + 3.0 * sxy ** 2) / 1e6               # MPa

        # 屈服与 SF
        if sigma_y_func is not None:
            sy = np.array([sigma_y_func(float(tt)) for tt in tri_T]) / 1e6  # MPa
        else:
            sy = np.full_like(vm, 300.0)
        SF = sy / np.maximum(vm, 1e-6)

        # 峰值/位置
        T_gas_face = T.copy()
        gas_nodes = np.where(m.p[1] < 1e-7)[0]
        s_gas = m.p[0, gas_nodes]
        # 肋背（s 接近 pitch/2）vs 通道中心（s≈0）的燃气面温度
        rib_node = gas_nodes[np.argmax(s_gas)]
        chan_node = gas_nodes[np.argmin(s_gas)]
        i_vm = int(np.argmax(vm))
        cen = m.p[:, m.t[:, i_vm]].mean(axis=1)

        res.p, res.t, res.T = m.p, m.t, T
        res.tri_T, res.tri_vm, res.tri_SF = tri_T, vm, SF
        res.t_liner_mm = t_liner_m * 1e3
        res.w_channel_mm = w_channel_m * 1e3
        res.h_channel_mm = h_channel_m * 1e3
        res.pitch_mm = pitch_m * 1e3
        res.T_max_K = float(T.max())
        res.T_gas_rib_K = float(T[rib_node])
        res.T_gas_chan_K = float(T[chan_node])
        res.vm_max_MPa = float(vm.max())
        res.vm_p90_MPa = float(np.percentile(vm, 90))
        res.min_SF = float(SF.min())
        res.x_vm_max_mm = (float(cen[0] * 1e3), float(cen[1] * 1e3))
        res.plastic = bool(res.min_SF < 1.0)
        # 燃气面有效热流（Robin 口径：q=h_g·(T_aw−T_wg面均温)；固定热流口径直接取 q_gas）
        if h_g_W_m2K > 0.0 and T_aw_K > 0.0:
            T_gas_mean = float(T[gas_nodes].mean())
            res.q_gas_MW_m2 = h_g_W_m2K * (T_aw_K - T_gas_mean) / 1e6
        else:
            res.q_gas_MW_m2 = q_gas_W_m2 / 1e6
        res.notes = [
            f"2D FEA(喉部通道横截面,scikit-fem,平面应变,{material_name}):"
            f"网格 {m.t.shape[1]} 三角元,T_ref(无应力)取截面均温 {T_ref_eff:.0f} K。",
            f"峰值壁温 T_max = {res.T_max_K:.0f} K;通道中心燃气面 {res.T_gas_chan_K:.0f} K "
            f"vs 肋中心燃气面 {res.T_gas_rib_K:.0f} K(周向温差 "
            f"{res.T_gas_chan_K - res.T_gas_rib_K:+.0f} K——高肋高宽比下肋被通道侧壁高效冷却,"
            f"通道中心反而最热;这是 1D 抓不到的周向效应)",
            f"峰值 von Mises σ_vm = {res.vm_max_MPa:.0f} MPa(90 分位 {res.vm_p90_MPa:.0f}) @ "
            f"(s={res.x_vm_max_mm[0]:.2f}, y={res.x_vm_max_mm[1]:.2f}) mm;最小 SF = {res.min_SF:.2f}",
            ("⚠ 热衬里弹性应力 ≫ 屈服 → **塑性工作**:真实应力被屈服钳住、超出部分转为塑性应变。"
             "2D 揭示薄热衬里被冷背结构约束,热应力远高于 1D 薄壁梯度估计——这正是再生冷却铜室"
             "每次点火-关机循环塑性、LCF/doghouse 失效的根因(本页只定载荷/应力,寿命循环数另算)。"
             if res.plastic else "弹性 SF≥1,衬里弹性工作。"),
        ]
        res.ok = True
    except Exception as exc:
        import traceback
        res.notes = [f"2D FEA 失败:{exc}", traceback.format_exc()]
        res.ok = False
    return res


# ════════════════════════════════════════════════════════════════════
# 从 WallField/几何 提取喉部输入并求解
# ════════════════════════════════════════════════════════════════════

def _station_boundary(wf, geometry: Dict[str, Any],
                      station_index: Optional[int] = None) -> Dict[str, float]:
    """从 WallField **任意站位** 提取材料无关的边界条件 + 通道几何。

    station_index=None → 喉部站（旧行为）。V19 泛化：推力室设计工作区的站位
    游标可对沿程任意站跑 2D FEM。

    燃气侧取 T_aw、h_g（与材料无关），冷却侧取 h_c、T_co、p_co——而**不**取
    wf.T_wg/wf.q（这两个已被原内壁材料的 k 污染）。这样换不同 k 的材料时，
    2D FEA 自洽算出各自的 T_wg 与热流，实现公平比较。

    通道几何优先级：wf 沿程数组（V19 marching 分布模式逐站真实值）→
    geometry["chamber"] 标量（旧回退）。
    燃气静压：wf.p_gas[站]（V19 真实局部静压）→ 0.55·pc 旧近似。
    """
    checkpoint()
    ti = wf.throat_index if station_index is None else int(station_index)
    ti = max(0, min(ti, len(wf.x) - 1)) if wf.x else 0
    g_n = geometry.get("nozzle") or {}
    rt = float(g_n.get("throat_radius_m", 0.0) or 0.0)
    if rt <= 0 and wf.r:
        rt = min(wf.r)
    r_loc = float(wf.r[ti]) if wf.r else rt
    g_ch = geometry.get("chamber") or {}

    def _at(arr, default):
        return float(arr[ti]) if arr and len(arr) > ti else default

    def _geometry_at(name):
        arr = getattr(wf, name, None)
        if arr is None or len(arr) == 0:
            return None
        if ti >= len(arr):
            raise ValueError(f"FEA所需沿程几何 {name} 缺少当前站位，不能回退估算。")
        value = float(arr[ti])
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"FEA沿程几何 {name} 必须为有限正数，不能回退估算。")
        return value

    # 通道几何：优先 wf 沿程数组（分布模式），回退 geometry 标量
    n_value = _geometry_at("n_chan")
    if n_value is not None and int(n_value) != n_value:
        raise ValueError("FEA沿程通道数必须为整数，不能截断小数部分。")
    n_chan = int(n_value or 0)
    if n_chan <= 0:
        n_chan = int(g_ch.get("num_cooling_channels", 0) or 0)
    if n_chan <= 0:
        n_chan = max(int(round(2 * math.pi * rt / 4.0e-3)), 60)
    if not math.isfinite(r_loc) or r_loc <= 0:
        raise ValueError("FEA实际截面半径必须为有限正数。")
    pitch = 2.0 * math.pi * r_loc / n_chan
    w_chan = _geometry_at("w_chan")
    if w_chan is None:
        w_chan = float(g_ch.get("cooling_channel_width_mm", 0.0) or 0.0) / 1e3
    if w_chan <= 0:
        w_chan = min(0.55 * pitch, 0.8 * pitch)
    if not math.isfinite(w_chan) or not 0 < w_chan < pitch:
        raise ValueError("FEA通道宽度必须介于零与实际节距之间；不能裁窄已解几何。")
    h_chan = _geometry_at("h_chan")
    if h_chan is None:
        h_chan = float(g_ch.get("cooling_channel_height_mm", 4.0) or 4.0) / 1e3
    t_liner = _geometry_at("t_liner")
    if t_liner is None:
        t_liner = float(wf.t_wall_m or 0.5e-3)
    if not all(math.isfinite(v) and v > 0 for v in (h_chan, t_liner)):
        raise ValueError("FEA通道深度与内衬厚度必须为有限正数。")

    # 燃气静压：V19 marching 剖面带局部静压；旧剖面回退 0.55·pc 喉部近似
    p_gas = _at(getattr(wf, "p_gas", None), 0.0)
    if p_gas <= 0:
        p_gas = 0.55 * (wf.loads.p_gas_MPa * 1e6) if getattr(wf, "loads", None) else 0.55 * 33e6
    return dict(
        rt=rt, n_chan=n_chan, pitch_m=pitch, w_channel_m=w_chan, h_channel_m=h_chan,
        t_liner_m=t_liner,
        T_aw_K=_at(wf.T_aw, 3500.0), h_g_W_m2K=_at(wf.h_g, 5.0e4),
        h_c_W_m2K=_at(wf.h_c, 2.0e5), T_coolant_K=_at(wf.T_coolant, 300.0),
        p_coolant_Pa=_at(wf.p_coolant, 60e6), p_gas_Pa=p_gas,
    )


def _throat_boundary(wf, geometry: Dict[str, Any]) -> Dict[str, float]:
    """旧接口（喉部站）——委托 _station_boundary(None)。"""
    checkpoint()
    return _station_boundary(wf, geometry, None)


def _solve_material_2d(bc: Dict[str, float], inner_material_key: str) -> FEAResult:
    """用材料无关边界 bc + 指定材料，跑一次 2D FEA（材料的 k(T)/E(T)/σy(T) 自洽）。"""
    checkpoint()
    from materials import get_material
    from thermal_structural import _E_of_T, _k_of_T
    mat = get_material(inner_material_key)
    # 物性在代表热壁温(~900K)评估 k，对各材料同口径；E 同口径
    T_eval = 900.0
    res = solve_throat_channel_2d(
        t_liner_m=bc["t_liner_m"], h_channel_m=bc["h_channel_m"],
        w_channel_m=bc["w_channel_m"], pitch_m=bc["pitch_m"],
        h_c_W_m2K=bc["h_c_W_m2K"], T_coolant_K=bc["T_coolant_K"],
        p_coolant_Pa=bc["p_coolant_Pa"], p_gas_Pa=bc["p_gas_Pa"],
        T_aw_K=bc["T_aw_K"], h_g_W_m2K=bc["h_g_W_m2K"],
        E_Pa=_E_of_T(mat, T_eval), nu=mat.poisson_ratio,
        alpha_1perK=mat.cte_1e6_per_K * 1e-6, k_WmK=_k_of_T(mat, T_eval),
        sigma_y_func=lambda T: mat.sigma_yield_at(T) * 1e6,
        material_name=mat.display_name.split(" ")[0],
    )
    # 材料许用温度 + 判定
    res.T_max_use_K = float(mat.T_max_use_K)
    res.material_key = inner_material_key
    if res.ok:
        over_t = res.T_max_K - res.T_max_use_K
        if over_t > 0:
            res.level = "gap"
            res.verdict_zh = f"热面壁温 {res.T_max_K:.0f}K 超材料长期上限 {res.T_max_use_K:.0f}K（+{over_t:.0f}K）→ 扛不住"
        elif res.min_SF < 1.0:
            res.level = "marginal"
            res.verdict_zh = f"温度在上限内但 SF={res.min_SF:.2f}<1 → 衬里塑性，临界/需强化"
        else:
            res.level = "ok"
            res.verdict_zh = f"温度{res.T_max_K:.0f}K<上限{res.T_max_use_K:.0f}K 且 SF={res.min_SF:.2f}≥1 → 扛得住"
    return res


def solve_from_wallfield(wf, geometry: Dict[str, Any],
                         inner_material_key: str = "grcop84",
                         station_index: Optional[int] = None) -> FEAResult:
    """从 WallField + 几何取材料无关边界，对单一材料跑 2D FEA。

    station_index=None → 喉部站（旧行为）；V19：可指定沿程任意站位。"""
    checkpoint()
    if not getattr(wf, "x", None):
        return FEAResult(ok=False, notes=["无 WallField 数据"])
    return _solve_material_2d(_station_boundary(wf, geometry, station_index),
                              inner_material_key)


def compare_liner_materials_2d(wf, geometry: Dict[str, Any],
                               candidate_keys=None):
    """对所有候选铜合金内衬各跑一遍喉部 2D FEA，返回按裕度排序的结果列表（谁扛得住）。"""
    checkpoint()
    if not getattr(wf, "x", None):
        return []
    if candidate_keys is None:
        from materials import MATERIALS
        candidate_keys = [k for k, m in MATERIALS.items()
                          if "chamber_inner_wall" in getattr(m, "parts", [])]
    bc = _throat_boundary(wf, geometry)
    results = []
    for key in candidate_keys:
        checkpoint()
        try:
            results.append(_solve_material_2d(bc, key))
        except Exception:
            pass
    # 排序：先按是否扛得住(ok>marginal>gap)，再按 min_SF 降序
    rank = {"ok": 0, "marginal": 1, "gap": 2}
    results.sort(key=lambda r: (rank.get(r.level, 3), -r.min_SF))
    return results


# ════════════════════════════════════════════════════════════════════
# 绘图：2D 温度场 + von Mises 应力场（真二维云图）
# ════════════════════════════════════════════════════════════════════

def plot_fea_fields(fig, res: FEAResult) -> None:
    """画 2D 温度场 + von Mises 应力场（通道横截面），含网格轮廓。"""
    import numpy as np
    from matplotlib.tri import Triangulation

    fig.clear()
    fig.patch.set_facecolor("#FAFAFA")
    if not res.ok or res.p is None:
        ax = fig.add_subplot(111); ax.axis("off")
        msg = (res.notes[0] if res.notes else "FEA 无结果")
        ax.text(0.5, 0.5, msg, ha="center", va="center", transform=ax.transAxes,
                color="#9CA3AF", fontsize=10, style="italic")
        return

    p = res.p * 1e3       # mm
    tri = Triangulation(p[0], p[1], res.t.T)
    s_max = float(p[0].max()); y_max = float(p[1].max())
    y0 = res.t_liner_mm; y1 = y0 + res.h_channel_mm; sc = 0.5 * res.w_channel_mm
    # 周向放大倍数（让细高截面铺满面板、看得清场分布；不锁等比，标注说明）
    s_exag = max(1.0, round(y_max / max(s_max, 1e-6) / 2.0))

    ax_T = fig.add_subplot(121)
    ax_S = fig.add_subplot(122)

    def _decorate(ax, is_stress):
        # 通道空腔轮廓
        ax.plot([0, sc, sc, 0, 0], [y0, y0, y1, y1, y0],
                color="#0ea5e9", lw=1.4, ls="-", alpha=0.9, zorder=5)
        # 物理标注
        ax.annotate("燃气面 (热流 q 入)", (sc * 0.5, 0), (sc * 0.5, -0.45 * y0 - 0.05),
                    ha="center", va="top", fontsize=8, color="#b91c1c",
                    arrowprops=dict(arrowstyle="->", color="#b91c1c", lw=0.8))
        ax.text(sc * 0.5, 0.5 * (y0 + y1), "冷却通道\n(冷却剂)", ha="center", va="center",
                fontsize=8, color="#0369a1", style="italic")
        ax.text(0.5 * (sc + s_max), 0.5 * (y0 + y1), "肋\nrib", ha="center", va="center",
                fontsize=8, color="#374151")
        ax.text(0.5 * s_max, y_max, "冷面/夹套界面", ha="center", va="bottom",
                fontsize=7.5, color="#6b7280")
        ax.set_xlabel(f"周向 s [mm]  (通道中心→肋中心；显示放大 {s_exag:.0f}×)",
                      fontsize=8.5, color="#6B7280")
        ax.set_ylabel("径向 y [mm]  (燃气面 y=0 → 外侧)", fontsize=8.5, color="#6B7280")
        ax.tick_params(labelsize=8, colors="#6B7280")
        ax.set_xlim(-0.02 * s_max, s_max * 1.02)
        ax.set_ylim(y_max * 1.02, -0.18 * y_max)    # 反向：燃气面在上 + 顶部留标注空间
        ax.set_box_aspect((y_max) / (s_max * s_exag))  # 周向放大后的盒比

    # ① 温度场（gouraud 平滑 + 等温黑线）
    c1 = ax_T.tripcolor(tri, res.T, cmap="inferno", shading="gouraud")
    try:
        lv = _np_levels(res.T_max_K)
        ax_T.tricontour(tri, res.T, levels=lv, colors="k", linewidths=0.5, alpha=0.55)
    except Exception:
        pass
    ax_T.set_title("① 2D 温度场 T(s,y) [K]", fontsize=10, fontweight="bold",
                   loc="left", color="#111827")
    cb1 = fig.colorbar(c1, ax=ax_T, fraction=0.09, pad=0.02); cb1.set_label("T [K]", fontsize=8)
    # 标通道中心 vs 肋背燃气面温度（1D 抓不到的周向温差）
    ax_T.plot(0, 0, "v", ms=7, color="#fde047", mec="#7c2d12", zorder=6)
    ax_T.plot(s_max, 0, "v", ms=7, color="#fb923c", mec="#7c2d12", zorder=6)
    ax_T.annotate(f"通道中心 {res.T_gas_chan_K:.0f}K", (0, 0), (0, -0.12 * y_max),
                  fontsize=7.5, color="#7c2d12", ha="left")
    ax_T.annotate(f"肋背 {res.T_gas_rib_K:.0f}K (周向Δ{res.T_gas_chan_K-res.T_gas_rib_K:+.0f}K)",
                  (s_max, 0), (s_max, -0.12 * y_max), fontsize=7.5, color="#7c2d12", ha="right")
    _decorate(ax_T, False)

    # ② von Mises 应力场（clim 截到 p90 避免角点奇异刷爆色标）
    vmax = max(res.vm_p90_MPa, 1.0)
    c2 = ax_S.tripcolor(tri, res.tri_vm, cmap="viridis", shading="flat", vmax=vmax)
    ax_S.set_title("② 2D von Mises 应力场 σ_vm(s,y) [MPa]", fontsize=10,
                   fontweight="bold", loc="left", color="#111827")
    cb2 = fig.colorbar(c2, ax=ax_S, fraction=0.09, pad=0.02)
    cb2.set_label(f"σ_vm [MPa]（色标截至 p90={vmax:.0f}）", fontsize=7.5)
    ax_S.plot(res.x_vm_max_mm[0], res.x_vm_max_mm[1], "o", ms=9,
              mfc="none", mec="#dc2626", mew=2.0, zorder=6)
    ax_S.annotate(f"峰值 {res.vm_max_MPa:.0f} MPa\nmin SF={res.min_SF:.2f}",
                  res.x_vm_max_mm, (res.x_vm_max_mm[0], res.x_vm_max_mm[1] + 0.10 * y_max),
                  fontsize=7.5, color="#dc2626", fontweight="bold", ha="center")
    _decorate(ax_S, True)

    plastic_txt = ("⚠ 衬里塑性工作(SF<1)：弹性应力远超屈服，热衬里被冷背结构约束，"
                   "每次点火-关机循环塑性——这是 1D 薄壁估计抓不到、却是 LCF 失效根因的关键。"
                   if res.plastic else "弹性 SF≥1，衬里弹性工作。")
    fig.suptitle(
        f"主燃烧室喉部 通道横截面 2D 有限元（scikit-fem，平面应变）  ·  {res.material}"
        f"  ·  通道 {res.w_channel_mm:.2f}×{res.h_channel_mm:.2f}mm / 衬里 {res.t_liner_mm:.2f}mm",
        fontsize=11, fontweight="bold", color="#0f172a")
    fig.text(0.5, 0.015, plastic_txt, ha="center", va="bottom", fontsize=8,
             color="#7c2d12", wrap=True)
    fig.subplots_adjust(left=0.07, right=0.97, top=0.90, bottom=0.16, wspace=0.30)


def _np_levels(tmax):
    import numpy as np
    step = 100.0 if tmax > 700 else 50.0
    return np.arange(300.0, tmax, step)


def plot_material_comparison_2d(fig, results) -> None:
    """候选铜合金内衬喉部 2D FEA 对比图：壁温 vs 材料上限 + min SF + 判定。"""
    import numpy as np
    fig.clear(); fig.patch.set_facecolor("#FAFAFA")
    results = [r for r in results if getattr(r, "ok", False)]
    if not results:
        ax = fig.add_subplot(111); ax.axis("off")
        ax.text(0.5, 0.5, "无可对比的 2D FEA 结果", ha="center", va="center",
                transform=ax.transAxes, color="#9CA3AF", fontsize=11, style="italic")
        return
    names = [r.material for r in results]
    x = np.arange(len(results))
    T_wg = [r.T_max_K for r in results]
    T_lim = [r.T_max_use_K for r in results]
    SF = [r.min_SF for r in results]
    col = {"ok": "#16a34a", "marginal": "#d97706", "gap": "#dc2626"}
    colors = [col.get(r.level, "#6b7280") for r in results]

    ax1 = fig.add_subplot(121)
    ax1.bar(x, T_wg, color=colors, alpha=0.85, width=0.6, label="2D 峰值热面壁温 T_wg")
    ax1.plot(x, T_lim, "_", ms=26, mew=3, color="#111827", label="材料长期温度上限")
    for i, r in enumerate(results):
        ax1.annotate(f"{T_wg[i]:.0f}", (i, T_wg[i]), (0, 3), textcoords="offset points",
                     ha="center", fontsize=8, fontweight="bold")
        ax1.annotate(f"上限{T_lim[i]:.0f}", (i, T_lim[i]), (0, 5), textcoords="offset points",
                     ha="center", fontsize=7, color="#111827")
    ax1.set_xticks(x); ax1.set_xticklabels(names, rotation=20, ha="right", fontsize=8)
    ax1.set_ylabel("温度 [K]", fontsize=9)
    ax1.set_title("热面壁温 vs 材料温度上限（同一冷却工况）", fontsize=10,
                  fontweight="bold", loc="left", color="#111827")
    ax1.legend(fontsize=7.5, loc="upper right")
    ax1.grid(True, axis="y", alpha=0.25)

    ax2 = fig.add_subplot(122)
    ax2.bar(x, SF, color=colors, alpha=0.85, width=0.6)
    ax2.axhline(1.0, color="#b91c1c", ls="--", lw=1.2)
    ax2.annotate("SF=1（屈服临界）", (len(results) - 1, 1.0), (0, 4),
                 textcoords="offset points", ha="right", fontsize=7.5, color="#b91c1c")
    for i in x:
        ax2.annotate(f"{SF[i]:.2f}", (i, SF[i]), (0, 3), textcoords="offset points",
                     ha="center", fontsize=8, fontweight="bold")
    ax2.set_xticks(x); ax2.set_xticklabels(names, rotation=20, ha="right", fontsize=8)
    ax2.set_ylabel("最小安全系数 SF [-]", fontsize=9)
    ax2.set_ylim(0, max(1.3, max(SF) * 1.15))
    ax2.set_title("最小安全系数 min SF（2D，含热应力+压差弯曲）", fontsize=10,
                  fontweight="bold", loc="left", color="#111827")
    ax2.grid(True, axis="y", alpha=0.25)


def plot_material_fields_grid(fig, results) -> None:
    """所有候选内衬材料的 2D 温度场云图**并排对比**（同一冷却工况 + 同色标）。

    每种材料一幅喉部通道横截面温度场；同一色标（越亮越热）→ 一眼看出哪种材料
    把热面壁温压得最低、谁超了自己的温度上限。标题给材料 + 峰值 T_wg + 上限 + min SF。
    """
    import numpy as np
    from matplotlib.tri import Triangulation

    fig.clear(); fig.patch.set_facecolor("#FAFAFA")
    rs = [r for r in results if getattr(r, "ok", False) and r.p is not None]
    if not rs:
        ax = fig.add_subplot(111); ax.axis("off")
        ax.text(0.5, 0.5, "无可对比的 2D FEA 结果（需 scikit-fem + 候选材料）",
                ha="center", va="center", transform=ax.transAxes,
                color="#9CA3AF", fontsize=11, style="italic")
        return
    N = len(rs)
    Tmin = min(float(r.T.min()) for r in rs)      # 同色标，公平对比谁更热（节点温度）
    Tmax = max(float(r.T.max()) for r in rs)
    lvl_col = {"ok": "#16a34a", "marginal": "#d97706", "gap": "#dc2626"}
    last = None
    for i, res in enumerate(rs):
        ax = fig.add_subplot(1, N, i + 1)
        p = res.p * 1e3
        tri = Triangulation(p[0], p[1], res.t.T)
        last = ax.tripcolor(tri, res.T, cmap="inferno", shading="gouraud",
                            vmin=Tmin, vmax=Tmax)
        y0 = res.t_liner_mm; y1 = y0 + res.h_channel_mm; sc = 0.5 * res.w_channel_mm
        ax.plot([0, sc, sc, 0, 0], [y0, y0, y1, y1, y0], color="#0ea5e9", lw=1.0, alpha=0.85)
        s_max = float(p[0].max()); y_max = float(p[1].max())
        ax.set_xlim(-0.02 * s_max, s_max * 1.02)
        ax.set_ylim(y_max * 1.02, -0.04 * y_max)     # 燃气面在上
        ax.set_box_aspect(2.5)
        ax.set_xticks([])
        if i == 0:
            ax.set_ylabel("径向 y [mm]（燃气面在上 → 外侧）", fontsize=8, color="#6B7280")
            ax.tick_params(labelsize=7, colors="#6B7280")
        else:
            ax.set_yticks([])
        mk = "✓" if res.level == "ok" else ("~" if res.level == "marginal" else "✗")
        tcol = lvl_col.get(res.level, "#374151")
        ax.set_title(f"{mk} {res.material}\nT_wg {res.T_max_K:.0f} K\n上限 {res.T_max_use_K:.0f} · SF {res.min_SF:.2f}",
                     fontsize=8.5, color=tcol, fontweight="bold", linespacing=1.4)

    cb = fig.colorbar(last, ax=fig.axes, fraction=0.035, pad=0.02)
    cb.set_label("热面壁温 T [K]（同色标，越亮越热）", fontsize=8)
    fig.suptitle("主流内衬材料 喉部 2D 温度场对比 · 同一冷却工况 · 同色标(越亮越热) · ✓扛得住 ~临界 ✗扛不住",
                 fontsize=10, fontweight="bold", color="#0f172a", y=0.995)
    best = rs[0]
    concl = ("结论：" + ("、".join(f"{r.material}({r.level})" for r in rs))
             + f"。最优 {best.material}（{best.verdict_zh}）。"
             + ("　全部材料温度/SF 不达标 → 须在冷却上下功夫（强化换热/TBC/膜冷却）或提热导牌号，"
                "而非单纯换材料。" if all(r.level != "ok" for r in rs) else ""))
    fig.text(0.5, 0.015, concl, ha="center", va="bottom", fontsize=8,
             color="#7c2d12", wrap=True)
    fig.subplots_adjust(left=0.06, right=0.90, top=0.70, bottom=0.15, wspace=0.10)


# ── 独立测试入口 ──
def demo():
    import warnings; warnings.simplefilter("ignore")
    res = solve_throat_channel_2d(
        t_liner_m=0.5e-3, h_channel_m=4.0e-3, w_channel_m=1.5e-3,
        pitch_m=2.5e-3, q_gas_W_m2=124e6, h_c_W_m2K=1.47e5,
        T_coolant_K=300.0, p_coolant_Pa=66e6, p_gas_Pa=18e6,
        E_Pa=100e9, nu=0.33, alpha_1perK=17e-6, k_WmK=235.0,
        sigma_y_func=lambda T: max(60e6, 200e6 - 0.12e6 * (T - 300)),
    )
    for n in res.notes:
        print(n)


if __name__ == "__main__":
    demo()
