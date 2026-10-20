"""Check native Shell properties, including paths with Chinese text and spaces."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from windows_identity import create_workbench_shortcut, APP_USER_MODEL_ID


@unittest.skipUnless(sys.platform == "win32", "Windows taskbar properties")
class TaskbarTests(unittest.TestCase):
    def test_shortcut_properties_recognized_by_windows_shell(self):
        with tempfile.TemporaryDirectory(prefix="工作台 portable ") as directory:
            path = Path(directory) / "火箭动力 启动.lnk"
            root = (Path(directory) / "程序 files").resolve()
            (root / "assets").mkdir(parents=True)
            shutil.copy2(ROOT / "start_windows.vbs", root / "start_windows.vbs")
            shutil.copy2(ROOT / "assets" / "app.ico", root / "assets" / "app.ico")
            create_workbench_shortcut(path, root)
            self.assertTrue(path.is_file())
            environment = dict(os.environ, LRE_TEST_SHORTCUT=str(path))
            result = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                "[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding; "
                "$s=New-Object -ComObject Shell.Application; "
                "$f=$s.Namespace((Split-Path $env:LRE_TEST_SHORTCUT)); "
                "$i=$f.ParseName((Split-Path $env:LRE_TEST_SHORTCUT -Leaf)); "
                "$link=$i.GetLink; "
                "@{Target=$link.Path; Arguments=$link.Arguments; WorkingDirectory=$link.WorkingDirectory; "
                "Id=$i.ExtendedProperty('System.AppUserModel.ID'); "
                "Icon=$i.ExtendedProperty('System.AppUserModel.RelaunchIconResource')} | ConvertTo-Json"],
                env=environment, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0,
                             result.stderr.decode("utf-8", errors="replace"))
            values = json.loads(result.stdout.decode("utf-8-sig"))
            self.assertEqual(values["Id"], APP_USER_MODEL_ID)
            self.assertEqual(values["Icon"], str(root / "assets" / "app.ico") + ",0")
            self.assertEqual(Path(values["Target"]),
                             Path(os.environ["SystemRoot"]) / "System32" / "wscript.exe")
            self.assertEqual(values["Arguments"],
                             subprocess.list2cmdline([str(root / "start_windows.vbs")]))
            self.assertEqual(Path(values["WorkingDirectory"]), root)

    def test_native_window_icons_survive_screen_change_and_release_properties(self):
        # A separate process uses the Windows QPA plugin even when the main
        # desktop suite uses offscreen. The test window is never shown onscreen.
        code = r'''
import ctypes, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'src'))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from workbench import WorkbenchWindow
from windows_identity import read_taskbar_identity, APP_USER_MODEL_ID
app = QApplication([])
w = WorkbenchWindow()
w.setAttribute(Qt.WA_DontShowOnScreen, True)
w.show()
app.processEvents()
hwnd = int(w.winId())
user = ctypes.windll.user32
user.IsWindow.argtypes = [ctypes.c_void_p]
user.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t]
user.SendMessageW.restype = ctypes.c_ssize_t
assert user.IsWindow(hwnd), 'Expected a real Windows HWND'
for screen in app.screens():
    w.windowHandle().setScreen(screen)
    w._queue_taskbar_icon_refresh()
    app.processEvents()
    hwnd = int(w.winId())
    identity = read_taskbar_identity(hwnd=hwnd)
    assert identity['app_id'] == APP_USER_MODEL_ID
    assert identity['icon_resource'] == str(Path.cwd() / 'assets' / 'app.ico') + ',0'
    assert identity['display_name'] == '火箭动力工作台'
    assert 'start_windows.vbs' in identity['relaunch_command']
    assert all(user.SendMessageW(hwnd, 0x7f, kind, 0) for kind in (0, 1, 2)), 'Native icons missing'
    print(screen.name(), identity['app_id'])
w.close()
app.processEvents()
assert all(value is None for value in read_taskbar_identity(hwnd=hwnd).values())
'''
        environment = dict(os.environ, QT_QPA_PLATFORM="windows", PYTHONUTF8="1", PYTHONWARNINGS="ignore")
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=environment,
                                capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
