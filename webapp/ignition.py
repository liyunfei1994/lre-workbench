"""Immutable-source adapter for the gaseous ignition-event pressure-rise bench."""
from copy import deepcopy
import json
import math

from webapp.solver import plain, science_environment
from webapp.validation import fingerprint


LIMITATIONS = [
    '本页是氧气/甲烷气相供给、执行阀、均匀反应容腔与等效出口的研究模型。来源机型仅确定工质和追溯关系，不代表该机型整机启动仿真。',
    '初态与供给边界在本页独立设置；不把稳态热燃气或预冷液体末态直接当作气相初值。尚无喷注雾化、液滴汽化、两相燃烧或不凝气置换管网。',
    '给定点火事件施加外部热脉冲；化学反应采用 Cantera GRI-Mech 3.0 气相有限速率。热脉冲能量与历时属于研究输入，未经实际点火器标定，不保证燃烧建立。',
    '反应物和产物内能、焓采用同一含生成焓基准；化学热释放率仅作诊断，不作为第二份热源加入总能量方程。',
    '理想气体混合物、零维完全混合假设不解析点火核、火焰传播、空间混合、声学不稳定性及高压非理想效应。机理计算结果不等于已经过此工况的试验验证。',
    '入口储源压力固定，实际流量随压差和阀位变化。等效出口区分未壅塞与壅塞；不据此输出完整拉瓦尔喷管启动推力、分离或侧载。',
    '研究边界带理想止回功能。尚未耦合低速泵图、转子加速、预燃室、涡轮、再生冷却网络和整机阀序控制。',
    '压力阈值到达时间是用户所定参考压力的响应指标，不是自主预测的点火延迟、点火成功或熄火判据。当前无此类实机判据。',
    '关闭化学反应可作惰性流动与外加热对照。煤油/正十二烷暂不支持本页，不用甲烷机理替代煤油的启动反应。',
    '守恒审计、时间步与容差检查属于数值验证；仍需有测量边界和不确定度的独立试验数据确认实际建压过程。',
]


def field(key, label, unit, low, high, step, group, description, factor=1):
    return dict(key=key,label=label,unit=unit,min=low,max=high,step=step,
                group=group,description=description,factor=factor)


