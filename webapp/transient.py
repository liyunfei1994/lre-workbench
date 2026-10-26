"""Versioned, immutable-source boundary for local hot-state transient studies."""
from copy import deepcopy
import math
import json

from webapp.solver import plain, science_environment
from webapp.validation import fingerprint
from convergence import numerical_convergence


LIMITATIONS = [
    '研究级零维热态小扰动模型：积分容腔质量、热内能和轴系动能；不是稳态节流曲线插值。',
    '从已收敛且系统约束通过的稳态记录初始化；仅适用于相对有效阀口面积 90%–110% 的局部扰动。',
    '泵扬程斜率、转动惯量和预燃室/排气容积属于显式可编辑的研究假设；尚无真实部件特性图或试验标定。',
    '燃气采用名义点冻结的 cp、R 和组分；等效热输入按名义能量闭合拟合。液体馈入燃烧器按名义限制反应物流量缩放，FFSC 主室后燃按气体总入流缩放；不求解化学反应速率或混合延迟。',
    '液路为准稳态有效阻力，阀口命令是有效流通面积比例，不是推力指令或真实阀门行程；未包含液柱惯性、汽蚀、两相流及执行器动态。',
    '冷却仅包含在名义供给温度和有效液路阻力内，不包含壁面蓄热、冷却剂热惯性或热浸。',
    'GG 单轴、GG 双轴和 FFSC 双轴分别建立轴系与容腔连接；主喷管及 GG 排气喷管有效面积固定。',
    '不适用于冷态启动、点火、关机熄火、再启动、深度节流、燃烧稳定性或水击；局部状态越界时终止并保留此前时间点。',
    '守恒通过表示离散模型账目闭合，不能替代独立试验验证；冻结组分热模型也不能用于元素守恒或排放预测。',
]


def finite(value):
    if isinstance(value,bool) or not isinstance(value,(float,int)):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (ValueError,OverflowError):
        return None


def _source(source):
    c=(source.get('result') or {}).get('calculation') or {}
    if not all(isinstance(c.get(k),dict) for k in ('inputs','summary','states','components')):
        raise ValueError('来源缺少完整的稳态输入、状态点或部件结果，请重新运行设计点。')
    s=c['summary']
    if numerical_convergence(c)['status'] != 'pass':
        raise ValueError('来源稳态记录尚未完整数值收敛或缺少分层证据，请重新计算后用作瞬态热态初值。')
    assessment=source['result'].get('assessment') or {}
    if assessment.get('status')!='pass':
        failed=[x.get('label',x.get('key','系统约束')) for x in assessment.get('checks',[]) if x.get('status')!='pass']
        raise ValueError('来源系统约束未通过：'+('；'.join(failed[:4]) or '请先完成稳态系统约束校核。'))
    if c['inputs'].get('cycle_type') not in ('gg','ffsc'):
        raise ValueError('当前仅支持 GG 与 FFSC 热态工作点。')
    for key in ('crossflow_fuel_to_oxpb_margin_MPa','crossflow_lox_to_fuelpb_margin_MPa'):
        if key in s and (finite(s[key]) is None or s[key]<-1e-9):
            raise ValueError(f'{key}: 来源存在交叉流供给欠压，不能用绝对值或虚构补压建立动态支路。')
    return c


