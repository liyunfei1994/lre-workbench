"""Immutable design-fluid selection for the thermal inventory research bench."""
from copy import deepcopy
import json
import math

from webapp.solver import plain, science_environment
from webapp.validation import fingerprint


LIMITATIONS = [
    '每种工质分别建立一个刚性容腔与一个集总金属壁面。可作为管段、泵腔或冷却通道的等效单元；尚未串联成整机预冷网络。',
    '流体采用 CoolProp HEOS 纯工质均相热力学平衡闭合。两相温度、压力相同；不解析液面位置、滑移、气泡或实际充填前沿。',
    '入口与出口采用准稳态均相平衡等熵节流孔质量通量模型，并计入临界流限制；出口抽取均匀混合物，不是选择性排出蒸气的放空口。',
    '边界安装理想止回功能；供给压力不足时入口停止，背压高于容腔压力时出口停止。此页不模拟反流、管路惯性、泵和转子动力学。',
    '壁面热容量、壁流换热导纳 UA、环境导纳及有效流通面积是用户研究参数。默认值未由实机试验标定，不包含膜态沸腾到核态沸腾的专用关联式。',
    '初始气体必须是所选推进剂自身的蒸气。氮气、氦气等不凝气体吹除及多组分混合物尚未建立。',
    '入口与容腔物性在各自状态求值；超出物性或用户压力范围时停止并保留有效记录，不用相态钳制伪造持续充填。',
    '液相质量分数与液相体积分数是不同指标。液相体积分数用于本页充填判据，不代表已解析空间液位。',
    '末态快照只描述本页容腔和壁面；尚不能自动覆盖供给网络、热态瞬态或整机点火初值，也不代表满足点火或泵启动条件。',
    '质量和能量账目闭合属于数值验证；尚无本装置的独立预冷/充填试验验证。正十二烷是煤油代理纯工质，不是真实 RP-1 混合物。',
]


def field(key, label, unit, low, high, step, group, description, factor=1):
    return dict(key=key, label=label, unit=unit, min=low, max=high, step=step,
                group=group, description=description, factor=factor)


PARAMETERS = [
    field('duration_s','仿真时长','s',.05,300,.5,'时间设置','积分时间；达到适用域边界会提前停止。'),
    field('sample_count','输出采样数','点',21,1001,1,'时间设置','自适应积分与输出采样网格独立。'),
    field('command_time_s','阀门指令起始时刻','s',0,299,.1,'时间设置','从初始指令向末端指令变化的起点。'),
    field('command_ramp_s','指令变化历时','s',.001,300,.1,'时间设置','线性指令斜坡；实际开度另有滞后和速率限制。'),
    field('rtol','积分相对容差','—',1e-8,1e-4,1e-6,'数值设置','局部积分误差控制，不是物理模型精度。'),
]
CIRCUIT_PARAMETERS = [
    field('volume_m3','有效流体容积','L',1e-5,.1,.01,'容腔与初态','刚性几何体积；不从稳态流量反推。',1000),
    field('initial_pressure_Pa','初始绝对压力','MPa',1e4,1e7,.01,'容腔与初态','PT 或 PQ 初态的压力。',1e-6),
    field('initial_temperature_K','初始流体温度','K',55,600,1,'容腔与初态','PT 模式使用；必须与选定工质物性有效域一致。'),
    field('initial_quality','初始蒸气质量分数','%',0,1,1,'容腔与初态','PQ 模式使用；不是气相体积分数。',100),
    field('wall_temperature_K','初始壁面温度','K',55,800,1,'金属壁面','等效金属的均匀初温。'),
    field('wall_heat_capacity_J_K','壁面等效热容量','J/K',1,1e6,10,'金属壁面','金属质量乘有效比热；首版为常数。'),
    field('wall_UA_W_K','壁流换热导纳 UA','W/K',0,1e4,1,'金属壁面','Q=UA(T壁−T流体)；需按装置标定，非自动沸腾关联式。'),
    field('ambient_temperature_K','环境温度','K',55,800,1,'金属壁面','壁面与环境换热的边界温度。'),
    field('ambient_UA_W_K','环境换热导纳','W/K',0,1e3,.1,'金属壁面','设为 0 表示对外绝热。'),
    field('supply_pressure_Pa','供给绝对压力','MPa',1e4,1e7,.01,'流动边界','入口固定液体储源状态；非泵计算结果。',1e-6),
    field('supply_temperature_K','供给温度','K',55,600,1,'流动边界','默认复制来源入口温度，可为研究工况修改。'),
    field('back_pressure_Pa','出口绝对背压','MPa',1e4,1e7,.01,'流动边界','低于容腔压力时允许混合物排出。',1e-6),
    field('inlet_CdA_m2','入口全开有效面积 CdA','mm²',0,1e-3,.1,'流动边界','流量系数已包含在有效面积中；0 表示隔离。',1e6),
    field('outlet_CdA_m2','出口全开有效面积 CdA','mm²',0,1e-3,.1,'流动边界','出口抽取均相混合物，不能当作蒸气专用排气阀。',1e6),
    field('initial_inlet_opening','入口初始开度','%',0,1,1,'阀门执行器','实际初始行程与初始命令一致。',100),
    field('final_inlet_opening','入口末端指令','%',0,1,1,'阀门执行器','归一化有效面积随开度线性变化。',100),
    field('initial_outlet_opening','出口初始开度','%',0,1,1,'阀门执行器','初始命令与实际开度。',100),
    field('final_outlet_opening','出口末端指令','%',0,1,1,'阀门执行器','用于保持排出、关闭或改变流量。',100),
    field('valve_tau_s','执行器时间常数','s',.005,10,.01,'阀门执行器','一阶执行器；不是预冷时间常数。'),
    field('valve_rate_per_s','最大开闭速率','%/s',.01,100,10,'阀门执行器','开度变化率上限。',100),
    field('min_pressure_Pa','最低允许绝对压力','MPa',1e4,1e7,.01,'边界与判据','低于边界停止；还需满足 EOS 的物性范围。',1e-6),
    field('max_pressure_Pa','最高允许绝对压力','MPa',1e4,2e7,.01,'边界与判据','超过边界停止，不强行截断压力继续积分。',1e-6),
    field('max_wall_temperature_K','最高允许壁温','K',55,1000,1,'边界与判据','壁温超过边界停止。'),
    field('target_wall_temperature_K','预冷壁温目标上限','K',55,800,1,'边界与判据','用户研究判据；不等同于发动机点火许可。'),
    field('target_liquid_volume_fraction','目标液相体积分数','%',0,1,1,'边界与判据','末态液体占容腔体积的比例；不是液相质量分数。',100),
]


