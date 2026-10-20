"""Load an OFL font so desktop labels render consistently on both systems."""
from pathlib import Path
from PySide6.QtGui import QFont, QFontDatabase


def configure_fonts(app):
    font_path = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NotoSansSC.ttf"
    if font_path.exists():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        if families:
            app.setFont(QFont(families[0], 10))
            # Matplotlib needs a filesystem font, not Qt's registration.
            from matplotlib import font_manager, rcParams
            font_manager.fontManager.addfont(str(font_path))
            rcParams["font.sans-serif"] = [font_manager.FontProperties(fname=str(font_path)).get_name(), "DejaVu Sans"]
            rcParams["axes.unicode_minus"] = False
            return families[0]
    available = set(QFontDatabase.families())
    for family in ["Noto Sans CJK SC", "Microsoft YaHei UI", "Microsoft YaHei", "WenQuanYi Micro Hei", "DejaVu Sans"]:
        if family in available:
            app.setFont(QFont(family, 10))
            return family
    return app.font().family()