def transient_setup(source):
    """Expose assumptions, defaults and source readiness without running a job."""
    issues=[]
    c=(source.get('result') or {}).get('calculation') or {}
    inp=c.get('inputs') or {}; components=c.get('components') or {}
    def output(name,key,fallback):
        value=finite((components.get(name) or {}).get('outputs',{}).get(key))
        return value if value is not None and value>0 else fallback
    area=output('nozzle','At_m2',.01)
    mainV=output('main_chamber','cc_volume_m3',area*(finite(inp.get('chamber_l_star_m')) or 1.0))
    single=inp.get('cycle_type')=='gg' and inp.get('gg_layout')=='single_shaft'
    def inertia(side):
        power=output(side+'_pump','power_W',1e6)
        if single and side=='ox': power+=output('fuel_pump','power_W',1e6)
        omega=output(side+'_pump','nr_rpm',20000)*math.pi/30
        return max(.0001,min(1000,power*.1/(omega*omega)))
    defaults=dict(duration_s=1.0,sample_count=201,command_time_s=.2,command_ramp_s=.05,
                  fuel_area_ratio=.95,ox_area_ratio=.95,chamber_volume_m3=mainV,
                  generator_volume_m3=max(1e-6,mainV*.05),exhaust_volume_m3=max(1e-6,mainV*.1),
                  inertia_ox_kg_m2=inertia('ox'),inertia_fuel_kg_m2=inertia('fuel'),
                  pump_head_slope=.25,rtol=1e-6)
    rows=[
        ('duration_s','仿真时长','s',.05,10,.05,'用户设置','积分终止时间。'),
        ('sample_count','输出采样点数','点',21,2001,1,'用户设置','仅控制输出采样；积分器使用自适应内部步长。'),
        ('command_time_s','阀口变化起始时刻','s',0,9.9,.01,'用户设置','从热态初值开始施加扰动的时刻。'),
        ('command_ramp_s','阀口变化持续时间','s',.001,5,.01,'用户设置','有效面积命令的线性变化时间，不是拟合响应时间常数。'),
        ('fuel_area_ratio','燃料相对有效阀口面积','—',.9,1.1,.01,'用户设置','燃料各液路支路共同的相对有效流通面积。'),
        ('ox_area_ratio','氧化剂相对有效阀口面积','—',.9,1.1,.01,'用户设置','氧化剂各液路支路共同的相对有效流通面积。'),
        ('chamber_volume_m3','主燃烧室有效容积','m³',1e-6,10,.001,'来源几何估算','来源 cc_volume_m3；缺失时使用 L*At，属于几何估算。'),
        ('generator_volume_m3','燃气发生器 / 单个预燃室容积','m³',1e-6,10,.0001,'研究假设','默认主室容积的 5%；FFSC 两预燃室分别采用该值。需几何或试验数据替换。'),
        ('exhaust_volume_m3','GG 排气合流容腔容积','m³',1e-6,10,.0001,'研究假设','默认主室容积的 10%；仅 GG 使用。'),
        ('inertia_ox_kg_m2','共轴转动惯量' if single else '液氧侧轴系转动惯量','kg·m²',.0001,1000,.01,'研究假设','默认 J=P轴×0.1/ω²，仅作量级起点；不是实际发动机惯量。'),
        ('inertia_fuel_kg_m2','燃料侧轴系转动惯量','kg·m²',.0001,1000,.01,'研究假设','仅双轴使用；单轴采用共轴惯量。'),
        ('pump_head_slope','局部泵扬程曲线斜率系数','—',.05,2,.05,'研究假设','Δp/Δp₀=(1+s)(ω/ω₀)²−s(ṁ/ṁ₀)²；非实测泵图。'),
        ('rtol','积分相对容差','—',1e-8,1e-4,1e-7,'数值设置','Radau 刚性积分；归一化状态绝对容差为该值的 0.01 倍。'),
    ]
    parameters=[dict(zip(('key','label','unit','min','max','step','source','description'),row)) for row in rows]
    try:
        c=_source(source)
        from transient import HotStateModel
        model=HotStateModel(c,defaults)
        nominal=float(max(abs(model.rhs(0,model.initial)[:-2])))
        if nominal>1e-4:
            issues.append(f'来源映射后的名义状态不是动态平衡点：最大归一化导数 {nominal:.3g} /s。')
    except (ValueError,KeyError,TypeError,ZeroDivisionError) as exc:
        issues.append(str(exc) if not isinstance(exc,KeyError) else '来源缺少动态初始化字段：'+str(exc))
    return dict(ready=not issues,issues=issues,defaults=defaults,parameters=parameters,
                source_summary=dict(cycle_type=inp.get('cycle_type'),gg_layout=inp.get('gg_layout'),
                    propellant_pair=inp.get('propellant_pair'),pc_Pa=inp.get('pc'),
                    thrust_N=(c.get('summary') or {}).get('thrust_N'),chamber_volume_m3=mainV,
                    model='hot_state_local_0d_v1'),limitations=LIMITATIONS)


def defaults_for_source(source):
    return transient_setup(source)['defaults']


def normalize_request(value,source):
    setup=transient_setup(source)
    if not setup['ready']:
        raise ValueError('；'.join(setup['issues']))
    if not isinstance(value,dict) or set(value)-({'source_job_id'}|set(setup['defaults'])):
        raise ValueError('瞬态请求包含未支持的参数。')
    if value.get('source_job_id')!=source['id']:
        raise ValueError('来源设计记录不匹配。')
    result={'source_job_id':source['id'],**setup['defaults'],**value}
    for p in setup['parameters']:
        v=finite(result[p['key']])
        if v is None or not p['min']<=v<=p['max']:
            raise ValueError(f'{p["label"]}须在 {p["min"]}–{p["max"]} 范围内。')
        if p['key']=='sample_count':
            if not v.is_integer(): raise ValueError('输出采样点数必须为整数。')
            v=int(v)
        result[p['key']]=v
    if result['command_time_s']+result['command_ramp_s']>=result['duration_s']:
        raise ValueError('阀口变化须在仿真结束前完成，并留出响应时间。')
    return result


