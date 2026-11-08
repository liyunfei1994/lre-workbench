"""Versioned missing-data manifests, not component physics or executable models.

Registration checks document shape, units and provenance declarations only. No
file is opened, no formula evaluated, and no declared data becomes hardware.
"""
from copy import deepcopy
import re

from lre_workbench.domain.analysis_contract import FIXED_HARDWARE_STEADY
from webapp.validation import fingerprint


# Field kinds describe the registration envelope. A reference is an artifact
# label plus a declared digest, not a path that this service will dereference.
_ITEMS = (
    ("pump_characteristics", "泵特性", "两侧泵须分别登记部件身份、数据版本及工作域；设计点和常效率不构成特性图。", (
        ("component_ids", "部件身份与所属轴", "1", "text"),
        ("characteristics", "流量、转速、压升与效率数据", "SI", "reference"),
        ("inlet_domain", "入口状态及适用范围", "SI", "reference"))),
    ("turbine_characteristics", "涡轮特性", "需区分实际量与折合量，明确气体状态与折合基准；不采用设计点单点拟合作为实测特性。", (
        ("component_ids", "部件身份与所属轴", "1", "text"),
        ("characteristics", "流通能力、压比、转速与效率数据", "SI", "reference"),
        ("correction_basis", "折合量、参考状态及气体组成说明", "1", "text"))),
    ("valve_characteristics", "阀门硬件与开度定义", "需要实际阀门 ID、安装端口、硬件特性和限位；分流比例不能代替阀位。", (
        ("identities_and_ports", "阀门 ID 与上下游端口", "1", "reference"),
        ("characteristics", "开度与流通能力数据及其单位定义", "SI", "reference"),
        ("opening_limits", "开度定义、允许范围和泄漏状态", "1", "reference"),
        ("operating_limits", "流体兼容性与压力、温度适用范围", "SI", "reference"))),
    ("feed_network", "供给边界与管路", "现有四支路集总阻力仅是来源设计点证据；还需实际连接与前置供给部件定义。", (
        ("ports_and_connections", "端口、流向与连接关系", "1", "reference"),
        ("resistance_data", "管路阻力及适用状态", "SI", "reference"),
        ("supply_boundaries", "各推进剂供给端口的压力和热状态定义", "SI", "reference"))),
    ("injectors", "喷注器流通数据", "主室热气喷注器与预燃室喷注器应分别登记；设计压降预算不等于固定流通能力。", (
        ("identities_and_ports", "喷注器身份、端口和流体", "1", "reference"),
        ("characteristics", "压力、流量和入口状态数据", "SI", "reference"),
        ("geometry_revision", "冻结几何版本", "1", "text"))),
    ("shaft_connections", "轴系连接与损失", "须明确部件连接、转速定义和机械损失数据；来源选型转速不作为规定转速。", (
        ("connections", "轴 ID、部件关联及转向约定", "1", "reference"),
        ("losses", "机械损失与适用范围", "SI", "reference"),
        ("speed_basis", "转速量及单位换算约定", "1", "text"))),
    ("thermal_fluid_properties", "流体状态与物性接口", "统一端口状态、焓基准与组分定义，避免把不同参考态或位置的量混用。", (
        ("property_source", "物性来源、版本及适用域", "1", "reference"),
        ("enthalpy_reference", "焓参考态与单位", "1", "text"),
        ("composition_basis", "组分、相态与质量/摩尔分数约定", "1", "text"))),
    ("combustion_mixing", "燃烧与混合模型接口", "温度是模型输出；不得登记维持设计温度的反求约束来替代固定硬件模型。", (
        ("model_reference", "燃烧与混合模型来源、假设和适用范围", "1", "reference"),
        ("port_state_definition", "入口焓、压力、组分与输出状态定义", "SI", "reference"))),
    ("cooling", "冷却通道与热边界", "区分冻结通道证据和热量/压降预算；模型必须说明热流方向、材料与适用范围。", (
        ("geometry_revision", "通道、壁体和材料版本", "1", "reference"),
        ("thermal_hydraulic_model", "固定通道模型及验证依据", "1", "reference"),
        ("heat_sign_convention", "热流方向和能量参考态", "1", "text"))),
    ("nozzle", "固定喷管模型接口", "已有喉部面积和面积比可追溯；仍须显式绑定使用冻结几何的模型，禁止重新定尺寸。", (
        ("geometry_revision", "喷管几何版本", "1", "reference"),
        ("model_reference", "固定几何模型及适用域", "1", "reference"))),
    ("constraints", "运行约束依据", "限值是求解后的独立检查项；缺少依据时不能将数值收敛报告为运行通过。", (
        ("limits", "温度、压力、转速等限制与依据", "SI", "reference"),
        ("outside_domain_policy", "超出部件数据工作域时的处理规则", "1", "text"))),
)

CONVENTIONS = {
    "units": "端口值使用显式 SI 单位；特性表须逐列声明单位，SI 不是对整张表的单位认证。",
    "pressure": "每个端口声明静压或总压及绝压基准。",
    "energy": "焓参考态、组分基准、热流和轴功符号必须显式一致。",
    "identity": "部件 ID、几何/材料版本、数据来源、修订版本及适用范围必须可追溯。",
    "missing": "null 表示待补，不能用零、默认效率、演示曲线或设计目标补位。",
    "controls": "阀门尺寸和特性属于硬件；阀位属于工况。控制目标需另行定义执行机构。",
}