PARAMETERS = [
    field('duration_s','仿真时长','s',.005,10,.1,'时间设置','独立反应容腔的积分时间。'),
    field('sample_count','基础输出采样数','点',21,1001,1,'时间设置','另保存事件精确时刻，实际点数可能略多；输出网格不决定点火时刻。'),
    field('rtol','积分相对容差','—',1e-9,1e-4,1e-7,'时间设置','数值误差控制，不代表物理模型精度。'),
    field('volume_m3','反应容腔容积','L',1e-6,.1,.01,'容腔初态','独立研究几何，不能由设计推力唯一反推。',1000),
    field('initial_pressure_Pa','初始绝对压力','MPa',1e4,2e6,.01,'容腔初态','气相初态压力，需位于用户边界内部。',1e-6),
    field('initial_temperature_K','初始气体温度','K',300,2000,10,'容腔初态','氮气或未燃氧气/甲烷混合气，不是液体初态。'),
    field('initial_of_ratio','初始预混气 O/F','—',.1,20,.1,'容腔初态','仅初始预混气使用；质量混合比，不是摩尔比。'),
    field('supply_ox_pressure_Pa','氧气储源绝对压力','MPa',1e4,2e6,.05,'气相供给边界','独立固定压力边界，不是泵出口计算值。',1e-6),
    field('supply_fuel_pressure_Pa','甲烷储源绝对压力','MPa',1e4,2e6,.05,'气相供给边界','独立固定压力边界，不是泵出口计算值。',1e-6),
    field('supply_ox_temperature_K','氧气供给温度','K',300,1500,10,'气相供给边界','只接受气相等效入口，不沿用低温液氧入口。'),
    field('supply_fuel_temperature_K','甲烷供给温度','K',300,1500,10,'气相供给边界','只接受气相等效入口，不包含液体汽化焓。'),
    field('inlet_ox_CdA_m2','氧气全开有效面积 CdA','mm²',0,1e-3,.1,'气相供给边界','包含流量系数；实际面积随阀位变化。',1e6),
    field('inlet_fuel_CdA_m2','甲烷全开有效面积 CdA','mm²',0,1e-3,.1,'气相供给边界','包含流量系数；0 表示封闭入口。',1e6),
    field('outlet_CdA_m2','等效出口面积 CdA','mm²',0,1e-3,.1,'气相供给边界','准稳态收敛孔口，不是完整扩张喷管。',1e6),
    field('back_pressure_Pa','出口绝对背压','MPa',1e4,2e6,.01,'气相供给边界','背压不低于最低压力；出口无反流。',1e-6),
    field('ox_open_time_s','氧阀指令开始时刻','s',0,10,.01,'阀门时序','氧气阀从初始指令向末端指令变化。'),
    field('fuel_open_time_s','燃料阀指令开始时刻','s',0,10,.01,'阀门时序','与点火时刻独立；无自动最佳阀序。'),
    field('valve_ramp_s','阀门指令变化历时','s',.0001,10,.01,'阀门时序','给定指令斜坡，实际阀位另有执行器响应。'),
    field('valve_tau_s','执行器时间常数','s',.0001,2,.005,'阀门时序','一阶执行器参数，需部件试验确定。'),
    field('valve_rate_per_s','最大开闭速率','%/s',.01,1e4,10,'阀门时序','实际开度的变化率上限。',100),
    field('initial_ox_opening','氧阀初始开度','%',0,1,1,'阀门时序','初始实际开度与指令一致。',100),
    field('initial_fuel_opening','燃料阀初始开度','%',0,1,1,'阀门时序','初始实际开度与指令一致。',100),
    field('final_ox_opening','氧阀末端指令','%',0,1,1,'阀门时序','按本页等效面积线性开度关系计算。',100),
    field('final_fuel_opening','燃料阀末端指令','%',0,1,1,'阀门时序','末端指令不是目标推力。',100),
    field('ignition_time_s','外部点火事件时刻','s',0,10,.01,'外部点火事件','给定热脉冲的开始时刻；不等于燃烧建立时刻。'),
    field('ignition_duration_s','点火热脉冲历时','s',.0001,5,.005,'外部点火事件','事件终点独立于输出采样网格。'),
    field('ignition_energy_J','外部点火输入能量','J',0,1e5,1,'外部点火事件','0 为无点火热输入对照；未经具体点火器标定。'),
    field('wall_temperature_K','初始壁温','K',200,2000,10,'壁面换热','等效集总壁面初温。'),
    field('wall_heat_capacity_J_K','壁面热容量','J/K',.01,1e6,10,'壁面换热','金属质量乘比热，本轮为常量。'),
    field('wall_UA_W_K','流体至壁面换热导纳','W/K',0,1e4,1,'壁面换热','给定换热参数；不代替详细火焰壁面换热计算。'),
    field('ambient_temperature_K','环境温度','K',200,1500,10,'壁面换热','壁面对外热边界。'),
    field('ambient_UA_W_K','壁面至环境换热导纳','W/K',0,1e4,1,'壁面换热','0 表示壁面对环境绝热。'),
    field('min_pressure_Pa','最低压力边界','MPa',1e3,2e6,.01,'边界与响应指标','超界停止并保留有效时间段。',1e-6),
    field('max_pressure_Pa','最高压力边界','MPa',1e4,5e6,.1,'边界与响应指标','用户研究边界，不是结构强度校核。',1e-6),
    field('min_temperature_K','最低气体温度','K',300,1500,10,'边界与响应指标','不允许静默进入未建模的低温液体区。'),
    field('max_temperature_K','最高气体温度','K',500,3000,100,'边界与响应指标','本页限制于共同热力学范围 300–3000 K；超用户边界终止。'),
    field('max_wall_temperature_K','最高壁温','K',250,3000,100,'边界与响应指标','用户停止条件，不代表材料许用温度。'),
    field('reference_pressure_Pa','建压参考压力','MPa',1e4,5e6,.05,'边界与响应指标','观察首次达到参考压力的时刻，不作为点火成功判据。',1e-6),
]


def _source(source):
    calc=(source.get('result') or {}).get('calculation') or {}
    if source.get('status')!='succeeded' or not isinstance(calc.get('inputs'),dict):
        raise ValueError('请选择已完成的设计记录以保存工质及方案来源。')
    if calc['inputs'].get('propellant_pair')!='lox_methane':
        raise ValueError('本阶段只支持氧气/甲烷气相点火研究；煤油/正十二烷尚无本页适用的启动反应模型。')
    return calc


