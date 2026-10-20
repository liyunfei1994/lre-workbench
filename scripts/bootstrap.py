"""Create the isolated Windows runtime and install dependencies."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import venv
import uuid

ROOT = Path(__file__).resolve().parent.parent


def probe_interpreter(python: Path, environment: Path | None = None) -> bool:
    """Do not confuse an existing executable with a functioning local venv."""
    code = ("import sys,struct,json,sysconfig; print(json.dumps({'prefix':sys.prefix,"
            "'base':sys.base_prefix,'version':list(sys.version_info[:2]),"
            "'bits':struct.calcsize('P')*8,"
            "'free_threaded':bool(sysconfig.get_config_var('Py_GIL_DISABLED'))}))")
    try:
        if os.name == "nt":
            # Avoid Windows' modal loader error for an obviously damaged file.
            with python.open("rb") as stream:
                if stream.read(2) != b"MZ":
                    return False
        result = subprocess.run([str(python), "-I", "-c", code], capture_output=True,
                                text=True, encoding="utf-8", timeout=20, check=True)
        info = json.loads(result.stdout)
        if (tuple(info["version"]) != (3, 14) or info["bits"] != 64
                or info["free_threaded"]):
            return False
        return environment is None or (
            Path(info["prefix"]).resolve() == environment.resolve()
            and info["prefix"] != info["base"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return False


def prepare_environment(environment: Path, root: Path = ROOT) -> Path:
    """Retain a broken/copied environment before rebuilding it on this machine."""
    root = root.resolve()
    environment = environment.absolute()
    # Never move an arbitrary directory, a symlink, or a Windows junction.
    if environment.name != ".venv" or environment.parent.resolve() != root:
        raise RuntimeError("只允许修复当前项目中的 .venv。")
    if environment.resolve() != root / ".venv":
        raise RuntimeError(".venv 指向项目外部，请改为普通目录后再运行。")
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if probe_interpreter(python, environment):
        return python
    if Path(sys.prefix).resolve() == environment.resolve():
        raise RuntimeError("不能从待修复环境内部重建自身。请使用本机 Python 重新运行安装。")
    if environment.exists():
        backup = root / f".venv.backup-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
        environment.rename(backup)
        print(f"原环境已保留：{backup.name}", flush=True)
    print("正在建立本机独立 Python 环境…", flush=True)
    venv.EnvBuilder(with_pip=True).create(environment)
    if not probe_interpreter(python, environment):
        raise RuntimeError("新环境未通过解释器检查。")
    return python


@contextmanager
def setup_lock(root: Path = ROOT):
    """Serialize environment repair, while allowing multiple GUI instances."""
    cache = root / ".cache"
    cache.mkdir(exist_ok=True)
    with (cache / "setup.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("另一个启动窗口正在检查 / 安装环境，请稍后重试。") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def probe_runtime(python: Path, root: Path = ROOT, mode: str = "full") -> dict:
    code = ("import sys,json; sys.path.insert(0,sys.argv[1]); "
            "from runtime_environment import environment_report; "
            "print(json.dumps(environment_report(sys.argv[2],sys.argv[3])))")
    result = subprocess.run([str(python), "-c", code, str(root / "src"), str(root), mode],
                            capture_output=True, text=True, encoding="utf-8", check=True, timeout=90)
    return json.loads(result.stdout)


def ensure_runtime(python: Path, environment: Path, mode: str = "full", root: Path = ROOT) -> dict:
    """Import and version checks allow offline startup even without a marker."""
    report = probe_runtime(python, root, mode)
    if not report["ready"]:
        print("正在安装 / 修复完整科学计算环境，首次安装需要联网…" if mode == "full"
              else "正在安装 / 修复基础界面环境，首次安装需要联网…", flush=True)
        pip = subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True)
        if pip.returncode:
            subprocess.run([str(python), "-m", "ensurepip", "--upgrade"], check=True)
        command = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:",
                   "-r", str(root / "requirements.txt")]
        if mode == "full":
            command.extend(["-r", str(root / "requirements-science.txt")])
        if (root / "constraints.txt").exists():
            command.extend(["-c", str(root / "constraints.txt")])
        subprocess.run(command, check=True)
        report = probe_runtime(python, root, mode)
    if not report["ready"]:
        failures = [f"{name}: {entry['error'] or entry['version']}（要求 {entry['expected_version']}）"
                    for name, entry in report["dependencies"].items()
                    if entry["required"] and not (entry["available"] and entry["matches_expected"])]
        if mode == "full":
            failures.extend(f"{name}: {entry['error'] or '科学计算功能检查未通过'}"
                            for name, entry in report["functional_checks"].items() if not entry["ready"])
        failures.extend(report.get("lock_problems", []))
        raise RuntimeError("依赖安装后仍未通过导入与版本检查：\n" + "\n".join(failures))
    marker = environment / "ffsc-runtime.json"
    marker.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true", help="Install only, without opening the GUI")
    parser.add_argument("--diagnose", action="store_true", help="Write a report without installing application dependencies")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--science", action="store_true", help="Compatibility option: the complete scientific environment is now the default")
    modes.add_argument("--basic", action="store_true", help="Explicit interface-only setup; missing chemistry/property libraries use approximate formulas")
    args = parser.parse_args()
    if os.name != "nt":
        print("当前版本只提供 Windows 启动与安装入口。", flush=True)
        return 1
    if not probe_interpreter(Path(sys.executable)):
        print("需要标准版 64 位 CPython 3.14（不支持 free-threaded 构建）。", flush=True)
        return 1
    environment = ROOT / ".venv"
    python = environment / "Scripts/python.exe"
    try:
        if args.diagnose:
            from diagnose import write_diagnostics
            path, ready = write_diagnostics(ROOT)
            print(f"诊断报告：{path}", flush=True)
            return 0 if ready else 1
        with setup_lock():
            python = prepare_environment(environment)
            ensure_runtime(python, environment, "basic" if args.basic else "full")
        print("基础界面环境就绪；缺少 Cantera / CoolProp 时使用经验公式，结果不能与完整科学环境视为一致。"
              if args.basic else "火箭动力工作台完整科学计算环境就绪（Cantera / CoolProp / FEM 已验证）。", flush=True)
        if args.setup:
            return 0
        return subprocess.call([str(python), str(ROOT / "run_app.py")], cwd=ROOT)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"安装 / 启动失败：{exc}\n请检查网络和 Python 环境，重新运行本脚本。", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
