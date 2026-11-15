"""Steady operating points of a frozen, design-point-calibrated local model.

This does not invoke the concept-design solver or infer component maps. The
existing hot-state lumped model supplies mass, energy and shaft balances. Its
eight steady states and two equivalent feed conductances are solved against
thrust and overall mixture ratio. Inventory ledger states are not unknowns.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

import numpy as np
from scipy.optimize import least_squares

from lre_workbench.domain.models import preburner_temperature_constraint
from lre_workbench.operations.transient import HotStateModel, gas_flux
from lre_workbench.runtime.tasks import checkpoint

MODEL_ID = "fixed_geometry_local_throttle_v1"
MODE = "fixed_geometry_local_throttle"
RESIDUAL_TOLERANCE = 1e-6
DOMAIN_LOW, DOMAIN_HIGH = .65, 1.35
G0 = 9.80665
LIMITATIONS = [
    "按保存的设计点标定的局部运行估算；泵曲线、涡轮通流和有效热释放均不是实测部件特性。",
    "冻结几何、喷管面积、局部泵曲线系数及燃气物性；推力和总混合比为控制目标，预燃室温度为平衡结果。",
    "调节量是两路供给等效通流系数比，作用于同种推进剂的全部支路；不等于真实阀位，也不能证明实际执行机构具有相应控制能力。",
    "主泵入口压力、推进剂馈入温度和环境压力固定；前置增压泵及贮箱供给未联立求解。",
    "冷却包含在基准馈入温度及集总阻力内，未重算壁温、换热和压损；不报告汽蚀、热安全、燃烧稳定性或结构校核通过。",
    "各容腔压力、温度、轴转速及等效通流比只允许在基准的 0.65–1.35 计算保护范围内；该范围不是试验确认的有效域。域外点不外推成性能曲线。",
    "冻结局部真空推力系数，只在喷管壅塞时使用；不计算低工况流动分离及侧载。",
    "100% 点的回算用于检查标定一致性，不能替代独立试验验证；本模型也不替代完整固定硬件稳态任务。",
]


def _positive(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name}必须为有限正数。")
    return float(value)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def normalize_request(request):
    if not isinstance(request, dict) or set(request) - {"source_job_id", "throttle_min", "throttle_max", "throttle_steps"}:
        raise ValueError("节流请求仅接受来源记录、推力比例上下限和点数。")
    low = _positive(request.get("throttle_min", .9), "最低推力比例")
    high = _positive(request.get("throttle_max", 1.), "最高推力比例")
    count = _positive(request.get("throttle_steps", 5), "扫描点数")
    if not .05 <= low < high <= 1.:
        raise ValueError("推力比例须满足 5% ≤ 最低值 < 最高值 ≤ 100%。")
    if not count.is_integer() or not 2 <= count <= 15:
        raise ValueError("扫描点数须为 2–15 的整数。")
    return {**request, "throttle_min": low, "throttle_max": high, "throttle_steps": int(count)}


class _SteadyModel(HotStateModel):
    def command(self, t, side):
        return self.conductance[side]

    def __init__(self, calculation):
        if calculation.get("inputs", {}).get("cycle_type") != "ffsc":
            raise ValueError("局部节流估算仅支持全流量补燃循环。")
        self.conductance = {"ox": 1., "fuel": 1.}
        # Inventories cancel from steady residuals. These unit scales are NOT
        # estimates of actual chamber volumes or rotor inertia.
        settings = dict(chamber_volume_m3=1., generator_volume_m3=1.,
                        inertia_ox_kg_m2=1., inertia_fuel_kg_m2=1., pump_head_slope=.25)
        super().__init__(deepcopy(calculation), settings)
        self.reference_thrust = _positive(self.summary.get("thrust_N"), "保存的实际推力")
        self.reference_mr = _positive(self.summary.get("mdot_ox_kg_s"), "液氧流量") / _positive(
            self.summary.get("mdot_fuel_kg_s"), "甲烷流量")
        self.reference = dict(thrust_N=self.reference_thrust,
            pc_Pa=self.volumes[self.main]["p0"], ambient_pressure_Pa=self.ambient,
            of_ratio=self.reference_mr, throat_area_m2=self.nozzles[0]["area"],
            expansion_ratio=self.inputs["expansion_ratio"], cycle_type="ffsc",
            propellant_pair=self.inputs["propellant_pair"],
            thrust_basis="saved_actual_thrust_at_saved_ambient")
        self.calibration = dict(pumps=deepcopy(self.pumps), liquid_branches=deepcopy(self.branches),
            turbines=deepcopy(self.turbines), nozzles=deepcopy(self.nozzles),
            gas_properties_and_heat=deepcopy(self.volumes), shafts=deepcopy(self.shafts),
            liquid_cp_JkgK=deepcopy(self.liquid_cp), pump_head_slope=.25,
            ambient_pressure_Pa=self.ambient)
        self.calibration_hash = _hash(self.calibration)
        residual, data, _ = self.balance(np.zeros(10), 1.)
        if np.max(np.abs(residual)) > RESIDUAL_TOLERANCE:
            raise ValueError("保存的实际设计点不能闭合本地标定模型，不能作为 100% 节流基准。")
        self.nominal_residual = float(np.max(np.abs(residual)))
        self.nominal_thrust = data["thrust"]

    def balance(self, x, target):
        checkpoint()
        ratios = np.exp(x)
        y = self.initial.copy()
        for i in range(self.nv):
            y[2*i] = ratios[2*i] / ratios[2*i+1]  # m/m0 = (p/p0)/(T/T0)
            y[2*i+1] = ratios[2*i]              # U/U0 = p/p0
        for j in range(self.ns):
            y[2*self.nv+j] = ratios[2*self.nv+j] ** 2
        self.conductance = {"ox": float(ratios[-2]), "fuel": float(ratios[-1])}
        derivative, data = self.evaluate(0, y)
        residual = []
        for i, v in enumerate(self.volumes):
            residual.extend((derivative[2*i]*v["m0"]/v["mdot0"],
                             derivative[2*i+1]*v["U0"]/(v["mdot0"]*v["cp"]*v["T0"])))
        for j, s in enumerate(self.shafts):
            residual.append(derivative[2*self.nv+j]*s["K0"]/self.pumps[s["side"]]["power0"])
        residual.extend((data["thrust"]/self.reference_thrust-target,
                         data["mdot"]["ox"]/max(data["mdot"]["fuel"], 1e-12)/self.reference_mr-1))
        return np.asarray(residual), data, derivative

    def domain(self, x, data):
        names = [f"{v['name']}_{field}" for v in self.volumes for field in ("pressure", "temperature")]
        names += ["shaft_speed_ox", "shaft_speed_fuel", "ox_feed_conductance", "fuel_feed_conductance"]
        violations = [dict(quantity=name, ratio=float(value), lower=DOMAIN_LOW, upper=DOMAIN_HIGH)
                      for name, value in zip(names, np.exp(x))
                      if value < DOMAIN_LOW-1e-9 or value > DOMAIN_HIGH+1e-9]
        for n in self.nozzles:
            gamma = self.volumes[n["cv"]]["gamma"]
            if self.ambient / data["gas"][n["cv"]]["p"] >= (2/(gamma+1))**(gamma/(gamma-1)):
                violations.append(dict(quantity="nozzle_not_choked"))
        return dict(status="outside" if violations else "within", violations=violations,
                    basis="computational_guard_not_validated_envelope")


def _description(model):
    return dict(id=MODEL_ID, title="基准标定局部节流估算", experiment_validated=False,
        full_hardware_model=False, nominal_max_scaled_residual=model.nominal_residual,
        calibration_sha256=model.calibration_hash, calibration=deepcopy(model.calibration),
        unknown_count=10, balance_equation_count=8, control_target_count=2,
        pump_head_slope=.25, state_ratio_guard=[DOMAIN_LOW, DOMAIN_HIGH],
        equivalent_conductance_ratio_guard=[DOMAIN_LOW, DOMAIN_HIGH],
        targets=["thrust_ratio", "saved_overall_mixture_ratio"],
        outputs_not_targets=["ox_preburner_temperature", "fuel_preburner_temperature"],
        control_basis="同种推进剂全部支路的集总通流系数比例，不是实际阀门行程",
        state_basis="基准记录的集总状态标定；不将历史节点压力/温度重新声明为已确认的总态接口",
        residual_basis="容腔质量/基准流量、能量/基准焓流、轴功率/基准泵功率；无容积或惯量依赖",
        thermal_model="冻结物性和局部等效热释放斜率，未求解变工况化学平衡或冷却壁面",
        equations=["Σṁin − Σṁout = 0", "Σṁhin − Σṁhout + Qeffective − Pturbine = 0",
                   "ηshaft Pturbine − Ppump = 0", "F/Fref = command; ṁox/ṁfuel = MRref",
                   "Δp/Δp0 = 1.25(n/n0)² − 0.25(ṁ/ṁ0)²", "ṁbranch = C0 kfeed √Δp"])


def configuration(calculation):
    try:
        model = _SteadyModel(calculation)
        return dict(ready=True, issues=[], readiness=dict(can_run=True),
            baseline=model.reference, model=_description(model), limitations=deepcopy(LIMITATIONS),
            defaults=dict(throttle_min=.9, throttle_max=1., throttle_steps=5))
    except (ValueError, KeyError, TypeError, ZeroDivisionError, OverflowError) as exc:
        return dict(ready=False, issues=[str(exc)], readiness=dict(can_run=False),
            limitations=deepcopy(LIMITATIONS), defaults=dict(throttle_min=.9, throttle_max=1., throttle_steps=5))


def _summary(model, x, data):
    main = data["gas"][model.main]
    mdot = sum(data["mdot"].values())
    vacuum = data["thrust"] + model.ambient*model.nozzles[0]["exit_area"]
    summary = dict(thrust_N=data["thrust"], thrust_vac_N=vacuum, Isp_s=data["thrust"]/(mdot*G0),
        Isp_vac_s=vacuum/(mdot*G0), mdot_total_kg_s=mdot,
        mdot_ox_kg_s=data["mdot"]["ox"], mdot_fuel_kg_s=data["mdot"]["fuel"],
        of_ratio=data["mdot"]["ox"]/data["mdot"]["fuel"],
        pc_Pa=main["p"], chamber_pressure_Pa=main["p"], chamber_temperature_K=main["T"])
    for side, idx in (("ox", model.oxpb), ("fuel", model.fpb)):
        summary.update({f"shaft_speed_{side}_rpm":data["speed"][side]*model.pumps[side]["omega0"]*30/math.pi,
            f"{side}_pump_power_W":data["pump_power"][side],
            f"{side}_turbine_power_W":data["turbine_power"][side],
            f"{side}_pump_outlet_pressure_Pa":data["pump_pressure"][side],
            f"{side}_effective_area_ratio":model.conductance[side],
            f"{side}_preburner_T_combustion_K":data["gas"][idx]["T"],
            f"{side}_preburner_pressure_Pa":data["gas"][idx]["p"]})
    # Preserve all four branch flows and the resulting PB mixture ratios.
    for b in model.branches:
        flow = b["C"]*model.conductance[b["side"]]*math.sqrt(max(0.,data["pump_pressure"][b["side"]]-data["gas"][b["dest"]]["p"]))
        destination = "ox" if b["dest"] == model.oxpb else "fuel"
        summary[f"mdot_{b['side']}_to_{destination}_preburner_kg_s"] = flow
    for side in ("ox", "fuel"):
        summary[f"{side}_preburner_mr"] = summary[f"mdot_ox_to_{side}_preburner_kg_s"] / summary[f"mdot_fuel_to_{side}_preburner_kg_s"]
    return {key: float(value) for key, value in summary.items()}


def _checks(model, summary):
    checks = []
    for side in ("ox", "fuel"):
        constraint = preburner_temperature_constraint(model.inputs, side)
        limit = constraint["limit_K"]
        if isinstance(limit, (int, float)) and not isinstance(limit, bool) and math.isfinite(limit) and limit > 0:
            actual = summary[f"{side}_preburner_T_combustion_K"]
            checks.append(dict(key=f"{side}_preburner_temperature", label=f"{'富氧' if side == 'ox' else '富燃'}预燃室温度上限",
                actual=actual, limit=limit, unit="K", criterion="maximum", status="pass" if actual <= limit else "fail",
                limit_status=constraint["status"], basis=constraint["basis"]))
        else:
            checks.append(dict(key=f"{side}_preburner_temperature", label=f"{'富氧' if side == 'ox' else '富燃'}预燃室温度上限",
                actual=summary[f"{side}_preburner_T_combustion_K"], limit=None, unit="K", criterion="maximum", status="not_evaluated",
                limit_status=constraint["status"], basis=constraint["basis"]))
    return checks


def calculate(calculation, request, progress=None):
    request = normalize_request(request)
    model = _SteadyModel(calculation)
    result = dict(schema_version=1, analysis_type="operating_throttle", analysis_mode=MODE,
        model=_description(model), baseline=deepcopy(model.reference), request=deepcopy(request),
        limitations=deepcopy(LIMITATIONS), points=[], complete=False)
    previous = np.zeros(10)

    def publish(phase, ratio=None):
        points = result["points"]
        result["counts"] = dict(total=request["throttle_steps"], completed=len(points),
            converged=sum(p["converged"] for p in points), passed=0,
            review=sum(p["status"] == "review" for p in points),
            failed=sum(p["status"] == "failed" for p in points),
            out_of_domain=sum(p["status"] == "out_of_domain" for p in points))
        if progress:
            progress(dict(iteration=len(points), completed=len(points), maximum=request["throttle_steps"],
                current_ratio=ratio, phase=phase), deepcopy(result))

    publish("冻结基准几何与局部模型系数")
    # Descending continuation from the calibrated design point; every requested
    # point remains a real independent balance solve, never an interpolation.
    for ratio in np.linspace(request["throttle_max"], request["throttle_min"], request["throttle_steps"]):
        checkpoint()
        ratio = float(ratio)
        point = dict(index=len(result["points"])+1, throttle_ratio=ratio, target_thrust_ratio=ratio,
            thrust_target_N=ratio*model.reference_thrust, actual_thrust_ratio=None,
            pc_Pa=None, converged=False, status="failed", error="", summary={}, warnings=[],
            geometry=dict(status="pass", scope="frozen_nozzle_area", throat_area_m2=model.reference["throat_area_m2"],
                reference_area_m2=model.reference["throat_area_m2"], relative_error=0., tolerance=0.),
            hardware=dict(status="pass", scope="calibration_identity_only", fingerprint=model.calibration_hash),
            model_domain=dict(status="not_evaluated", violations=[]))
        # Constant Cf gives a necessary chamber-pressure ratio. Reject impossible
        # local requests BEFORE extrapolating any temperature or performance.
        pa_area = model.ambient*model.nozzles[0]["exit_area"]
        required_pc = (ratio*model.reference_thrust+pa_area)/(model.reference_thrust+pa_area)
        if required_pc < DOMAIN_LOW-1e-9:
            point.update(status="out_of_domain", error="目标推力对应的室压低于局部模型计算保护范围，未外推性能。",
                model_domain=dict(status="outside", violations=[dict(quantity="main_pressure", ratio=required_pc,
                    lower=DOMAIN_LOW, upper=DOMAIN_HIGH)], basis="computational_guard_not_validated_envelope"))
        else:
            try:
                # Slightly wider numerical bounds permit classification of an
                # otherwise converged point as domain-exceeded, not a false pass.
                solved = least_squares(lambda x:model.balance(x,ratio)[0], previous,
                    bounds=(np.full(10, math.log(.35)), np.full(10, math.log(1.6))),
                    xtol=1e-10, ftol=1e-10, gtol=1e-10, max_nfev=180)
                residual, data, derivative = model.balance(solved.x,ratio)
                maximum = float(np.max(np.abs(residual)))
                domain = model.domain(solved.x,data)
                point["convergence"] = dict(status="pass" if maximum <= RESIDUAL_TOLERANCE else "fail",
                    max_scaled_residual=maximum, tolerance=RESIDUAL_TOLERANCE,
                    residuals=[float(v) for v in residual], nfev=int(solved.nfev),
                    method="bounded_least_squares_log_states")
                point["model_domain"] = domain
                if maximum > RESIDUAL_TOLERANCE:
                    point["error"] = "流量、能量、轴功率或控制目标未闭合；不作为有效性能点。"
                elif domain["status"] != "within":
                    point.update(status="out_of_domain", error="收敛候选状态越出局部计算保护范围；不作为有效性能点。")
                else:
                    previous = solved.x.copy()
                    summary = _summary(model, solved.x, data)
                    checks = _checks(model, summary)
                    mass = {v["name"]:float(derivative[2*i]*v["m0"]) for i,v in enumerate(model.volumes)}
                    energy = {v["name"]:float(derivative[2*i+1]*v["U0"]) for i,v in enumerate(model.volumes)}
                    power = {s["side"]:float(data["shaft_acceleration"][s["side"]]) for s in model.shafts}
                    point.update(converged=True, status="review", summary=summary,
                        pc_Pa=summary["chamber_pressure_Pa"], actual_thrust_ratio=summary["thrust_N"]/model.reference_thrust,
                        assessment=dict(status="review", numerical=dict(status="pass"), checks=checks),
                        steady_closure=dict(status="pass", scope="local_model_balances_only",
                            mass_residual_kg_s=mass, energy_residual_W=energy, shaft_residual_W=power,
                            external_mass_residual_kg_s=float(data["external_mass_rate"]),
                            external_energy_residual_W=float(data["external_energy_rate"])))
                    if any(c["status"] == "fail" for c in checks):
                        point["warnings"].append("预测预燃室温度超过保存的上限；该估算点不满足温度约束。")
            except (ValueError, FloatingPointError, OverflowError, ZeroDivisionError) as exc:
                point["error"] = "局部平衡求解失败："+str(exc)
        result["points"].append(point)
        publish(f"已计算 {len(result['points'])}/{request['throttle_steps']} 个目标点", ratio)
    result["complete"] = True
    publish("局部节流估算完成")
    json.dumps(result, allow_nan=False)
    return result