def ignition_setup(source):
    issues=[]; defaults={}
    try:
        _source(source)
        from ignition import IgnitionModel, default_settings
        defaults=default_settings()
        IgnitionModel(defaults)
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        issues.append(str(exc))
    return dict(ready=not issues,issues=issues,defaults=defaults,parameters=PARAMETERS,
        initial_mixtures=[dict(value='nitrogen',label='氮气初始库存'),dict(value='reactants',label='未燃氧气/甲烷预混气')],
        source_summary=dict(job_id=source.get('id'),revision=source.get('revision'),purpose='仅用于工质与方案追溯；独立气相研究台，非整机初值。'),
        source_constraints=deepcopy((source.get('result') or {}).get('assessment') or {}),limitations=LIMITATIONS)


def normalize_request(value,source):
    _source(source)
    allowed={'source_job_id','chemistry_enabled','initial_mixture'}|{p['key'] for p in PARAMETERS}
    if not isinstance(value,dict) or set(value)-allowed:
        raise ValueError('点火事件研究请求含未知字段；不接受液体快照或额外成功判据。')
    if value.get('source_job_id')!=source.get('id'):
        raise ValueError('来源记录不匹配。')
    from ignition import default_settings, validate_settings
    out={'source_job_id':source['id'],**default_settings()}
    for p in PARAMETERS:
        v=value.get(p['key'],out[p['key']])
        try: valid=isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and p['min']<=v<=p['max']
        except OverflowError: valid=False
        if not valid: raise ValueError(f'{p["label"]} 必须是允许范围内的有限数值。')
        if p['key']=='sample_count':
            if v!=int(v): raise ValueError('输出采样数必须是整数。')
            out[p['key']]=int(v)
        else: out[p['key']]=float(v)
    out['chemistry_enabled']=value.get('chemistry_enabled',out['chemistry_enabled'])
    if not isinstance(out['chemistry_enabled'],bool): raise ValueError('化学反应开关必须是布尔值。')
    out['initial_mixture']=value.get('initial_mixture',out['initial_mixture'])
    if out['initial_mixture'] not in ('nitrogen','reactants'): raise ValueError('初态仅支持氮气或氧气/甲烷未燃预混气。')
    settings={k:v for k,v in out.items() if k!='source_job_id'}
    validate_settings(settings)
    json.dumps(out,allow_nan=False)
    return out


def calculate_ignition(request,source,progress=None):
    from ignition import IgnitionModel
    from version import APP_VERSION
    request=normalize_request(request,source)
    model=IgnitionModel({k:v for k,v in request.items() if k!='source_job_id'})
    result=dict(schema_version=1,application_version=APP_VERSION,analysis_type='gaseous_ignition_event',
        source=dict(job_id=source['id'],project_id=source['project_id'],revision=source['revision'],result_sha256=fingerprint(source['result'])),
        request=deepcopy(request),environment=science_environment('lox_methane'),
        source_constraints=deepcopy(source['result'].get('assessment') or {}),complete=False,samples=[],
        model_description=deepcopy(model.model_description),limitations=LIMITATIONS,audits={'status':'pending'})
    def publish(samples,partial_metadata):
        result['samples']=deepcopy(samples)
        result.update(deepcopy(partial_metadata))
        count=len(samples)
        for event in result.get('events',[]):
            event['observed']=bool(samples) and event['time_s']<=samples[-1]['time_s']+1e-12
        if progress and (count==1 or count%10==0):
            partial=plain(deepcopy(result),missing:=[])
            partial['nonfinite_fields']=missing
            progress(dict(completed=count,maximum=max(request['sample_count'],count),time_s=samples[-1]['time_s'] if samples else 0,
                phase='积分气相反应库存、外部点火能量和建压响应'),partial)
    samples,metadata=model.integrate(publish)
    result['samples']=samples
    result.update(metadata)
    for key in ('initialization_snapshot','endpoint_snapshot'):
        if isinstance(result.get(key),dict):
            result[key].update(source=deepcopy(result['source']),request_sha256=fingerprint(request),engine_initialization_supported=False)
    result=plain(result,missing:=[]);result['nonfinite_fields']=missing
    json.dumps(result,allow_nan=False)
    if progress:
        progress(dict(completed=len(result['samples']),maximum=max(request['sample_count'],len(result['samples'])),
            phase='点火事件建压计算完成' if result['complete'] else '已保留模型有效时间段',
            time_s=result['samples'][-1]['time_s'] if result['samples'] else 0),deepcopy(result))
    return result
