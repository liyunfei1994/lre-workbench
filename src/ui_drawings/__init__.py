"""
ui_drawings —— 工程化几何图纸包

原 ui_drawings.py 已按部件拆分为本包，对外接口完全保持不变：

    from ui_drawings import AutoFitGraphicsView, GeometryDrawingPanel

包结构：
    ui_drawings._common      —— 共享基础设施（守卫导入/样式/工具条/画布/说明面板）
    ui_drawings.engine3d     —— 整机三维视图
    ui_drawings.nozzle       —— 喷管：fig / build_3d / explain
    ui_drawings.chamber      —— 主燃烧室：fig / build_3d / explain
    ui_drawings.injector     —— 喷注器：fig / explain
    ui_drawings.pumps        —— 涡轮泵：fig / explain
    ui_drawings.turbines     —— 涡轮：fig / explain
    ui_drawings.preburners   —— 预燃室：fig / explain
    ui_drawings.panel        —— ComponentView / GeometryDrawingPanel / 3D 调度

要新增一个部件：加一个 ui_drawings/<部件>.py（提供 fig/explain[/build_3d]），
再到 panel.py 的 specs 与 _EXPLAIN_FUNCS 注册即可。
"""

from ._common import (
    AutoFitGraphicsView,
    AspectPixmapLabel,
    MATPLOTLIB_AVAILABLE,
    PYVISTA_AVAILABLE,
)
from .engine3d import EngineThreeDView
from .panel import (
    GeometryDrawingPanel, ComponentView, build_component_3d, export_contour_csv,
)

__all__ = [
    "AutoFitGraphicsView",
    "GeometryDrawingPanel",
    "EngineThreeDView",
    "ComponentView",
    "AspectPixmapLabel",
    "build_component_3d",
    "export_contour_csv",
    "MATPLOTLIB_AVAILABLE",
    "PYVISTA_AVAILABLE",
]