def calculate_transient(request,source,progress=None):
    from transient import HotStateModel
    from version import APP_VERSION
    request=normalize_request(request,source)
    model=HotStateModel(_source(source),request)
    nominal=float(max(abs(model.rhs(0,model.initial)[:-2])))
    if nominal>1e-4:
        raise ValueError(f'当前容积与惯量设置放大了来源的初始化不平衡：最大归一化导数 {nominal:.3g} /s。')
    result=dict(schema_version=1,application_version=APP_VERSION,analysis_type='hot_state_transient',
        source=dict(job_id=source['id'],project_id=source['project_id'],revision=source['revision'],
                    created=source.get('created'),result_sha256=fingerprint(source['result']),
                    calculation_inputs_sha256=fingerprint(source['result']['calculation']['inputs'])),
        request=deepcopy(request),source_inputs=deepcopy(model.inputs),
        environment=science_environment(model.inputs['propellant_pair']),complete=False,samples=[],
        limitations=LIMITATIONS,model_description=dict(id='hot_state_local_0d_v1',
            title='零维热态小扰动 · 质量 / 能量 / 轴系动能',
            equations=['dm/dt = Σṁin − Σṁout',
                       'dU/dt = Σṁin hin − Σṁout hout + Qeffective − Pturbine',
                       'd(½Jω²)/dt = ηmechanical Pturbine − Ppump',
                       'p = mRT/V; U = m cv T',
                       'Δppump/Δp₀ = (1+s)(ω/ω₀)² − s(ṁ/ṁ₀)²',
                       'ṁliquid = C Arelative √max(pup−pdown,0)'],
            parameter_evidence=transient_setup(source)['parameters'],
            volumes=deepcopy(model.volumes),shafts=deepcopy(model.shafts),
            nozzle_calibration=deepcopy(model.nozzles),
            liquid_branches=deepcopy(model.branches),
            turbine_calibration=deepcopy(model.turbines),
            nominal_work_fit_policy='仅当名义泵功率略大于涡轮功率且相对残差≤1e-7时，对局部涡轮比功系数作等比例数值拟合；逐涡轮记录修正值，机械效率始终≤1。更大不平衡阻止初始化。',
            liquid_cp_assumptions_JkgK=model.liquid_cp,
            energy_boundary='所有燃气容腔与轴系转子；液路、泵流体和冷却壁在该储能边界外。液体馈入温度及比热冻结；未反馈瞬时泵功引起的液体温升。',
            aggregate_energy_balance='d(ΣUgas+ΣKshaft)/dt = Σṁfeed hfeed + ΣQeffective − Σṁnozzle hgas − ΣPpump − Σ(1−ηmechanical)Pturbine',
            heat_input_model='液体燃烧器的名义限制反应物斜率冻结；FFSC 主燃室的后燃等效热按总气体入流缩放，后燃混合比敏感度未建模。',
            experiment_validated=False),
        audits=dict(nominal_max_scaled_derivative_per_s=nominal,status='pending'),metrics={})
    def publish(sample):
        result['samples'].append(sample)
        count=len(result['samples'])
        if progress and (count==1 or count%10==0 or count==request['sample_count']):
            progress(dict(iteration=count,maximum=request['sample_count'],phase='积分热态动态模型',
                          time_s=sample['time_s'],completed=count),deepcopy(result))
    try:
        _,integrator=model.integrate(publish)
        result['integrator']=integrator
        result['complete']=True
        result['termination_reason']='completed'
    except ValueError as exc:
        result['termination_reason']=str(exc)
        if not result['samples']: raise
    samples=result['samples']
    mass=max(abs(s['mass_balance_error_kg']) for s in samples)
    energy=max(abs(s['energy_balance_error_J']) for s in samples)
    result['audits'].update(max_abs_mass_balance_error_kg=mass,max_abs_energy_balance_error_J=energy,
        mass_relative_error=mass/model.mass_scale,energy_relative_error=energy/model.energy_scale,
        status='pass' if mass/model.mass_scale<1e-5 and energy/model.energy_scale<1e-5 else 'fail',
        basis='容腔质量与热内能、轴系动能相对积分边界通量的有符号闭合；不代表试验验证。')
    result['metrics']=dict(sample_count=len(samples),initial_thrust_N=samples[0]['thrust_N'],
        final_thrust_N=samples[-1]['thrust_N'],max_thrust_N=max(s['thrust_N'] for s in samples),
        min_thrust_N=min(s['thrust_N'] for s in samples),initial_pc_Pa=samples[0]['pc_Pa'],
        final_pc_Pa=samples[-1]['pc_Pa'],last_time_s=samples[-1]['time_s'],
        interpretation='末点值不等于已达到新稳态；不报告未经判据验证的稳定时间。')
    missing=[]
    result=plain(result,missing)
    result['nonfinite_fields']=missing
    json.dumps(result,ensure_ascii=False,allow_nan=False)
    if progress: progress(dict(iteration=len(samples),maximum=request['sample_count'],
        completed=len(samples),phase='积分完成' if result['complete'] else '局部模型范围终止',
        time_s=samples[-1]['time_s']),deepcopy(result))
    return result
