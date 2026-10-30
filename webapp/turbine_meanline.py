"""Opt-in constant-radius Euler velocity triangles, not an aerodynamic solver.

Positive tangential velocity follows rotor rotation. Angles are measured from
the positive axial direction. R is rotor static enthalpy drop / stage work;
identifying it with stage reaction assumes a repeating stage (c0 == c2).
No pressure distribution, choking, losses or metal angles are inferred.
"""
import json
import math


def _number(value, label, positive=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}未记录有效数值")
    if positive and value <= 0:
        raise ValueError(f"{label}须为正数")
    return float(value)


def meanline_view(outputs, saved_inputs, side):
    inputs, o = saved_inputs or {}, outputs or {}
    mode = inputs.get("turbine_meanline_mode", "disabled")
    base = {"method": "constant_radius_constant_axial_euler_v1", "mode": mode,
            "status": "disabled", "rows": [], "checks": [],
            "notes": ["采用各级相同中径和轴向速度；反动度与负荷分配是输入约束。",
                      "角度自轴向量起，正切向与转动同向；所列为流动角，不是叶片金属角。",
                      "R=动叶静焓降/级欧拉比功；解释为级反动度还要求重复级 c0=c2。",
                      "未求逐级压力、静温、马赫数、壅塞、损失、入射/落后角或实际性能；不可用来反推 Global setup 总静态。",
                      "该初选独立于概念环形流道；不会回写循环结果或用于验证流道尺寸。"]}
    if mode == "disabled":
        base["reason"] = "未启用均径初选；总体设计点交接不要求先求速度三角形。"
        return base
    try:
        if mode not in ("equal_work", "custom_work"):
            raise ValueError("均径初选模式未识别")
        side = "fuel" if side == "shared" else side
        if side not in ("ox", "fuel"):
            raise ValueError("轴系未识别")
        n = _number(o.get("n_stages_recommended"), "已保存级数")
        if int(n) != n or n > 6:
            raise ValueError("级数须为 1–6 的整数")
        n = int(n)
        u = _number(o.get("U_mean_actual_ms"), "平均圆周速度")
        diameter = _number(o.get("D_mean_m"), "保存中径")
        rpm = _number(o.get("shaft_speed_rpm"), "转速")
        if not math.isclose(u, math.pi * diameter * rpm / 60, rel_tol=2e-7):
            raise ValueError("保存中径、转速与圆周速度冲突")
        work = _number(o.get("actual_drop_Jkg"), "实际总焓降")
        phi = _number(inputs.get(f"turb_{side}_meanline_flow_coefficient"), "均径轴向流量系数")
        reaction = _number(inputs.get(f"turb_{side}_reaction_degree"), "反动度目标", False)
        if not 0 <= reaction <= 1:
            raise ValueError("本初选的反动度目标须在 0–1 内")
        if mode == "equal_work":
            fractions = [1 / n] * n
        else:
            fractions = json.loads(inputs.get(f"turb_{side}_stage_work_fractions", "null"))
            if not isinstance(fractions, list) or len(fractions) != n:
                raise ValueError("自定义比功比例的数量必须等于本记录实际级数")
            fractions = [_number(x, "各级比功比例") for x in fractions]
            if not math.isclose(sum(fractions), 1, rel_tol=0, abs_tol=1e-8):
                raise ValueError("各级比功比例之和须为 1；不自动归一化")
        ca = phi * u
        for i, fraction in enumerate(fractions):
            dh = work * fraction
            cu1 = u * (1 - reaction) + dh / (2 * u)
            cu2 = u * (1 - reaction) - dh / (2 * u)
            wu1, wu2 = cu1 - u, cu2 - u
            c1, c2 = math.hypot(ca, cu1), math.hypot(ca, cu2)
            rotor_drop = dh - (c1 * c1 - c2 * c2) / 2
            euler = u * (cu1 - cu2)
            base["rows"].append({"stage": i + 1, "work_fraction": fraction,
                "work_j_kg": dh, "loading_coefficient": dh / u**2,
                "u_m_s": u, "ca_m_s": ca, "cu1_m_s": cu1, "cu2_m_s": cu2,
                "wu1_m_s": wu1, "wu2_m_s": wu2, "c1_m_s": c1, "c2_m_s": c2,
                "w1_m_s": math.hypot(ca, wu1), "w2_m_s": math.hypot(ca, wu2),
                "alpha1_deg": math.degrees(math.atan2(cu1, ca)),
                "alpha2_deg": math.degrees(math.atan2(cu2, ca)),
                "beta1_deg": math.degrees(math.atan2(wu1, ca)),
                "beta2_deg": math.degrees(math.atan2(wu2, ca)),
                "rotor_static_drop_j_kg": rotor_drop,
                "reaction_target": reaction, "reaction_check": rotor_drop / dh,
                "euler_residual_j_kg": euler - dh})
        total = sum(row["work_j_kg"] for row in base["rows"])
        base.update(status="kinematic_only", reason="给定约束下的均径运动学解；尚未完成逐级气动设计。",
                    total_work_j_kg=work, allocated_work_j_kg=total,
                    flow_coefficient=phi, reaction_target=reaction)
        base["checks"] = [{"label": "各级比功之和", "status": "pass",
                           "residual_j_kg": total - work},
                          {"label": "欧拉功与反动度代数闭合", "status": "pass"}]
        if any(not math.isclose(row["reaction_check"], reaction, abs_tol=1e-8)
               or abs(row["euler_residual_j_kg"]) > 1e-7 * work for row in base["rows"]):
            raise ValueError("均径三角形闭合误差超限")
        json.dumps(base, allow_nan=False)
    except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
        base.update(status="unavailable", reason=str(exc), rows=[], checks=[])
    return base
