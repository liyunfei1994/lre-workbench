"""Immutable design scales -> explicitly bounded liquid feed subsystem studies."""
from copy import deepcopy
from functools import lru_cache
import json
import math

from webapp.solver import science_environment, plain
from webapp.validation import fingerprint


LIMITATIONS = [
    '本页是供给子系统研究台：两种工质各自连接固定供给压力、泵支路、液体容腔、执行阀及固定背压。尚未与预燃室/主室动态耦合。',
    '所选稳态记录仅提供工质状态与泵标度；默认管径、管长、容积、阻力、背压和执行器参数是可编辑研究假设，不是参考发动机实测数据。',
    '初始液路已充满单相液体；密度和等温体积模量在来源入口温度、所设容腔初压处计算后冻结。只使用小压缩线性容腔，不模拟预冷、气液界面、汽蚀或两相充填。',
    '泵参考体积流量来自来源入口密度；本页质量流量采用容腔参考密度乘体积流量，不能用来复现整机跨泵真实密度变化。',
    '转速时间线是外部规定的边界，泵转矩和轴功率是计算输出；本页不求启动器—转子—涡轮闭环，也不预测发动机自行启动。',
    '解析演示泵图用于检验零速、反流和四象限接口，不能替代真实离心泵全工况特性图。导入的用户图同样需要标定与试验验证。',
    '来源等熵泵效率在本研究台借作参考液压效率，压升×体积流量/效率仅是等温近似轴功尺度，不等同原真实流体泵轴功。',
    '阀门采用一阶滞后和开闭速率限制；零指令仍保留显式泄漏通流面积，不表示理想零泄漏隔离阀。',
    '液压能审计包含支路动能、容腔弹性储能、边界压力功、泵液压功和阻力耗散；不等于包含燃烧及壁面传热的整机总能量审计。',
    '集总模型不解析水击波传播；压力低于单相下限、容腔压缩超限或泵图越界时停止并保留有效结果。',
]
MAP_CONTRACT = {
    'angle': 'theta = atan2(q, n) mod 360°；n = rpm/rpm_ref，q = Q/Q_ref。',
    'head': 'head = Δp / [Δp_ref × (n²+q²)]',
    'torque': 'torque = τ / [τ_ref × (n²+q²)]；τ_ref = Δp_ref Q_ref / (η_ref ω_ref)。',
    'nominal': '额定点 n=q=1 对应 45°；head=torque=0.5。原点压升与转矩为零。',
    'csv': 'theta_deg,head,torque；角度升序覆盖 0–360°，端点值一致。曲线插值及运行状态须满足轴功减液压功非负。',
}


def _field(key, label, unit, low, high, step, group, description, factor=1):
    return dict(key=key, label=label, unit=unit, min=low, max=high, step=step,
                group=group, description=description, factor=factor)