def _finite(v):
    try:
        return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
    except OverflowError:
        return False


def _defaults(source):
    if source.get('status')!='succeeded':
        raise ValueError('请选择已完成的稳态设计记录，以取得推进剂种类和供给温度。')
    calc=(source.get('result') or {}).get('calculation') or {}
    inp=calc.get('inputs')
    if not isinstance(inp,dict) or inp.get('propellant_pair') not in ('lox_methane','lox_kerosene'):
        raise ValueError('支持液氧/甲烷或液氧/正十二烷代理工质的设计记录。')
    circuits=[]
    fuel='n-Dodecane' if inp['propellant_pair']=='lox_kerosene' else 'Methane'
    for side,fluid,label,key in [('ox','Oxygen','液氧预冷单元','ox_inlet_temp'),('fuel',fuel,'燃料预冷单元','fuel_inlet_temp')]:
        temperature=inp.get(key)
        if not _finite(temperature) or temperature<=0:
            raise ValueError(f'{label} 来源供给温度无效。')
        cold=fluid!='n-Dodecane'
        circuits.append(dict(id=side,label=label,fluid=fluid,volume_m3=.0002,
            initial_mode='PT',initial_pressure_Pa=2e5,initial_temperature_K=290. if cold else float(temperature),
            initial_quality=1.,wall_temperature_K=290. if cold else max(320.,temperature+20.),
            wall_heat_capacity_J_K=120.,wall_UA_W_K=15.,ambient_temperature_K=290.,ambient_UA_W_K=0.,
            supply_pressure_Pa=8e5,supply_temperature_K=float(temperature),back_pressure_Pa=1e5,
            inlet_CdA_m2=.5e-6,outlet_CdA_m2=1e-6,
            initial_inlet_opening=0.,final_inlet_opening=1.,initial_outlet_opening=1.,final_outlet_opening=1.,
            valve_tau_s=.1,valve_rate_per_s=2.,min_pressure_Pa=5e4,max_pressure_Pa=1.5e6,
            max_wall_temperature_K=500.,target_wall_temperature_K=float(temperature)+20.,target_liquid_volume_fraction=.95))
    return dict(duration_s=20.,sample_count=201,command_time_s=.2,command_ramp_s=.5,rtol=1e-6,circuits=circuits)


