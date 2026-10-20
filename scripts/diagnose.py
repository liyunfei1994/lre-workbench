"""Produce support information without loading user schemes or executing adapters."""
from __future__ import annotations
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


def write_diagnostics(root: Path) -> tuple[Path, bool]:
    from bootstrap import probe_interpreter, probe_runtime
    sys.path.insert(0, str(root / "src"))
    from version import APP_VERSION
    python = root / ".venv" / "Scripts" / "python.exe"
    report = {"application_version": APP_VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
              "project_directory": str(root), "interpreter": str(python),
              "interpreter_works": probe_interpreter(python, root / ".venv"),
              "ready": False, "runtime": None, "git": {}}
    if report["interpreter_works"]:
        try:
            report["runtime"] = probe_runtime(python, root)
            report["ready"] = report["runtime"]["ready"]
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            report["error"] = str(exc)
    else:
        report["error"] = "虚拟环境缺失、已搬迁或依赖的基础 Python 不可用。请运行 setup_windows.bat。"
    for name, arguments in (("commit", ["rev-parse", "HEAD"]),
                            ("changes", ["status", "--short", "--untracked-files=no"])):
        try:
            result = subprocess.run(["git", *arguments], cwd=root, capture_output=True,
                                    text=True, encoding="utf-8", errors="replace", timeout=10)
            report["git"][name] = result.stdout.strip() if result.returncode == 0 else "unavailable"
        except (OSError, subprocess.SubprocessError):
            report["git"][name] = "unavailable"
    output = root / "diagnostics"
    output.mkdir(exist_ok=True)
    path = output / f"environment-{datetime.now():%Y%m%d-%H%M%S-%f}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, report["ready"]
