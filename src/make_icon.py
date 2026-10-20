"""
生成 app_icon.png ── FFSC 数字样机程序图标。
设计:剖面化的火箭发动机(燃烧室 + 收缩段 + 喉部 + TOP 钟形喷管 + 火焰)。
配色:深航空蓝(#0F2851)+ 橙色火焰(#F97316),透明背景。
"""
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch


def make_icon(out_path: Path, size_px: int = 256):
    # 颜色
    COLOR_BODY  = "#0F2851"   # 深航空蓝(燃烧室+喷管轮廓)
    COLOR_FLAME = "#F97316"   # 橙色(火焰)
    COLOR_HIGHLIGHT = "#3B82F6"  # 亮蓝(喉部高亮)

    fig = plt.figure(figsize=(size_px / 64, size_px / 64), dpi=64)
    ax = fig.add_subplot(111)
    ax.set_aspect("equal")
    ax.set_xlim(-1.0, 1.0)
    ax.set_ylim(-0.55, 1.15)
    ax.axis("off")

    # ─────────── 发动机轮廓(从顶到底:燃烧室 → 喉部 → 钟形喷管)───────────
    # 半径 r 作为 |x|;y 为轴向(顶部为 y=1.05,底部喷管出口为 y=0.00)
    r_chamber = 0.32
    y_top     = 1.05
    y_dome    = 0.88
    y_cyl_bot = 0.62
    y_throat  = 0.50
    r_throat  = 0.12
    y_exit    = 0.00
    r_exit    = 0.58

    # 钟形喷管轮廓:用 Bezier 控制点近似 TOP 80% 钟形(图标艺术化)
    # 起点 (r_throat, y_throat) → 终点 (r_exit, y_exit)
    # 初始膨胀角约 35°(喉后),出口角约 9°(平行流)
    bell_pts = []
    for t in np.linspace(0.0, 1.0, 60):
        # 三次贝塞尔:(P0, P1, P2, P3),P0 在喉部,P3 在出口
        P0 = np.array([r_throat, y_throat])
        P1 = np.array([0.27, 0.40])    # 喉后扩张
        P2 = np.array([0.50, 0.15])    # 接近出口减缓
        P3 = np.array([r_exit, y_exit])
        pt = (1 - t) ** 3 * P0 + 3 * (1 - t) ** 2 * t * P1 + 3 * (1 - t) * t ** 2 * P2 + t ** 3 * P3
        bell_pts.append(tuple(pt))

    # 右半轮廓(从顶部圆心顺时针到出口)
    right_outline = [
        (0.0,        y_top),       # 顶部中心
        (r_chamber - 0.05, y_top), # 顶部右边
        (r_chamber, y_dome),       # 圆角过渡
        (r_chamber, y_cyl_bot),    # 圆柱段底
    ]
    # 收缩段(直线到喉部)
    right_outline.append((r_throat, y_throat))
    # 钟形喷管
    right_outline.extend(bell_pts)

    # 左半 = 右半镜像
    left_outline = [(-x, y) for x, y in reversed(right_outline)]
    full_outline = right_outline + left_outline

    # ── 火焰(出口下方的尖锐羽流)─────────────────────────────────
    flame_outer = [
        (-r_exit, y_exit),
        (-0.42, -0.10),
        (-0.30, -0.32),
        (-0.18, -0.20),
        ( 0.00, -0.48),    # 火焰中心最长
        ( 0.18, -0.20),
        ( 0.30, -0.32),
        ( 0.42, -0.10),
        ( r_exit, y_exit),
    ]
    flame_inner = [
        (-0.30, y_exit - 0.02),
        (-0.18, -0.18),
        ( 0.00, -0.32),
        ( 0.18, -0.18),
        ( 0.30, y_exit - 0.02),
    ]

    # 绘制顺序:外焰(底层)→ 发动机体 → 内焰高亮(顶层)
    ax.add_patch(Polygon(flame_outer, closed=True,
                         facecolor=COLOR_FLAME, edgecolor="none", zorder=1))
    ax.add_patch(Polygon(full_outline, closed=True,
                         facecolor=COLOR_BODY, edgecolor="none", zorder=2))
    ax.add_patch(Polygon(flame_inner, closed=True,
                         facecolor="#FBBF24", edgecolor="none", zorder=3, alpha=0.85))

    # 喉部高亮线(增强识别度)
    ax.plot([-r_throat, r_throat], [y_throat, y_throat],
            color=COLOR_HIGHLIGHT, lw=2.5, zorder=4, solid_capstyle="round")

    # 保存(透明背景)
    fig.savefig(out_path, dpi=64, bbox_inches="tight", pad_inches=0.04,
                transparent=True)
    plt.close(fig)
    print(f"icon saved: {out_path}  ({size_px}x{size_px})")


if __name__ == "__main__":
    out = Path(__file__).parent / "app_icon.png"
    make_icon(out, size_px=256)
    # 也存一份 .ico 兼容版(可选)
    try:
        from PIL import Image
        png = Image.open(out)
        ico_path = out.with_suffix(".ico")
        png.save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48),
                                                  (64, 64), (128, 128), (256, 256)])
        print(f"ico saved: {ico_path}")
    except ImportError:
        print("PIL 未装,跳过 .ico 输出(只生成 PNG)。pip install Pillow 可启用。")