PARAMETERS = [
    _field('duration_s','仿真时长','s',.05,20,.05,'运行时间','积分总时长。'),
    _field('sample_count','输出采样点数','点',21,2001,1,'运行时间','输出网格；不决定自适应积分内部步长。'),
    _field('command_time_s','命令变化起始时刻','s',0,19.9,.01,'运行时间','阀门和规定转速时间线的共同起始时刻。'),
    _field('command_ramp_s','命令变化持续时间','s',.001,10,.01,'运行时间','输入命令线性变化时间；阀门实际开度另有执行器动态。'),
    _field('rtol','积分相对容差','—',1e-8,1e-4,1e-7,'运行时间','Radau 刚性积分器容差。'),
]
CIRCUIT_PARAMETERS = [
    _field('tank_pressure_Pa','供给边界压力','MPa',1000,2e8,.01,'边界与初值','等效供给边界；默认取来源主泵入口，不代表直接从贮箱取流。',1e-6),
    _field('outlet_pressure_Pa','下游背压','MPa',1000,2e8,.01,'边界与初值','默认入口压力加额定压升的 25%，是独立试验台负载。',1e-6),
    _field('initial_manifold_pressure_Pa','容腔初始压力','MPa',1000,2e8,.01,'边界与初值','默认额定泵出口减入口支路损失；可设置非平衡初值。',1e-6),
    _field('initial_pump_flow_ratio','泵支路初始流量 / 额定','—',-1.8,1.8,.05,'边界与初值','带符号；正值朝容腔，负值朝供给端。'),
    _field('initial_outlet_flow_ratio','出口支路初始流量 / 额定','—',-1.8,1.8,.05,'边界与初值','带符号；正值朝下游背压端。'),
    _field('initial_speed_ratio','初始规定转速 / 额定','—',-1.2,1.2,.05,'转速边界','外部规定转速，可经过零速；不是积分得到的轴速。'),
    _field('final_speed_ratio','末端规定转速 / 额定','—',-1.2,1.2,.05,'转速边界','按共同命令时间线变化；负值用于反转研究。'),
    _field('inlet_length_m','泵支路等效管长','m',.01,100,.1,'管路与容腔','惯性 L=ρl/A；阻力另设。'),
    _field('inlet_diameter_m','泵支路等效内径','mm',.002,2,1,'管路与容腔','单根等效管的流通面积。',1000),
    _field('outlet_length_m','出口支路等效管长','m',.01,100,.1,'管路与容腔','集总流量惯性，非水击空间网格。'),
    _field('outlet_diameter_m','出口支路等效内径','mm',.002,2,1,'管路与容腔','与长度共同决定支路惯性。',1000),
    _field('volume_m3','液体容腔有效容积','L',1e-6,10,.1,'管路与容腔','C=V/K；初始已充满液体。',1000),
    _field('max_compression_fraction','允许最大压缩应变','%',.001,.1,.1,'管路与容腔','|(p−p_initial)/K|；超限停止，避免线性容腔外推。',100),
    _field('inlet_loss_Pa','泵支路额定管损','MPa',0,2e8,.01,'管路与容腔','R=Δp_ref/Q_ref²，阻力符号始终阻碍流动。',1e-6),
    _field('outlet_loss_Pa','出口支路额定管损','MPa',0,2e8,.01,'管路与容腔','不含执行阀损失。',1e-6),
    _field('valve_loss_Pa','阀门全开额定压降','MPa',100,2e8,.01,'阀门执行机构','按有效面积平方反比计算阀损，适用非汽蚀单相模型。',1e-6),
    _field('initial_valve_opening','初始实际开度','%',0,1,1,'阀门执行机构','归一化行程；首段命令与该值一致。',100),
    _field('final_valve_opening','末端指令开度','%',0,1,1,'阀门执行机构','指令与实际开度分别绘制。',100),
    _field('valve_tau_s','执行器时间常数','s',.001,5,.01,'阀门执行机构','一阶滞后时间常数，是待标定的执行器参数。'),
    _field('valve_rate_per_s','最大开闭速率','%/s',.01,100,10,'阀门执行机构','限制实际开度变化速率。',100),
    _field('leak_fraction','关闭时面积 / 全开面积','%',.005,.2,.1,'阀门执行机构','保留有限泄漏面积；该模型不是理想完全封闭阀。',100),
]


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


@lru_cache(maxsize=128)
def _properties(fluid, temperature, pressure):
    from CoolProp.CoolProp import PropsSI, PhaseSI
    import CoolProp
    phase=PhaseSI('T',temperature,'P',pressure,fluid)
    if phase not in ('liquid','supercritical_liquid'):
        raise ValueError(f'{fluid} 物性参考状态不是受支持的单相液体（{phase}），本页不适用。')
    rho=PropsSI('Dmass','T',temperature,'P',pressure,fluid)
    compressibility=PropsSI('ISOTHERMAL_COMPRESSIBILITY','T',temperature,'P',pressure,fluid)
    vapor=PropsSI('P','T',temperature,'Q',0,fluid)
    if not all(finite(v) and v>0 for v in (rho,compressibility,vapor)):
        raise ValueError(f'{fluid} 无法得到有效单相密度、体积模量或饱和压力。')
    return dict(rho_kg_m3=rho,bulk_modulus_Pa=1/compressibility,
                pressure_floor_Pa=vapor+max(100.0,.001*vapor),
                property_evidence=dict(backend='CoolProp HEOS',version=CoolProp.__version__,
                    fluid=fluid,temperature_K=temperature,pressure_Pa=pressure,phase=phase,
                    saturation_pressure_Pa=vapor,basis='来源入口温度与容腔初压处计算；全回路使用同一冻结密度和体积模量，单相小压缩近似。'))


def _calculation(source):
    if source.get('status')!='succeeded':
        raise ValueError('请选择已完成的稳态设计记录，以取得泵与工质标度。')
    c=(source.get('result') or {}).get('calculation') or {}
    if not all(isinstance(c.get(k),dict) for k in ('inputs','summary','components')):
        raise ValueError('来源记录缺少泵或工质数据，请重新运行稳态设计点。')
    return c


