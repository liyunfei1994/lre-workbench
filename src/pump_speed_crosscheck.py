"""
pump_speed_crosscheck.py — 涡轮泵"转速 / 比转速 / 汽蚀 / 叶尖速度"自动对拍校验

定位（A 版，升级自原 _tmp_calc.py 手算草稿）
────────────────────────────────────────────────────────────────────
这是一个**会自我校验的回归守门脚本**：它 import 真正的求解器，跑当前设计点
（EngineInputs 默认值）取得 pump.py 实算出的泵参数，再把 pump.py 报出的每个
**派生量**（比转速 nq、吸入比转速 Nss、叶尖速度 u_tip、叶轮直径 D_imp、Thoma
空化数 σ）与本脚本**按教材定义独立复算**的值逐项相减——一致打 PASS、超过阈值
打 FAIL。

它解决了草稿版的两个毛病：
  · 数字不再过时：原始量（ṁ/ρ/Δp/N/NPSHa）直接取自当前求解结果，跟着设计点走；
  · 真正"守门"：谁把 pump.py 里的比转速/汽蚀/叶尖速度公式改错了，
    跑一下 `python pump_speed_crosscheck.py` 立刻红灯。

把本脚本里的 hand_calc() 当作 pump.py 应当实现的"规格(spec)"：
两者背离即说明实现与定义不符。

用法
────
    python pump_speed_crosscheck.py
退出码：全部 PASS → 0；任一 FAIL → 1（可挂 pre-commit / CI）。

权威实现在 pump.py 的 Pump._specific_speed() 与 evaluate()；公式有改动时
两边都要同步更新（这正是本脚本要抓的"两边不同步"）。
"""

import math
import sys
import warnings

g = 9.80665                 # 重力加速度 [m/s²]
_GPM = 15850.3              # m³/s → US gpm（与 pump.py 同常数）
_FT = 3.280839895          # m → ft（与 pump.py 同常数）


def hand_calc(Q, H, n_stages, N, omega, npsh_m, psi):
    """按教材定义独立复算各派生量（这套公式 = pump.py 应实现的"规格"）。

    参数
    ────
    Q        : 体积流量 [m³/s]（= ṁ/ρ_in）
    H        : 总扬程 [m]（= Δp/(ρ_in·g)）
    n_stages : 级数（比转速/叶尖速度按"每级扬程"算）
    N        : 转速 [rpm]
    omega    : 角速度 [rad/s]（= N·2π/60）
    npsh_m   : 可用净正吸入压头 [m]
    psi      : 压头系数 ψ = g·H1/u_tip²
    """
    H1 = H / max(n_stages, 1)                                 # 每级扬程 [m]
    out = {
        # 比转速 nq(EU) = N[rpm]·√Q / H1^0.75
        "specific_speed_Ns":             N * math.sqrt(Q) / H1 ** 0.75,
        # 吸入比转速（欧制）= N·√Q / NPSHa[m]^0.75
        "suction_specific_speed_nss_eu": N * math.sqrt(Q) / npsh_m ** 0.75,
        # 吸入比转速（美制）= N·√Q[gpm] / NPSHa[ft]^0.75（对标 30000/45000 判据）
        "suction_specific_speed_Nss":    N * math.sqrt(Q * _GPM) / (npsh_m * _FT) ** 0.75,
        # 叶尖速度 u_tip = √(g·H1/ψ)
        "u_tip_est_ms":                  math.sqrt(g * H1 / psi),
        # 叶轮外径 D = 2·u_tip/ω
        "d_impeller_est_m":              2 * math.sqrt(g * H1 / psi) / max(omega, 1e-9),
        # Thoma 空化数 σ = NPSHa / H（总扬程口径，与 pump.py 一致）
        "thoma_cavitation_number_sigma": npsh_m / max(H, 1e-9),
        # 每级扬程（一致性检查）
        "head_m_per_stage":              H1,
    }
    return out


_ROWS = [
    ("比转速 nq(EU)",          "specific_speed_Ns"),
    ("吸入比转速 nss(EU)",     "suction_specific_speed_nss_eu"),
    ("吸入比转速 Nss(US)",     "suction_specific_speed_Nss"),
    ("叶尖速度 u_tip [m/s]",   "u_tip_est_ms"),
    ("叶轮直径 D_imp [m]",     "d_impeller_est_m"),
    ("Thoma 空化数 σ",        "thoma_cavitation_number_sigma"),
    ("每级扬程 H1 [m]",        "head_m_per_stage"),
]


def crosscheck_pump(label, o, rtol=1e-4):
    """对一台泵：取 pump.py 输出的原始量 → 独立复算派生量 → 逐项对比。

    o    : 该泵 ComponentResult.outputs 字典
    rtol : 相对容差（同一公式应吻合到机器精度，1e-4 给足余量）
    返回 : 该泵是否全部 PASS
    """
    Q   = o["volume_flow_m3s"]
    H   = o["head_m"]
    n   = int(round(o["n_stages"]))
    N   = o["nr_rpm"]
    om  = o["nr_rad_s"]
    npm = o["npsh_available_m"]
    psi = o["psi_head_coefficient"]

    print(f"\n==== {label} ====")
    print(f"  原始量(取自 pump.py 实算)：Q={Q*1000:.1f} L/s  H={H:.0f} m "
          f"({n} 级 → H1={H/max(n,1):.0f} m)  N={N:.0f} rpm  "
          f"ω={om:.0f} rad/s  NPSHa={npm:.1f} m  ψ={psi:.2f}")

    if npm <= 0.0:
        print("  ⚠ NPSHa≤0（该泵未给汽蚀输入），跳过 Nss/σ 对比。")
    hand = hand_calc(Q, H, n, N, om, npm, psi)

    ok_all = True
    for name, key in _ROWS:
        a = float(o.get(key, float("nan")))      # pump.py 报出值
        b = hand[key]                            # 教材手算值
        if npm <= 0.0 and key in (
                "suction_specific_speed_nss_eu", "suction_specific_speed_Nss",
                "thoma_cavitation_number_sigma"):
            continue
        rel = abs(a - b) / max(abs(b), 1e-30)
        ok = rel <= rtol
        ok_all = ok_all and ok
        print(f"    {name:<22} pump.py={a:< 13.5g} 手算={b:< 13.5g} "
              f"rel={rel:.1e}  {'PASS ✓' if ok else 'FAIL ❌'}")

    # 额外一致性自检：H1·n == H、Q == ṁ/ρ（若 rho/mdot 可得）
    if abs(o["head_m_per_stage"] * n - H) / max(H, 1e-9) > rtol:
        print("    ⚠ 一致性: head_m_per_stage × n_stages ≠ head_m")
        ok_all = False
    return ok_all


def main():
    # 求解期间 thermo 在低 MR 试探点会抛 RuntimeWarning（不影响最终解），静音以保表格干净
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    from core import EngineInputs
    from cycle import FFSCCycleModel

    print("跑当前设计点求解（EngineInputs 默认，对标 Raptor 3）取真实泵参数…（约 10–15 s）")
    res = FFSCCycleModel(EngineInputs()).evaluate()
    comps = res.components

    ok = True
    ok = crosscheck_pump("液氧泵 LOX", comps["ox_pump"].outputs) and ok
    ok = crosscheck_pump("甲烷泵 CH4", comps["fuel_pump"].outputs) and ok

    print("\n" + ("✅ 全部 PASS：pump.py 实现与教材手算定义一致。"
                  if ok else
                  "❌ 有 FAIL：pump.py 的派生量与教材手算不符，请检查 Pump._specific_speed/evaluate。"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