def chilldown_setup(source):
    issues=[]; defaults={}
    try:
        defaults=_defaults(source)
        from chilldown import ChilldownModel
        ChilldownModel(defaults)
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        issues.append(str(exc))
    return dict(ready=not issues,issues=issues,defaults=defaults,parameters=PARAMETERS,circuit_parameters=CIRCUIT_PARAMETERS,
        initial_modes=[dict(value='PT',label='压力 + 温度（单相）'),dict(value='PQ',label='压力 + 蒸气质量分数（饱和）')],
        source_summary=dict(job_id=source.get('id'),revision=source.get('revision'),purpose='仅复制工质及供给温度；初始气液库存和壁温另行建立。'),
        source_constraints=deepcopy((source.get('result') or {}).get('assessment') or {}),limitations=LIMITATIONS)


def _numeric(value,p):
    if not _finite(value) or not p['min']<=value<=p['max']:
        raise ValueError(f'{p["label"]} 必须是允许范围内的有限数值。')
    if p['key']=='sample_count':
        if value!=int(value): raise ValueError('输出采样数须为整数。')
        return int(value)
    return float(value)


def normalize_request(value,source):
    allowed={'source_job_id','circuits'}|{p['key'] for p in PARAMETERS}
    if not isinstance(value,dict) or set(value)-allowed:
        raise ValueError('预冷与充填请求包含未知参数。')
    if value.get('source_job_id')!=source.get('id'):
        raise ValueError('来源记录不匹配。')
    out={'source_job_id':source['id'],**_defaults(source)}
    for p in PARAMETERS: out[p['key']]=_numeric(value.get(p['key'],out[p['key']]),p)
    if out['command_time_s']+out['command_ramp_s']>=out['duration_s']:
        raise ValueError('阀门指令变化须在仿真终点之前完成。')
    requested=value.get('circuits',out['circuits'])
    if not isinstance(requested,list) or not 1<=len(requested)<=2:
        raise ValueError('请选择 1–2 个独立预冷单元。')
    available={c['id']:c for c in out['circuits']}; selected=[]; seen=set()
    for item in requested:
        if not isinstance(item,dict) or item.get('id') not in available or item['id'] in seen:
            raise ValueError('预冷单元标识无效或重复。')
        c=deepcopy(available[item['id']]); seen.add(item['id'])
        if set(item)-set(c): raise ValueError('预冷单元包含未知字段；不支持不凝气体混合物。')
        for key in ('id','fluid','label'):
            if key in item and item[key]!=c[key]: raise ValueError(f'{key} 由来源记录确定，不可改写。')
        for p in CIRCUIT_PARAMETERS: c[p['key']]=_numeric(item.get(p['key'],c[p['key']]),p)
        c['initial_mode']=item.get('initial_mode',c['initial_mode'])
        if c['initial_mode'] not in ('PT','PQ'): raise ValueError('初态只能采用 PT 或 PQ。')
        selected.append(c)
    out['circuits']=selected
    from chilldown import ChilldownModel
    ChilldownModel(out)
    json.dumps(out,allow_nan=False)
    return out


def calculate_chilldown(request,source,progress=None):
    from chilldown import ChilldownModel
    from version import APP_VERSION
    request=normalize_request(request,source); model=ChilldownModel(request)
    result=dict(schema_version=1,application_version=APP_VERSION,analysis_type='chilldown_filling',
        source=dict(job_id=source['id'],project_id=source['project_id'],revision=source['revision'],result_sha256=fingerprint(source['result'])),
        request=deepcopy(request),environment=science_environment(source['result']['calculation']['inputs']['propellant_pair']),
        source_constraints=deepcopy(source['result'].get('assessment') or {}),complete=False,samples=[],
        model_description=deepcopy(model.model_description),limitations=LIMITATIONS,audits={'status':'pending'})
    def publish(sample):
        result['samples'].append(sample); count=len(result['samples'])
        if progress and (count==1 or count%10==0 or count==request['sample_count']):
            progress(dict(completed=count,maximum=request['sample_count'],time_s=sample['time_s'],phase='积分壁温、气液库存与真实流体状态'),deepcopy(result))
    _,metadata=model.integrate(publish)
    result.update(metadata)
    # Snapshots remain self-contained and bound to this precise source/model request.
    for key in ('initialization_snapshot','endpoint_snapshot'):
        if isinstance(result.get(key),dict):
            result[key].update(source=deepcopy(result['source']),request_sha256=fingerprint(request),engine_initialization_supported=False)
    result=plain(result,missing:=[]); result['nonfinite_fields']=missing
    json.dumps(result,allow_nan=False)
    if progress:
        progress(dict(completed=len(result['samples']),maximum=request['sample_count'],
            phase='预冷与充填积分完成' if result['complete'] else '预冷模型适用域终止',
            time_s=result['samples'][-1]['time_s'] if result['samples'] else 0),deepcopy(result))
    return result
