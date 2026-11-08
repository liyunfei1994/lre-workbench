"""Platform design scope; archived inputs remain readable without migration."""
import json

DESIGN_SCOPE = {
    "id": "ffsc_lox_methane",
    "cycle_type": "ffsc",
    "propellant_pair": "lox_methane",
    "label": "液氧甲烷全流量补燃循环",
    "history_read_only": True,
}
SCOPE_MESSAGE = "当前平台仅支持液氧甲烷全流量补燃循环（FFSC）设计。其他循环或推进剂的历史方案仅供查看与导出，请新建 FFSC 液氧甲烷方案。"


def supports_design(values):
    """Omitted identifiers keep the established EngineInputs defaults."""
    return isinstance(values, dict) and (
        values.get("cycle_type", "ffsc") == "ffsc"
        and values.get("propellant_pair", "lox_methane") == "lox_methane"
    )


def require_supported_design(values):
    if not supports_design(values):
        raise ValueError(SCOPE_MESSAGE)


def require_supported_source(source):
    """Check saved identities and topology before reusing an archived result."""
    inputs = source.get("inputs", {})
    require_supported_design(json.loads(inputs) if isinstance(inputs, str) else inputs)
    result = source.get("result")
    if isinstance(result, str):
        result = json.loads(result)
    if isinstance(result, dict) and "submitted_inputs" in result:
        require_supported_design(result["submitted_inputs"])
    calculation = result.get("calculation") if isinstance(result, dict) else None
    if isinstance(calculation, dict) and "inputs" in calculation:
        require_supported_design(calculation["inputs"])
    if isinstance(calculation, dict):
        # Some older records have no cycle identity. Their recorded equipment
        # must not acquire FFSC defaults merely because that identity is absent.
        keys = []
        for name in ("components", "states"):
            mapping = calculation.get(name)
            if isinstance(mapping, dict):
                keys.extend(mapping)
        nodes = calculation.get("topology_nodes")
        if isinstance(nodes, list):
            keys.extend(node.get("key") for node in nodes if isinstance(node, dict))
        edges = calculation.get("topology_edges")
        if isinstance(edges, list):
            keys.extend(edge.get(endpoint) for edge in edges if isinstance(edge, dict)
                        for endpoint in ("source", "target"))
        if any(isinstance(key, str) and (key.startswith("gg_") or key == "gas_generator")
               for key in keys):
            raise ValueError(SCOPE_MESSAGE)
