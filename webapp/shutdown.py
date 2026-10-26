"""Three explicitly independent shutdown/restart research units and frozen jobs."""
from copy import deepcopy
import importlib
import json
import math

from webapp.solver import plain, science_environment
from webapp.validation import fingerprint
from webapp.ignition import field


LIMITATIONS=[
    '三个研究单元分别积分，各自拥有独立边界、库存和初态；并未耦合成指定参考机型的整机关机与再启动模型。来源设计记录只用于方案追溯，默认值均为研究示例。',
    '所有阶段在同一次积分中延续质量、内能、壁温、实际阀位或转速；再启动事件不重置停机末态。历史记录保存原始输入、事件和末态快照。',
    '气相单元采用理想氧气/甲烷反应容腔与给定热脉冲；纯工质单元采用 HEOS 均相气液库存；两者不同能量基准未直接串联。',
    '真实流体泄放排出均相混合物；不解析空间液位、死区残液、不凝气体吹除或彻底排空。达到背压后不会人为清零库存。',
    '反流与有符号转速在满液轴系单元计算；该单元不适用于排空、汽蚀或大幅压缩，压力或泵图超界即停止。集总管路惯性不等于分布式水击峰值预测。',
    '给定再次供给、外加驱动力矩或热脉冲属于研究输入；尚无实机阀序、泵图、摩擦及壁面换热标定，不提供自动再点火成功、延迟或熄火判据。',
    '守恒审计和解析极限对照属于数值验证；不能替代有边界测量及不确定度的独立关机/再启动试验。',
]
MODES={
    'gas':('气相关机与给定再点火','shutdown_gas','ShutdownGasModel',
           '氧气/甲烷有限速率反应容腔：首次点火 → 关阀余气泄放 → 热浸 → 再次供给与给定热脉冲。'),
    'inventory':('残余工质泄放与再充填','shutdown_inventory','ShutdownInventoryModel',
                 '纯工质均相气液库存与金属壁：关入口、泄放、余热交换与再次充填；出口含理想止回。'),
    'rotor':('轴系惰转与液压反流','shutdown_rotor','ShutdownRotorModel',
             '满液管网、四象限泵、实际阀位和转子动力学：撤除驱动力矩、关阀、惰转与再次驱动。'),
}


def chart(title,unit,*series,description=''):
    return dict(title=title,unit=unit,description=description,series=[dict(key=x[0],label=x[1],factor=x[2] if len(x)>2 else 1) for x in series])


