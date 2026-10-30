"""Close the unique coolant/hot-gas solution inside one cycle evaluation."""
from __future__ import annotations
import copy
import math
from task_control import checkpoint


THERMAL_FEEDBACK_SOURCE = "regen_quasi1d_ph_v1"
COUPLING_CRITERIA = (
    ("pressure_residual_Pa", .5, "Pa"),
    ("hot_side_temperature_residual_K", .001, "K"),
    ("geometry_relative_residual", 2e-8, "1"),
    ("heat_relative_residual", 2e-8, "1"),
    ("hot_side_relative_residual", 5e-7, "1"),
    ("cell_heat_relative_residual", 5e-7, "1"),
)


def coupling_checks(evidence):
    """The same six physical criteria serve production and convergence reports."""
    return [dict(name=name, residual=evidence.get(name), tolerance=limit, unit=unit)
            for name, limit, unit in COUPLING_CRITERIA]


def coupling_passed(evidence):
    return all(isinstance(c["residual"], (int, float))
               and not isinstance(c["residual"], bool)
               and math.isfinite(c["residual"]) and 0 <= c["residual"] <= c["tolerance"]
               for c in coupling_checks(evidence))


def current_thermal_evidence(model, result):
    """Accept only evidence emitted by this core solve's physical evaluator.

    The private object token is intentionally absent from dataclass/JSON output.
    It proves freshness only; the six physical residuals are still checked.
    """
    token = getattr(model, "_thermal_feedback_epoch", None)
    proof = getattr(result, "_current_regen_thermal_evidence", None)
    if token is None or proof is None or proof[0] is not token:
        return None
    evidence = proof[1]
    return copy.deepcopy(evidence) if coupling_passed(evidence) else None


def fixed_contour_inputs(baseline):
    """Freeze nozzle definition from recorded hardware, never later edits."""
    recorded = dict((baseline or {}).get("contour_inputs") or {})
    if "expansion_ratio" in (baseline or {}):
        recorded.setdefault("expansion_ratio", baseline["expansion_ratio"])
    geometry = ((baseline or {}).get("cooling_hardware") or {}).get("geometry") or {}
    if geometry:
        recorded.update(nozzle_contour_method=geometry.get("nozzle_contour_method", "legacy_log_v1"),
            nozzle_initial_angle_deg=geometry.get("initial_expansion_angle_deg", 35.),
            nozzle_exit_angle_deg=geometry.get("exit_angle_deg", 10.),
            nozzle_bell_fraction=geometry["bell_fraction"],
            chamber_conv_half_angle_deg=geometry["conv_half_angle_deg"],
            expansion_ratio=geometry["expansion_ratio"])
    recorded.setdefault("nozzle_contour_method", "legacy_log_v1")
    allowed = ("nozzle_contour_method", "nozzle_initial_angle_deg", "nozzle_exit_angle_deg",
               "nozzle_bell_fraction", "chamber_conv_half_angle_deg", "expansion_ratio")
    return {key: recorded[key] for key in allowed if key in recorded}


def cooling_arguments(model, material=None):
    i=model.inputs
    trial=getattr(model,"_hydraulic_trial",None) or {}
    return dict(throat_area_m2=getattr(model,"_fixed_cooling_throat_area",None) or trial.get("throat_area_m2"),
        hot_side_profile=trial.get("hot_side_profile"), expansion_ratio=i.expansion_ratio,
        bell_fraction=i.nozzle_bell_fraction, channel_table=i.cooling_channel_table_json or None,
        nozzle_contour_method=getattr(i,"nozzle_contour_method","legacy_log_v1"),
        nozzle_initial_angle_deg=getattr(i,"nozzle_initial_angle_deg",35.),
        nozzle_exit_angle_deg=getattr(i,"nozzle_exit_angle_deg",10.),
        n_points=int(getattr(i,"cooling_grid_points",120)),
        sizing_reference=getattr(i,"cooling_sizing_reference","legacy_inlet_density_throat_width"),
        wall_thermal_dataset_id=i.wall_thermal_dataset_id,
        wall_manufacturing_state=i.wall_manufacturing_state,
        wall_material_zones=i.wall_material_zones_json,
        remap_hot_side_trial=True,
        gas_emissivity=i.cooling_gas_emissivity,wall_temp_limit_K=i.cooling_wall_temp_limit_K,
        wall_temp_limit_status=getattr(i,"cooling_wall_temp_limit_status","provisional"),
        wall_temp_limit_basis=getattr(i,"cooling_wall_temp_limit_basis",""),
        film_fraction=i.film_cooling_fraction)


