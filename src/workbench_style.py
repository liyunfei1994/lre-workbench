"""A neutral engineering workspace. Shared by Windows and Linux."""

def stylesheet(size=14, font_family="Noto Sans SC"):
    return """
QWidget { color: #263544; font-size: SIZEpx; background: #f2f4f6;
  font-family: 'FONTFAMILY'; }
QMainWindow { background: #f2f4f6; }
QFrame#AppHeader { background: #ffffff; border-bottom: 1px solid #dce2e7; }
QFrame#AppHeader QLabel { background: transparent; }
QLabel#Brand { font-size: 20px; font-weight: 700; color: #172837; }
QLabel#BrandSub { font-size: 11px; color: #7c8995; }
QLabel#DocumentTitle { color: #5b6875; padding-left: 20px; }
QFrame#Navigation { background: #1e2b37; border: none; }
QFrame#Navigation QWidget { background: transparent; }
QFrame#Navigation QScrollArea, QFrame#Navigation QWidget#NavContents { background: transparent; border: none; }
QFrame#Navigation QLabel { background: transparent; color: #94a5b4; font-size: 11px; }
QFrame#Navigation QFrame#NavSection {
  background: #2a3a47; border: none; border-left: 2px solid #dba65e;
  border-radius: 3px; margin: 9px 8px 5px 8px;
}
QFrame#Navigation QLabel#NavSectionTitle {
  background: transparent; color: #f0f4f7; font-size: 13px; font-weight: 700;
}
QFrame#Navigation QLabel#NavSectionNumber {
  background: #354b5b; color: #efc183; font-size: 10px; font-weight: 600;
  border-radius: 3px; padding: 2px 4px;
}
QPushButton#NavButton { background: transparent; color: #c2ccd5; text-align: left;
  padding: 9px 12px; border: none; border-left: 3px solid transparent; border-radius: 0; }
QPushButton#NavButton:hover { background: #293a49; color: #ffffff; }
QPushButton#NavButton:checked { background: #30414f; color: #ffffff; border-left: 3px solid #eaa44b; }
QPushButton#NavButton:disabled { color: #778899; }
QFrame#ParameterPane { background: #fafbfc; border-right: 1px solid #dce2e7; }
QFrame#ParameterPane QWidget { background: transparent; }
QFrame#ParameterPane QLineEdit, QFrame#ParameterPane QComboBox { background: #ffffff; }
QLabel#PanelTitle { font-size: 17px; font-weight: 600; color: #263544; }
QLabel#MutedLabel, QLabel#KpiHint { color: #798692; font-size: SMALLpx; background: transparent; }
QLabel#PageTitle { font-size: 23px; font-weight: 600; background: transparent; }
QLabel#PageSubtitle { color: #798692; font-size: SMALLpx; background: transparent; }
QFrame#ResultBanner { background: #fff5e5; border: 1px solid #ecd6b2; border-radius: 4px; }
QFrame#ResultBanner QLabel { color: #856123; background: transparent; font-size: SMALLpx; }
QFrame#ResultBanner[kind="ok"] { background: #edf6f2; border-color: #cbe3d7; }
QFrame#ResultBanner[kind="ok"] QLabel { color: #36765c; }
QFrame#ResultBanner[kind="error"] { background: #fff0ef; border-color: #e9c7c3; }
QFrame#ResultBanner[kind="error"] QLabel { color: #a3534d; }
QPushButton, QToolButton { background: #ffffff; color: #354655; border: 1px solid #d4dce3;
  border-radius: 4px; padding: 6px 12px; font-weight: 500; }
QPushButton:hover, QToolButton:hover { background: #edf2f6; border-color: #9aafbf; }
QPushButton:pressed, QToolButton:pressed { background: #e2e9ef; }
QPushButton:disabled, QToolButton:disabled { color: #9ba7b0; background: #eef1f3; border-color: #e0e5e9; }
QPushButton#PrimaryButton { background: #d99940; border-color: #c68a36; color: #172837; font-weight: 600; }
QPushButton#PrimaryButton:hover { background: #e9ab53; }
QPushButton#PrimaryButton:disabled { background: #ead7bb; color: #9c8a70; border-color: #ead7bb; }
QPushButton#SecondaryButton { background: #ffffff; color: #405365; }
QToolButton#SectionToggle { text-align: left; background: #ffffff; border: none;
  border-bottom: 1px solid #e3e8ed; border-radius: 0; padding: 12px; font-weight: 600; }
QLineEdit, QLineEdit#ParamEdit, QComboBox, QSpinBox, QDoubleSpinBox {
  background: #ffffff; border: 1px solid #d4dce3; border-radius: 3px;
  padding: 5px 8px; min-height: 20px; selection-background-color: #527f9a; }
QLineEdit:focus, QComboBox:focus { border-color: #688da5; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url("CHEVRON"); width: 10px; height: 7px; }
QComboBox QAbstractItemView { background: #ffffff; selection-background-color: #e5edf3;
  color: #203747; selection-color: #263544; border: 1px solid #ccd6df; padding: 4px; }
QCheckBox { spacing: 7px; background: transparent; }
QCheckBox::indicator { width: 14px; height: 14px; }
QGroupBox { background: #ffffff; border: 1px solid #dde4ea; border-radius: 4px;
  margin-top: 16px; padding: 12px 9px 9px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #526777; }
QTabWidget::pane { border: 1px solid #dfe5ea; background: #ffffff; border-radius: 3px; }
QTabBar::tab { background: #edf0f3; color: #73818e; border: none;
  padding: 8px 12px; margin-right: 1px; border-bottom: 2px solid transparent; font-size: SMALLpx; }
QTabBar::tab:selected { background: #ffffff; color: #263544; border-bottom: 2px solid #d99940; }
QTabBar::tab:hover { background: #e2e9ef; }
QTableWidget, QTreeWidget { background: #ffffff; alternate-background-color: #f7f9fb;
  border: 1px solid #dfe5ea; gridline-color: #edf0f3; selection-background-color: #e3edf5;
  selection-color: #243a4c; }
QTableWidget::item, QTreeWidget::item { padding: 5px; }
QHeaderView::section { background: #f2f5f7; color: #566b7b; border: none;
  border-bottom: 1px solid #dce3e9; padding: 8px; font-size: SMALLpx; }
QTextEdit, QTextBrowser, QListWidget { background: #ffffff; border: 1px solid #dfe5ea; border-radius: 3px; }
QScrollArea { background: transparent; border: none; }
QFrame#ResultCard, QFrame#StatusStrip { background: #ffffff; border: 1px solid #dfe5ea; border-radius: 4px; }
QFrame#ResultCard QLabel, QFrame#StatusStrip QLabel { background: transparent; }
QFrame#KpiCard_ok, QFrame#KpiCard_neutral, QFrame#KpiCard_warning, QFrame#KpiCard_error {
  background: #ffffff; border: 1px solid #dfe5ea; border-radius: 4px; }
QLabel#KpiTitle { color: #73818e; font-size: SMALLpx; background: transparent; }
QLabel#KpiValue { color: #213647; font-size: 26px; font-weight: 600; background: transparent; }
QLabel#CardTitle { color: #314b5f; font-size: SIZEpx; font-weight: 600; }
QLabel#KVKey { color: #7c8995; font-size: SMALLpx; }
QLabel#KVValue { color: #293e4f; font-size: SIZEpx; font-weight: 500; }
QLabel#BadgeNeutral, QLabel#PillNeutral { background: #e9eef2; color: #627583; padding: 4px 8px; border-radius: 3px; font-size: SMALLpx; }
QLabel#BadgeOk, QLabel#PillOk { background: #e7f2eb; color: #36765c; padding: 4px 8px; border-radius: 3px; font-size: SMALLpx; }
QLabel#BadgeWarning, QLabel#PillWarn { background: #fff1db; color: #916b32; padding: 4px 8px; border-radius: 3px; font-size: SMALLpx; }
QLabel#BadgeError, QLabel#PillError, QLabel#PillErr { background: #fae8e6; color: #a3534d; padding: 4px 8px; border-radius: 3px; font-size: SMALLpx; }
QProgressBar { background: #e5eaef; border: none; border-radius: 2px; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #d99940; border-radius: 2px; }
QStatusBar { background: #ffffff; border-top: 1px solid #dce2e7; font-size: SMALLpx; }
QStatusBar::item { border: none; }
QStatusBar QLabel { background: transparent; font-size: SMALLpx; color: #71818f; }
QMenuBar, QMenu { background: #ffffff; }
QMenu::item { padding: 7px 25px; }
QMenu::item:selected { background: #e6edf3; }
QScrollBar:vertical { background: transparent; width: 7px; margin: 0; }
QScrollBar::handle:vertical { background: #c7d1d9; min-height: 30px; border-radius: 3px; }
QScrollBar:horizontal { background: transparent; height: 7px; }
QScrollBar::handle:horizontal { background: #c7d1d9; min-width: 30px; border-radius: 3px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: #dce3e9; }
QSplitter::handle:horizontal { width: 3px; }
QToolTip { color: #263544; background: #ffffff; border: 1px solid #cad5df; padding: 6px; }
""".replace("SMALL", str(max(size - 2, 11))).replace("SIZE", str(size)).replace("FONTFAMILY", font_family).replace("CHEVRON", __import__("pathlib").Path(__file__).resolve().parent.parent.joinpath("assets/chevron.svg").as_posix())
