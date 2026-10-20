"""Portable application entry point, independent of current working directory."""
from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from version import APP_NAME, APP_FORMAL_NAME, APP_ENGLISH_NAME, APP_VERSION


def log_directory():
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    path = base / "lre-workbench" / APP_VERSION / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def main():
    # pythonw has no console streams. Libraries must still be able to write.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    log_file = log_directory() / "workbench.log"
    handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    try:
        from windows_identity import configure_process_identity, current_process_identity
        configure_process_identity()
        if sys.platform == "win32":
            logging.info("Windows AppUserModelID: %s", current_process_identity())
        if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or os.environ.get("QT_QPA_PLATFORM") == "offscreen"):
            raise RuntimeError("未检测到 Linux 桌面会话。请在桌面终端启动程序。")
        from PySide6.QtCore import qInstallMessageHandler
        from PySide6.QtGui import QFont, QFontDatabase, QIcon
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication(sys.argv)
        app.setApplicationName(APP_NAME)
        app.setApplicationDisplayName(APP_FORMAL_NAME)
        app.setApplicationVersion(APP_VERSION)
        app.setOrganizationName("LRE Workbench")
        icon_path = ROOT / "assets" / ("app.ico" if sys.platform == "win32" else "app.svg")
        app.setWindowIcon(QIcon(str(icon_path)))
        app.setStyle("Fusion")
        from workbench_fonts import configure_fonts
        configure_fonts(app)

        def qt_message(mode, context, message):
            if getattr(context, "category", "") == "qt.svg" and "is undefined" in message:
                return
            logging.info("Qt: %s", message)
        qInstallMessageHandler(qt_message)

        def exception_hook(kind, value, tb):
            logging.error("Unhandled exception", exc_info=(kind, value, tb))
            QMessageBox.critical(None, "操作失败", f"{value}\n\n诊断日志：{log_file}")
        sys.excepthook = exception_hook
        from workbench import WorkbenchWindow
        # Legacy plotting modules configure rcParams at import; reapply our
        # shared font after all modules have loaded.
        configure_fonts(app)
        window = WorkbenchWindow()
        window.show()
        window.offer_case_recovery()
        logging.info("%s window icon: %s; native sizes: %s", APP_ENGLISH_NAME, icon_path, window.windowIcon().availableSizes())
        logging.info("Started %s on %s", sys.executable, sys.platform)
        return app.exec()
    except Exception as exc:
        logging.exception("Application startup failed")
        message = f"{APP_NAME} 启动失败：{exc}\n\n请重新运行安装脚本。日志：{log_file}"
        print(message, file=sys.stderr)
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, f"{APP_NAME} 启动失败", 0x10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