def _circuits(source):
    c=_calculation(source); inp=c['inputs']; circuits=[]
    if inp.get('propellant_pair') not in ('lox_methane','lox_kerosene'):
        raise ValueError('本研究台仅支持液氧/甲烷或液氧/正十二烷代理工质。')
    fuel='n-Dodecane' if inp['propellant_pair']=='lox_kerosene' else 'Methane'
    for side,fluid,label,tempkey in [('ox','Oxygen','液氧供给','ox_inlet_temp'),('fuel',fuel,'燃料供给','fuel_inlet_temp')]:
        out=(c['components'].get(side+'_pump') or {}).get('outputs') or {}
        def required(key):
            value=out.get(key)
            if not finite(value) or value<=0:
                raise ValueError(f'{label} 缺少有效 {key} 标度。')
            return float(value)
        pin=required('p_in_MPa')*1e6; dp=required('delta_p_Pa'); rpm=required('nr_rpm')
        temp=out.get('T_in_K',inp.get(tempkey))
        if not finite(temp) or temp<=0: raise ValueError(f'{label} 缺少有效入口温度。')
        inlet_props=_properties(fluid,float(temp),pin)
        props=deepcopy(_properties(fluid,float(temp),pin+.95*dp))
        mdot=c['summary'].get(f'mdot_{side}_kg_s')
        if not finite(mdot) or mdot<=0: raise ValueError(f'{label} 来源质量流量无效。')
        eta=out.get('eta',inp.get('pump_efficiency_'+side))
        if not finite(eta) or not .05<=eta<1: raise ValueError(f'{label} 来源泵效率必须在 0.05–1（不含1）之间。')
        q=mdot/inlet_props['rho_kg_m3']; diameter=math.sqrt(4*q/(10*math.pi))
        circuits.append(dict(id=side,label=label,**props,tank_pressure_Pa=pin,
            outlet_pressure_Pa=pin+.25*dp,initial_manifold_pressure_Pa=pin+.95*dp,
            volume_m3=max(1e-5,q*.02),max_compression_fraction=.05,
            pump_head_Pa=dp,pump_flow_m3_s=q,pump_speed_rpm=rpm,pump_efficiency=eta,
            source_mdot_kg_s=mdot,source_inlet_rho_kg_m3=inlet_props['rho_kg_m3'],
            inlet_length_m=2.0,inlet_diameter_m=diameter,inlet_loss_Pa=.05*dp,
            outlet_length_m=2.0,outlet_diameter_m=diameter,outlet_loss_Pa=.07*dp,valve_loss_Pa=.63*dp,
            valve_tau_s=.05,valve_rate_per_s=2.0,leak_fraction=.01,
            initial_valve_opening=1.0,final_valve_opening=.8,
            initial_speed_ratio=1.0,final_speed_ratio=.8,
            initial_pump_flow_ratio=1.0,initial_outlet_flow_ratio=1.0,
            pump_map=dict(kind='analytic_demo',slope=min(.1,.5*(1/eta-1)),
                source='解析演示特性，非实测离心泵图',points=[],max_speed_ratio=1.25,max_flow_ratio=2.0)))
    return circuits


def feed_setup(source):
    issues=[]; circuits=[]
    defaults=dict(duration_s=2.0,sample_count=301,command_time_s=.2,command_ramp_s=.5,rtol=1e-6,circuits=[])
    try:
        circuits=_circuits(source); defaults['circuits']=circuits
        from feed_network import FeedNetworkModel
        FeedNetworkModel(defaults)
    except (ValueError,TypeError,KeyError,OverflowError) as exc:
        issues.append(str(exc))
    return dict(ready=not issues,issues=issues,defaults=defaults,parameters=PARAMETERS,
        circuit_parameters=CIRCUIT_PARAMETERS,pump_map_contract=MAP_CONTRACT,
        source_summary=dict(job_id=source.get('id'),revision=source.get('revision'),
            purpose='独立供给子系统；来源仅提供物性和泵标度，不继承整机热态初值。'),
        source_constraints=deepcopy((source.get('result') or {}).get('assessment') or {}),
        limitations=LIMITATIONS)


def _validate_number(value,parameter):
    try:
        valid=finite(value) and parameter['min']<=value<=parameter['max']
    except (OverflowError,ValueError): valid=False
    if not valid: raise ValueError(f'{parameter["label"]} 超出允许范围或不是有限数值。')
    return float(value)