CHARTS={
 'gas':[
    chart('容腔压力','MPa',('pressure_Pa','绝对压力',1e-6)),
    chart('流体与壁面热记忆','K',('temperature_K','气体'),('wall_temperature_K','金属壁')),
    chart('残余气体库存','g',('mass_kg','气体质量',1000)),
    chart('供给与余气排出','g/s',('ox_mass_flow_kg_s','氧气入口',1000),('fuel_mass_flow_kg_s','甲烷入口',1000),('outlet_mass_flow_kg_s','等效出口',1000)),
    chart('入口阀执行过程','%',('ox_opening','氧阀实际',100),('ox_command','氧阀指令',100),('fuel_opening','燃料阀实际',100),('fuel_command','燃料阀指令',100)),
    chart('外部点火与化学热释放','kW',('igniter_power_W','外部热输入',.001),('chemical_heat_release_W','化学热释放诊断',.001),description='生成焓已计入内能；化学热释放仅作诊断，不重复计为外部热源。'),
    chart('壁面与环境换热','W',('heat_to_wall_W','流体至壁面'),('heat_from_ambient_W','环境至壁面')),
    chart('总能量闭合误差','J',('energy_balance_error_J','总能量误差'),('fluid_energy_balance_error_J','流体能量误差'),('wall_energy_balance_error_J','壁面能量误差')),
 ],
 'inventory':[
    chart('容腔压力','MPa',('pressure_Pa','绝对压力',1e-6)),
    chart('流体与壁面余热','K',('temperature_K','流体'),('wall_temperature_K','金属壁')),
    chart('残余气液库存','g',('mass_kg','总质量',1000),('liquid_mass_kg','液相质量',1000),('vapor_mass_kg','气相质量',1000)),
    chart('气液体积占比','%',('liquid_volume_fraction','液相体积分数',100),('void_fraction','气相体积分数',100),description='均相容腔体积分数，不等于空间液位。'),
    chart('泄放与再充填流量','g/s',('inlet_mass_flow_kg_s','入口',1000),('outlet_mass_flow_kg_s','混合物出口',1000)),
    chart('阀门执行过程','%',('inlet_opening','入口实际',100),('inlet_command','入口指令',100),('outlet_opening','出口实际',100),('outlet_command','出口指令',100)),
    chart('余热传递','W',('heat_to_fluid_W','壁面至流体'),('heat_from_ambient_W','环境至壁面')),
    chart('总能量闭合误差','J',('energy_balance_error_J','总能量误差'),('fluid_energy_balance_error_J','流体能量误差'),('wall_energy_balance_error_J','壁面能量误差')),
 ],
 'rotor':[
    chart('惰转与再次驱动','rpm',('speed_rpm','轴转速'),description='由转矩平衡积分得到；负值表示反转。'),
    chart('容腔压力','MPa',('manifold_pressure_Pa','绝对压力',1e-6)),
    chart('有符号支路流量','kg/s',('pump_mass_flow_kg_s','泵支路'),('outlet_mass_flow_kg_s','出口支路'),description='泵支路正向为储源至容腔；出口正向为容腔至背压端。负值保留为反流。'),
    chart('转矩平衡','N·m',('drive_torque_Nm','外给驱动'),('pump_torque_Nm','泵负载'),('friction_torque_Nm','摩擦')),
    chart('转子动能','J',('rotor_energy_J','转子动能')),
    chart('关阀与再开阀','%',('valve_opening','实际开度',100),('valve_command','指令开度',100)),
    chart('满液容腔质量','kg',('inventory_kg','库存'),description='仅单相小压缩库存变化，本单元不模拟排空。'),
    chart('液压与转子能量误差','J',('total_energy_error_J','联合能量误差'),('rotor_energy_error_J','转子能量误差'),('hydraulic_energy_error_J','液压能量误差')),
 ]}


for _chart in CHARTS['gas']:
    for _series in _chart['series']:
        if _series['key']=='igniter_power_W': _series['step']=True


def _module(mode):
    if mode not in MODES: raise ValueError('请选择 gas、inventory 或 rotor 独立研究单元。')
    return importlib.import_module(MODES[mode][1])


def _source(source,mode=None):
    calc=(source.get('result') or {}).get('calculation') or {}
    if source.get('status')!='succeeded' or not isinstance(calc.get('inputs'),dict):
        raise ValueError('请选择已完成的稳态设计记录用于追溯。')
    if mode=='gas' and calc['inputs'].get('propellant_pair')!='lox_methane':
        raise ValueError('气相再次点火研究仅支持氧气/甲烷；不以甲烷机理代替煤油。')


