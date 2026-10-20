"""Create the isolated Windows runtime and install dependencies."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent.parent


def probe_runtime(python: Path, root: Path = ROOT, mode: str = "full") -> dict:
    code = ("import sys,json; sys.path.insert(0,sys.argv[1]); "
            "from runtime_environment import environment_report; "
            "print(json.dumps(environment_report(sys.argv[2],sys.argv[3])))")
    result = subprocess.run([str(python), "-c", code, str(root / "src"), str(root), mode],
                            capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(result.stdout)


def ensure_runtime(python: Path, environment: Path, mode: str = "full", root: Path = ROOT) -> dict:
    """Import and version checks allow offline startup even without a marker."""
    report = probe_runtime(python, root, mode)
    if not report["ready"]:
        print("正在安装 / 修复完整科学计算环境，首次安装需要联网…" if mode == "full"
              else "正在安装 / 修复基础界面环境，首次安装需要联网…", flush=True)
        command = [str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")]
        if mode == "full":
            command.extend(["-r", str(root / "requirements-science.txt")])
        subprocess.run(command, check=True)
        report = probe_runtime(python, root, mode)
    if not report["ready"]:
        failures = [f"{name}: {entry['error'] or entry['version']}（要求 {entry['expected_version']}）"
                    for name, entry in report["dependencies"].items()
                    if entry["required"] and not (entry["available"] and entry["matches_expected"])]
        if mode == "full":
            failures.extend(f"{name}: {entry['error'] or '科学计算功能检查未通过'}"
                            for name, entry in report["functional_checks"].items() if not entry["ready"])
        raise RuntimeError("依赖安装后仍未通过导入与版本检查：\n" + "\n".join(failures))
    marker = environment / "ffsc-runtime.json"
    marker.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true", help="Install only, without opening the GUI")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--science", action="store_true", help="Compatibility option: the complete scientific environment is now the default")
    modes.add_argument("--basic", action="store_true", help="Explicit interface-only setup; missing chemistry/property libraries use approximate formulas")
    args = parser.parse_args()
    if os.name != "nt":
        print("当前版本只提供 Windows 启动与安装入口。", flush=True)
        return 1
    if not (3, 10) <= sys.version_info[:2] <= (3, 12):
        print("需要 Python 3.10-3.12，推荐 Python 3.11。", flush=True)
        return 1
    environment = ROOT / ".venv"
    python = environment / "Scripts/python.exe"
    try:
        if not python.exists():
            print("正在建立火箭动力工作台独立 Python 环境…", flush=True)
            venv.EnvBuilder(with_pip=True).create(environment)
        # Venvs cannot be copied between operating systems; a broken/copied venv
        # is reported rather than deleted automatically.
        subprocess.run([str(python), "-c", "import sys; assert sys.prefix != sys.base_prefix"], check=True)
        ensure_runtime(python, environment, "basic" if args.basic else "full")
        print("基础界面环境就绪；缺少 Cantera / CoolProp 时使用经验公式，结果不能与完整科学环境视为一致。"
              if args.basic else "火箭动力工作台完整科学计算环境就绪（Cantera / CoolProp / FEM 已验证）。", flush=True)
        if args.setup:
            return 0
        return subprocess.call([str(python), str(ROOT / "run_app.py")], cwd=ROOT)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"安装 / 启动失败：{exc}\n请检查网络和 Python 环境，重新运行本脚本。", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