def close_regen_cycle(model,evaluator,*x):
    from pump import fluid_density
    from cooling_marching import CoolantMarchError
    physical=bool(model.inputs.cooling_bartz_mode)
    if not physical and not getattr(model,"_feed_hardware",None):
        return evaluator(*x)
    previous=getattr(model,"_hydraulic_trial",None)
    model._hydraulic_trial={}
    seed=getattr(model,"_regen_seed",None)
    pc=float(getattr(model.inputs,"pc",0.) or 0.)
    # Reuse only a nearby fully closed circuit. All acceptance checks still run;
    # this is an initial state, never a cached physical answer.
    if seed and x and pc>0 and seed["pc"]>0 and seed["x0"]>0:
        if max(abs(math.log(pc/seed["pc"])),abs(math.log(float(x[0])/seed["x0"])))<.22:
            model._hydraulic_trial=copy.deepcopy(seed["trial"])
    history=[]
    rejected=[]
    last_valid_dp=None
    pressure_seed_attempts=0
    pressure_relaxation=1.
    last_pressure_residual=None
    old_q=None
    old_cell_heat=None
    try:
        for iteration in range(40):
            checkpoint()
            try:
                result=evaluator(*x)
            except CoolantMarchError as exc:
                if not physical or pressure_seed_attempts>=8:
                    raise
                tried=model._hydraulic_trial.get("cooling_dp",model.inputs.cooling_dp_pa)
                proposed=(.5*(last_valid_dp+tried) if last_valid_dp is not None and last_valid_dp>tried
                          else max(2.*tried,1e6))
                rejected.append(dict(iteration=iteration+1,reason=str(exc),
                    trial_pressure_drop_Pa=tried,next_pressure_drop_Pa=proposed,
                    accepted_as_physical_state=False))
                model._hydraulic_trial["cooling_dp"]=proposed
                pressure_seed_attempts+=1
                continue
            comp=result.components["cooling_channel"]
            cooled=result.states["fuel_after_cooling"]
            pump=result.states["fuel_after_pump"]
            trial=model._hydraulic_trial
            last_valid_dp=trial.get("cooling_dp",model.inputs.cooling_dp_pa)
            dp=pump.pressure-cooled.pressure
            signed_pressure_residual=dp-last_valid_dp
            if (last_pressure_residual is not None
                and signed_pressure_residual*last_pressure_residual<0
                and abs(signed_pressure_residual)>.5*abs(last_pressure_residual)):
                pressure_relaxation=max(.1,.5*pressure_relaxation)
            last_pressure_residual=signed_pressure_residual
            closure=getattr(result,"steady_closure",None) or {}
            feed=closure.get("feed_network",{})
            pressure_error=feed.get("hydraulic_iteration_error_Pa",abs(dp-trial.get("cooling_dp",model.inputs.cooling_dp_pa)))
            next_trial=dict(cooling_dp=last_valid_dp+pressure_relaxation*signed_pressure_residual,
                densities={"fuel":fluid_density(model.propellant.fuel_species if hasattr(model,"propellant") else "CH4",cooled.temperature,cooled.pressure),"ox":result.states["lox_after_pump"].density})
            area_error=hot_error=heat_error=vector_error=cell_heat_error=0.
            fixed_area_error=None
            if physical:
                profile=comp.profile
                nozzle=result.components["nozzle"]
                hot=nozzle.profile["cells"]
                # Hot output is in gas order; the coolant mesh may be counterflow.
                hot=sorted(hot,key=lambda c:c["x_m"],reverse=profile["flow_direction"]=="counterflow")
                used=[c["hot_side"] for c in profile["cells"] if c["kind"]=="channel"]
                if len(hot)!=len(used) or not hot:
                    raise ValueError("热侧/冷却侧反馈单元数不一致。")
                hot_error=max(abs(a["T_aw_K"]-b["T_aw_K"]) for a,b in zip(hot,used))
                keys=("T_static_K","T_aw_K","T0_K","p_Pa","M","cp_JkgK","mu_Pas","Pr","gamma")
                vector_error=max(abs(a[k]-b[k])/max(abs(a[k]),1e-12) for a,b in zip(hot,used) for k in keys)
                cell_heat=[c["Q_W"] for c in profile["cells"] if c["kind"]=="channel"]
                cell_heat_error=(max(abs(a-b)/max(abs(a),1.) for a,b in zip(cell_heat,old_cell_heat))
                    if old_cell_heat is not None and len(cell_heat) == len(old_cell_heat) else 1.)
                old_cell_heat=cell_heat
                area_error=abs(nozzle.outputs["At_m2"]/comp.outputs["A_throat_m2"]-1)
                if getattr(model,"_fixed_cooling_throat_area",None):
                    # Outer fixed-throat solve owns this equality; geometry never resizes here.
                    fixed_area_error=area_error
                    area_error=0.
                q=comp.outputs["heat_load_W"]
                heat_error=abs(q-old_q)/max(abs(q),1.) if old_q is not None else 1.
                old_q=q
                next_trial.update(throat_area_m2=nozzle.outputs["At_m2"],hot_side_profile=hot)
            history.append(dict(iteration=len(history)+1,attempt=iteration+1,pressure_residual_Pa=pressure_error,
                pressure_relaxation=pressure_relaxation,
                hot_side_temperature_residual_K=hot_error,geometry_relative_residual=area_error,
                heat_relative_residual=heat_error,hot_side_relative_residual=vector_error,
                cell_heat_relative_residual=cell_heat_error))
            passed=coupling_passed(history[-1])
            feed["hydraulic_iterations"]=len(history)
            if passed:
                if physical:
                    comp.profile["hot_side_coupling"]=dict(status="pass",iterations=len(history),attempts=iteration+1,
                        rejected_pressure_trials=rejected,
                        fixed_hardware_area_relative_residual=fixed_area_error,
                        fixed_hardware_area_owner="outer_throttle_solve" if fixed_area_error is not None else None,
                        pressure_tolerance_Pa=.5,temperature_tolerance_K=.001,
                        geometry_relative_tolerance=2e-8,heat_relative_tolerance=2e-8,
                        hot_side_relative_tolerance=5e-7,cell_heat_relative_tolerance=5e-7,
                        **history[-1],history=history)
                    comp.profile["hot_side_profile"]=copy.deepcopy(nozzle.profile)
                    epoch = getattr(model, "_thermal_feedback_epoch", None)
                    if epoch is not None:
                        result._current_regen_thermal_evidence = (
                            epoch, copy.deepcopy(comp.profile["hot_side_coupling"]))
                    if x and pc>0:
                        model._regen_seed=dict(pc=pc,x0=float(x[0]),trial=copy.deepcopy(next_trial))
                return result
            model._hydraulic_trial=next_trial
        raise ValueError(f"冷却/热气/供给内环未闭合：{history[-1]}")
    finally:
        model._hydraulic_trial=previous
