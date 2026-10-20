"""Render the actual Qt workspace for layout review (offscreen)."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=["ffsc", "gg"], default="ffsc")
    parser.add_argument("--no-solve", action="store_true")
    args = parser.parse_args()
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QMessageBox
    from workbench_fonts import configure_fonts
    app = QApplication([])
    app.setStyle("Fusion")
    configure_fonts(app)
    from workbench import WorkbenchWindow
    configure_fonts(app)
    window = WorkbenchWindow()
    window.show()
    app.processEvents()
    output = ROOT / "docs"
    output.mkdir(exist_ok=True)
    window.grab().save(str(output / "preview-home.png"))
    if not args.no_solve:
        window._apply_engine_preset("raptor3_ffsc" if args.case == "ffsc" else "tq12a_gg")
        errors = []
        QMessageBox.critical = lambda parent, title, message: errors.append(message)
        window.run_cycle()
        deadline = time.monotonic() + 120
        while window._busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        if window._busy or window.last_results is None:
            window._busy = False
            window.close()
            raise RuntimeError("Worker did not produce results: " + str(errors))
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        for page in ["overview", "topology", "states", "components", "geometry", "chamber_design", "materials"]:
            window.navigate(page)
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            window.grab().save(str(output / f"preview-{page}.png"))
        data = {"cycle": args.case, "residual": window.last_results.summary.get("solver_residual_norm"),
                "solver_warning": window.last_results.summary.get("solver_warning"),
                "warnings": window.last_results.warnings, "geometry_generated": window.last_geometry is not None}
        (output / f"validation-{args.case}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(data, ensure_ascii=False))
    window.resize(1100, 700)
    window.navigate("overview")
    app.processEvents()
    window.grab().save(str(output / "preview-compact.png"))
    window.close()


if __name__ == "__main__":
    main()
