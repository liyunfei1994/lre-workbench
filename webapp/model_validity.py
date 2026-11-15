"""Read-only withdrawal notices for known-invalid saved physical models."""
import json

BM_EOS = "Peng-Robinson-Boston-Mathias"
WITHDRAWN_BM_VERSION = "pr_boston_mathias_v1"
WITHDRAWN_BM_REASON = (
    "此记录使用已撤回的 PR–BM v1 物理模型：化学平衡压力项被重复计入，"
    "保存的组成、温度及整机性能不能作为有效设计结果。原始记录仅供追溯；"
    "禁止用于新的校核对比或设计交接，请使用修正模型重新运行设计点。"
)


def saved_model_validity(result):
    """Do not infer validity from convergence or rewrite historical evidence."""
    if isinstance(result, str):
        result = json.loads(result)
    evidence = []

    def visit(value, path):
        if isinstance(value, dict):
            formula = value.get("eos_model")
            version = formula.get("version") if isinstance(formula, dict) else formula
            eos = value.get("eos", value.get("candidate_eos"))
            if eos == BM_EOS and version == WITHDRAWN_BM_VERSION:
                evidence.append({"path": path, "eos": eos, "version": version,
                                 "eos_model_sha256": value.get("eos_model_sha256")})
            for key, item in value.items():
                if isinstance(item, (dict, list)):
                    visit(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, (dict, list)):
                    visit(item, f"{path}[{index}]")

    visit(result, "result")
    return {"schema_version": 1, "status": "invalid_superseded" if evidence else "not_listed_invalid",
            "reuse_blocked": bool(evidence), "reason": WITHDRAWN_BM_REASON if evidence else None,
            "evidence": evidence,
            "scope": "known_model_withdrawals_only_not_physical_validation"}


def require_reusable_model(source):
    state = saved_model_validity(source.get("result"))
    if state["reuse_blocked"]:
        raise ValueError(state["reason"])
