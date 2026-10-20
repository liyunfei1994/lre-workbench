"""Protocol example only. It contains no CFD solver and never claims otherwise."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description="LRE CFD adapter protocol example; no real CFD solver")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--manifest")
    parser.add_argument("--result")
    args = parser.parse_args(argv)
    if args.describe:
        print(json.dumps({"protocol": "lre.cfd.result", "schema_version": 1,
                          "arguments": ["--manifest", "{manifest}", "--result", "{result}"],
                          "contains_cfd_solver": False, "self_test_source_kind": "test",
                          "warning": "This example cannot produce real CFD results."}, ensure_ascii=False))
        return 0
    if not args.self_test:
        print("未配置真实 CFD 求解器。本脚本仅演示适配器接口；请实现真实求解器适配器。"
              "只有显式 --self-test 才会生成 source_kind='test' 的固定接口测试数据。", file=sys.stderr)
        return 2
    if not args.manifest or not args.result:
        parser.error("--self-test requires --manifest and --result")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from cfd_exchange import load_cfd_job
    manifest_path = Path(args.manifest).expanduser().resolve()
    if manifest_path.name != "manifest.json":
        raise ValueError("The manifest argument must refer to the job's manifest.json")
    manifest = load_cfd_job(manifest_path.parent)
    result_path = Path(args.result).expanduser().resolve()
    expected = manifest_path.parent / manifest["files"]["result"]
    if result_path != expected or result_path.exists():
        raise ValueError("Refusing a result path outside the job or overwriting existing data")
    result = {
        "format": "lre.cfd.result", "schema_version": 1,
        "job_id": manifest["job_id"], "input_fingerprint": manifest["input_fingerprint"],
        "task": manifest["task"], "source_kind": "test",
        "solver": {"name": "LRE adapter interface self-test (NO CFD)", "version": "1"},
        "converged": True, "completed_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {"pressure_drop_Pa": {"value": 1234.5, "unit": "Pa"},
                    "heat_load_W": {"value": 2500.0, "unit": "W"}},
        "diagnostics": ["固定接口测试数据，不是 CFD 计算结果，不能用于工程反馈。"],
    }
    if "domain" in manifest:
        result["domain"] = manifest["domain"]
    with result_path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(f"已写入接口测试数据（source_kind=test，NO CFD）：{result_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(f"适配器接口检查失败：{exc}", file=sys.stderr)
        raise SystemExit(3)