def normalize_request(value,source):
    if not isinstance(value,dict) or set(value)-({'source_job_id','circuits'}|{p['key'] for p in PARAMETERS}):
        raise ValueError('供给网络请求包含未支持的字段。')
    if value.get('source_job_id')!=source.get('id'):
        raise ValueError('来源设计记录不匹配。')
    config=feed_setup(source)
    if not config['ready']: raise ValueError('；'.join(config['issues']))
    normalized={'source_job_id':source['id'],**deepcopy(config['defaults'])}
    for p in PARAMETERS:
        x=_validate_number(value.get(p['key'],normalized[p['key']]),p)
        if p['key']=='sample_count':
            if x!=int(x): raise ValueError('输出采样点数必须为整数。')
            x=int(x)
        normalized[p['key']]=x
    if normalized['command_time_s']+normalized['command_ramp_s']>=normalized['duration_s']:
        raise ValueError('命令变化须在积分结束之前完成。')
    requested=value.get('circuits',normalized['circuits'])
    if not isinstance(requested,list) or not 1<=len(requested)<=2:
        raise ValueError('请选择 1–2 条供给回路。')
    available={c['id']:c for c in config['defaults']['circuits']}; chosen=[]; seen=set()
    editable={p['key'] for p in CIRCUIT_PARAMETERS}
    for item in requested:
        if not isinstance(item,dict) or item.get('id') not in available or item['id'] in seen:
            raise ValueError('供给回路标识无效或重复。')
        base=deepcopy(available[item['id']]); seen.add(item['id'])
        if set(item)-set(base): raise ValueError('回路包含未知参数。')
        # Re-evaluate the frozen liquid reference when the initial pressure changes.
        # Browsers may echo either the displayed defaults or the already-normalized snapshot.
        original=deepcopy(base)
        for p in CIRCUIT_PARAMETERS:
            base[p['key']]=_validate_number(item.get(p['key'],base[p['key']]),p)
        evidence=original['property_evidence']
        base.update(deepcopy(_properties(evidence['fluid'],evidence['temperature_K'],base['initial_manifold_pressure_Pa'])))
        for key in set(item)-editable-{'pump_map'}:
            if item[key]!=base[key] and item[key]!=original[key]:
                raise ValueError(f'{key} 是来源标度或物性，不可由请求改写。')
        mapping=deepcopy(item.get('pump_map',base['pump_map']))
        if not isinstance(mapping,dict) or set(mapping)!=set(base['pump_map']):
            raise ValueError('泵图字段必须包含 kind、slope、source、points、max_speed_ratio、max_flow_ratio。')
        if not isinstance(mapping['source'],str) or not mapping['source'].strip() or len(mapping['source'])>500:
            raise ValueError('请填写不超过 500 字的泵图来源说明。')
        if not isinstance(mapping['points'],list) or len(mapping['points'])>721:
            raise ValueError('Suter 泵图最多允许 721 个角度点。')
        for key,hi in [('max_speed_ratio',5.),('max_flow_ratio',10.)]:
            if not finite(mapping[key]) or not 0<mapping[key]<=hi:
                raise ValueError(f'泵图 {key} 须为正值且不超过 {hi}。')
        for point in mapping['points']:
            if not isinstance(point,dict) or set(point)!={'theta_deg','head','torque'}:
                raise ValueError('每行泵图必须仅包含 theta_deg、head、torque。')
            if any(not finite(point[k]) or abs(point[k])>1e4 for k in ('head','torque')):
                raise ValueError('泵图无量纲系数须为有限数值且绝对值不超过 10000。')
        if mapping['kind']=='analytic_demo':
            mapping['source']='解析演示特性，非实测离心泵图'
            if mapping['points']: raise ValueError('解析演示特性不接受混入的表格点；请选择 Suter 表模式。')
        base['pump_map']=mapping; chosen.append(base)
    normalized['circuits']=chosen
    from feed_network import FeedNetworkModel
    FeedNetworkModel(normalized)  # all map, pressure and actuator checks also apply to direct API callers
    json.dumps(normalized,allow_nan=False)
    return normalized


def calculate_feed_network(request,source,progress=None):
    from feed_network import FeedNetworkModel
    from version import APP_VERSION
    request=normalize_request(request,source); model=FeedNetworkModel(request)
    result=dict(schema_version=1,application_version=APP_VERSION,analysis_type='feed_network_dynamic',
        source=dict(job_id=source['id'],project_id=source['project_id'],revision=source['revision'],
            result_sha256=fingerprint(source['result'])),request=deepcopy(request),
        environment=science_environment(source['result']['calculation']['inputs'].get('propellant_pair','lox_methane')),
        source_constraints=deepcopy(source['result'].get('assessment') or {}),
        complete=False,samples=[],limitations=LIMITATIONS,pump_map_contract=MAP_CONTRACT,
        model_description=deepcopy(model.model_description),audits={'status':'pending'})
    def publish(sample):
        result['samples'].append(sample); count=len(result['samples'])
        if progress and (count==1 or count%10==0 or count==request['sample_count']):
            progress(dict(completed=count,iteration=count,maximum=request['sample_count'],
                time_s=sample['time_s'],phase='积分供给管路、容腔与执行器'),deepcopy(result))
    _,metadata=model.integrate(publish)
    result.update(metadata)
    missing=[]; result=plain(result,missing); result['nonfinite_fields']=missing
    json.dumps(result,ensure_ascii=False,allow_nan=False)
    if progress: progress(dict(completed=len(result['samples']),maximum=request['sample_count'],
        phase='供给网络积分完成' if result['complete'] else '供给模型适用域终止',
        time_s=result['samples'][-1]['time_s'] if result['samples'] else 0),deepcopy(result))
    return result