def model_data_manifest(hardware):
    """Return detached requirements and clearly limited saved-design evidence."""
    items = []
    payload = hardware.get("payload", {}) if isinstance(hardware, dict) else {}
    for key, label, detail, fields in _ITEMS:
        evidence = []
        if key == "feed_network" and payload.get("feed"):
            evidence.append({"source_path": "hardware.payload.feed",
                             "description": "来源设计点集总阻力快照；不含真实阀位与机组特性。"})
        if key == "cooling" and payload.get("cooling"):
            evidence.append({"source_path": "hardware.payload.cooling",
                             "description": "来源冷却定义；须区分实际几何与预算模式。"})
        if key == "nozzle" and payload.get("nozzle"):
            evidence.append({"source_path": "hardware.payload.nozzle",
                             "description": "来源喷管几何；不表示部件模型已接入。"})
        items.append({"id": key, "label": label, "kind": "model_data_requirement",
                      "status": "design_evidence_only" if evidence else "missing",
                      "detail": detail, "evidence": evidence,
                      "fields": [{"key": name, "label": title, "unit": unit, "kind": kind,
                                  "value": None, "status": "missing"}
                                 for name, title, unit, kind in fields]})
    return {"schema_version": 1, "status": "missing_data", "ready": False,
            "conventions": deepcopy(CONVENTIONS), "items": items,
            "assembly": {"status": "not_connected", "unknown_count": None, "equation_count": None,
                         "detail": "尚未组装发动机方程；未知量/方程数、独立性与约束一致性均未验证。"}}


def data_template(record):
    """Export a task-bound declaration draft. Historical tasks are not migrated."""
    task = record.get("task", {})
    manifest = task.get("model_data")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("该历史任务未保存待补数据接口；请从来源设计记录建立新任务。")
    return {"schema_version": 1, "purpose": "fixed_steady_data_registration",
            "binding": {"task_id": record["id"], "task_sha256": fingerprint(task),
                        "hardware_id": task["hardware"]["id"],
                        "source_result_sha256": task["source"]["result_sha256"]},
            "components": [{"id": item["id"], "revision": None, "source": None,
                            "validity": None,
                            "fields": [{"key": field["key"], "unit": field["unit"],
                                        "kind": field["kind"], "value": None}
                                       for field in item["fields"]]}
                           for item in manifest["items"]]}


def _object(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{label} 字段缺失或含未知字段。")


def _text(value, path):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(f"{path} 必须是非空文本（不超过 4096 字符）。")


def _reference(value, path):
    _object(value, {"reference", "sha256"}, path)
    _text(value["reference"], path + ".reference")
    if not isinstance(value["sha256"], str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value["sha256"]):
        raise ValueError(f"{path}.sha256 必须是 64 位十六进制摘要。")


def validate_data_package(value, record):
    """Validate a draft envelope, never import models or unlock execution.

    A complete declaration still requires artifact/content verification and a
    server-owned adapter. This endpoint does not read a reference or URL.
    """
    expected = data_template(record)
    _object(value, expected, "数据登记包")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("不支持的数据登记包版本。")
    if value["purpose"] != expected["purpose"] or value["binding"] != expected["binding"]:
        raise ValueError("数据登记包用途或任务/硬件/来源绑定不匹配。")
    components = value["components"]
    if not isinstance(components, list) or len(components) != len(expected["components"]):
        raise ValueError("部件登记项缺失或多余。")
    indexed = {}
    for component in components:
        _object(component, {"id", "revision", "source", "validity", "fields"}, "部件登记项")
        key = component["id"]
        if not isinstance(key, str) or key in indexed:
            raise ValueError("部件 ID 无效或重复。")
        indexed[key] = component
    if set(indexed) != {item["id"] for item in expected["components"]}:
        raise ValueError("部件 ID 与任务接口不一致。")
    missing = []
    for required in expected["components"]:
        component = indexed[required["id"]]
        prefix = component["id"]
        for key in ("revision", "source", "validity"):
            if component[key] is None:
                missing.append(prefix + "." + key)
            elif key == "source":
                _reference(component[key], prefix + ".source")
            else:
                _text(component[key], prefix + "." + key)
        fields = component["fields"]
        if not isinstance(fields, list) or len(fields) != len(required["fields"]):
            raise ValueError(f"{prefix} 字段列表不完整。")
        mapped = {}
        for field in fields:
            _object(field, {"key", "unit", "kind", "value"}, prefix + ".fields")
            if not isinstance(field["key"], str) or field["key"] in mapped:
                raise ValueError(f"{prefix} 字段键无效或重复。")
            mapped[field["key"]] = field
        if set(mapped) != {field["key"] for field in required["fields"]}:
            raise ValueError(f"{prefix} 含未知或缺失字段。")
        for wanted in required["fields"]:
            field = mapped[wanted["key"]]
            path = prefix + "." + field["key"]
            if field["unit"] != wanted["unit"] or field["kind"] != wanted["kind"]:
                raise ValueError(f"{path} 单位或字段类型不匹配。")
            if field["value"] is None:
                missing.append(path)
            elif wanted["kind"] == "reference":
                _reference(field["value"], path)
            else:
                _text(field["value"], path)
    return {"schema_version": 1, "scope": "data_declaration_only", "valid": True,
            "status": "incomplete" if missing else "declared_unverified",
            "complete": not missing, "missing": missing, "can_run": False, "adopted": False,
            "binding": deepcopy(expected["binding"]),
            "limitations": ["仅检查登记包结构、单位标签、摘要格式和缺项；未读取或验证引用数据。",
                            "未修改硬件、任务或运行条件；完整登记不代表模型已接入或已验证。"]}