def descriptors(mode,defaults):
    sequence=[field('shutdown_time_s','关机指令时刻','s',0,60,.05,'关机与再次供给时序','开始关阀或撤除驱动的给定事件，不重置物理状态。'),
              field('restart_time_s','再次供给／驱动时刻','s',0,60,.05,'关机与再次供给时序','沿用停留后的残余库存、壁温或转速。')]
    if mode=='gas':
        from webapp.ignition import PARAMETERS
        params=deepcopy(PARAMETERS)+sequence+[
            field('reignition_time_s','再次外部热脉冲时刻','s',0,30,.01,'再次给定点火事件','给定事件，非自主预测的点火延迟。'),
            field('reignition_duration_s','再次热脉冲历时','s',.000001,10,.01,'再次给定点火事件','脉冲始末均为积分分段。'),
            field('reignition_energy_J','再次外部点火能量','J',0,1e5,10,'再次给定点火事件','0 可用于无额外点火输入对照。'),
            dict(key='chemistry_enabled',label='启用气相化学反应',type='boolean',group='容腔初态',description='关闭可进行惰性流动与外加热对照。'),
            dict(key='initial_mixture',label='初始气体组成',group='容腔初态',options=[dict(value='nitrogen',label='氮气'),dict(value='reactants',label='氧气/甲烷未燃预混气')])]
        for p in params:
            if p['key']=='duration_s':p['max']=30
            if p['key'] in ('final_ox_opening','final_fuel_opening'):p['label']=p['label'].replace('末端指令','工作阶段开度')
        priority=['时间设置','关机与再次供给时序','再次给定点火事件','阀门时序','外部点火事件','容腔初态','气相供给边界','壁面换热','边界与响应指标']
        params.sort(key=lambda p:priority.index(p['group']) if p['group'] in priority else len(priority))
        return params,[]
    if mode=='inventory':
        from webapp.chilldown import PARAMETERS,CIRCUIT_PARAMETERS
    else:
        from webapp.feed_network import PARAMETERS,CIRCUIT_PARAMETERS
    params=[deepcopy(p) for p in PARAMETERS if p['key'] in defaults]+sequence
    cs=defaults['circuits'][0]
    cp=[deepcopy(p) for p in CIRCUIT_PARAMETERS if p['key'] in cs]
    if mode=='inventory':
        cp += [dict(key='fluid',label='独立研究纯工质',group='容腔与初态',description='更换工质后须相应设置有效温压；正十二烷是煤油代理。',options=[dict(value=v,label=l) for v,l in [('Oxygen','氧'),('Methane','甲烷'),('n-Dodecane','正十二烷（煤油代理）')]]),
               dict(key='initial_mode',label='初始物性输入',group='容腔与初态',options=[dict(value='PT',label='压力与温度（单相）'),dict(value='PQ',label='压力与蒸气质量分数（饱和）')])]
        for stage,cn in [('shutdown','停机阶段'),('restart','再充填阶段')]:
            for port,label in [('inlet','入口'),('outlet','出口')]:
                cp.append(field(f'{stage}_{port}_opening',cn+label+'指令开度','%',0,1,1,'阀门执行器','阶段间按给定斜坡变化，实际阀位继续积分。',100))
    else:
        for p in cp:
            if p['key']=='initial_speed_ratio':p.update(label='初始轴速 / 额定',description='仅初始条件；后续转速由转矩与惯量积分。',group='转子与驱动')
            if p['key'] in ('tank_pressure_Pa','outlet_pressure_Pa','initial_manifold_pressure_Pa'):p['description']='独立试验台给定边界或初值，非来源发动机预测值。'
        cp += [field('rho_kg_m3','冻结液体密度','kg/m³',1,3000,1,'单相物性与泵标度','独立单相研究输入；全过程不计算温度和相变。'),
               field('bulk_modulus_Pa','冻结体积模量','GPa',1e5,1e11,.01,'单相物性与泵标度','用于小压缩液腔 C=V/K。',1e-9),
               field('pressure_floor_Pa','单相最低压力','MPa',1,1e8,.01,'单相物性与泵标度','须由实际流体温度与气蚀裕度确定；默认仅研究边界。',1e-6),
               field('pump_head_Pa','泵额定压升','MPa',1,1e9,.1,'单相物性与泵标度','泵图归一化标度。',1e-6),
               field('pump_flow_m3_s','泵额定体积流量','L/s',1e-8,1, .1,'单相物性与泵标度','泵图归一化标度。',1000),
               field('pump_speed_rpm','泵额定转速','rpm',1,1e6,100,'单相物性与泵标度','用于 n=ω/ωref。'),
               field('pump_efficiency','泵额定效率','%',.01,1,1,'单相物性与泵标度','泵图额定转矩标度。',100),
               field('rotor_inertia_kg_m2','转动惯量','kg·m²',1e-7,1e4,.001,'转子与驱动','实际转子惯量，需部件数据或试验。'),
               field('rotor_linear_friction_Nm_s','线性摩擦系数','N·m·s',0,1e4,.0001,'转子与驱动','摩擦力矩 bω。'),
               field('rotor_quadratic_friction_Nm_s2','二次摩擦系数','N·m·s²',0,1e4,.000001,'转子与驱动','摩擦力矩 cω|ω|，保证耗散非负。'),
               field('drive_torque_scale','初始平衡驱动力矩倍数','—',0,5,.1,'转子与驱动','1 为初态泵负载加摩擦；关机撤除，再驱动恢复。不是涡轮输出模型。')]
    return params,cp


def shutdown_setup(source):
    _source(source)
    modes=[]
    for mode,(label,mod,cls,description) in MODES.items():
        module=_module(mode);defaults=module.default_settings();params,cp=descriptors(mode,defaults)
        issues=[]
        try: _source(source,mode)
        except ValueError as exc: issues.append(str(exc))
        modes.append(dict(value=mode,label=label,description=description,ready=not issues,issues=issues,
            defaults=defaults,parameters=params,circuit_parameters=cp,charts=deepcopy(CHARTS[mode])))
    return dict(ready=any(m['ready'] for m in modes),issues=[],modes=modes,limitations=LIMITATIONS,
        source_summary=dict(job_id=source['id'],revision=source['revision'],purpose='仅方案来源追溯；各研究单元独立初值与边界。'),
        source_constraints=deepcopy((source.get('result') or {}).get('assessment') or {}))


def normalize_request(value,source):
    if not isinstance(value,dict) or set(value)-{'source_job_id','mode','settings'}:
        raise ValueError('关机研究请求含未知字段。')
    if value.get('source_job_id')!=source.get('id'):raise ValueError('来源记录不匹配。')
    mode=value.get('mode','gas')
    if not isinstance(mode,str) or mode not in MODES:raise ValueError('未知研究单元。')
    _source(source,mode)
    module=_module(mode);settings=module.default_settings();custom=value.get('settings',{})
    if not isinstance(custom,dict) or set(custom)-set(settings):raise ValueError('研究设置包含未知字段。')
    settings.update(deepcopy(custom))
    # Every core owns its strict physical contract; UI display bounds are not
    # silently substituted for the governing model's validation.
    settings=module.validate_settings(settings)
    # Also reject an inconsistent initial EOS/state before enqueueing a job.
    getattr(module,MODES[mode][2])(settings)
    out=dict(source_job_id=source['id'],mode=mode,settings=settings)
    json.dumps(out,allow_nan=False)
    return out


def calculate_shutdown(request,source,progress=None):
    from version import APP_VERSION
    request=normalize_request(request,source);mode=request['mode'];module=_module(mode)
    model=getattr(module,MODES[mode][2])(request['settings'])
    result=dict(schema_version=1,application_version=APP_VERSION,analysis_type='shutdown_restart_research',mode=mode,
        source=dict(job_id=source['id'],project_id=source['project_id'],revision=source['revision'],result_sha256=fingerprint(source['result'])),
        request=deepcopy(request),environment=science_environment(),source_constraints=deepcopy(source['result'].get('assessment') or {}),
        complete=False,samples=[],audits={'status':'pending'},model_description=deepcopy(model.model_description),
        limitations=LIMITATIONS,charts=deepcopy(CHARTS[mode]))
    if hasattr(model,'scheduled_events'):
        result['events']=[dict(name=n,time_s=t,kind='prescribed',observed=False) for n,t in model.scheduled_events()]
    else:result['events']=deepcopy(getattr(model,'events',[]))
    if hasattr(model,'initialization_snapshot'):
        initial=deepcopy(model.initialization_snapshot)
        if mode=='gas':
            initial=dict(schema='shutdown-gas-state-v1',time_s=initial['time_s'],circuits=[dict(initial,id='chamber',label='气相反应容腔')],engine_initialization_supported=False)
        result['initialization_snapshot']=initial
    def normalized():
        cleaned=plain(deepcopy(result),missing:=[]);cleaned['nonfinite_fields']=missing
        return cleaned
    def publish(row):
        result['samples'].append(deepcopy(row));count=len(result['samples'])
        for e in result['events']: e['observed']=e['time_s']<=row['time_s']+1e-12
        if progress and (count==1 or count%10==0):
            progress(dict(completed=count,maximum=max(request['settings']['sample_count'],count),time_s=row['time_s'],phase='连续积分关机与再次供给过程'),normalized())
    samples,metadata=model.integrate(publish)
    result.update(metadata);result['samples']=samples
    for e in result.get('events',[]): e['observed']=bool(samples) and e['time_s']<=samples[-1]['time_s']+1e-12
    result['reason']=result.get('termination_reason') or result.get('reason')
    for key in ('initialization_snapshot','endpoint_snapshot'):
        if isinstance(result.get(key),dict):result[key].update(source=deepcopy(result['source']),request_sha256=fingerprint(request),engine_initialization_supported=False)
    result=normalized();json.dumps(result,allow_nan=False)
    if progress:progress(dict(completed=len(samples),maximum=max(request['settings']['sample_count'],len(samples)),time_s=samples[-1]['time_s'] if samples else 0,
        phase='计算完成' if result['complete'] else '已保留模型有效时间段'),deepcopy(result))
    return result
