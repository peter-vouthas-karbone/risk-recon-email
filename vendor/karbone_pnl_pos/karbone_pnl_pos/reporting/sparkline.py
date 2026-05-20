"""
Sparkline utilities for the v4-hybrid PnL report.

sparkline_svg()     — inline SVG string (primary renderer; used by html_builder).
render_sparkline_png() — PNG file via matplotlib (kept for standalone/testing use).
"""
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger('pnl.' + __name__)

_POS_COLOR = '#15803d'
_NEG_COLOR = '#b91c1c'
_ZERO_LINE_COLOR = '#d1d5db'


def sparkline_svg(
    data: List[float],
    width: int = 96,
    height: int = 28,
    show_last_dot: bool = False,
) -> str:
    """
    Return an inline SVG sparkline string.
    Direct port of Sparkline (design/utils.jsx:51).
    Safe to embed directly in HTML; no external resources needed.
    """
    if not data:
        return ''

    max_v = max(max(data), 0.0)
    min_v = min(min(data), 0.0)
    data_range = (max_v - min_v) or 1.0

    n = len(data)
    step_x = width / ((n - 1) or 1)

    def _y(v: float) -> float:
        return height - 4 - ((v - min_v) / data_range) * (height - 8)

    zero_y = _y(0.0)
    points = [f'{i * step_x:.2f},{_y(v):.2f}' for i, v in enumerate(data)]
    points_str = ' '.join(points)

    # Closed fill path: M0,zeroY → polyline → Lwidth,zeroY → Z
    path_d = f'M0,{zero_y:.2f} L{" L".join(points)} L{width:.2f},{zero_y:.2f} Z'

    # Color by the net 30-day total, not the last day — a single positive day
    # at the end shouldn't paint a money-losing window green.
    last_val = data[-1]
    window_total = sum(data)
    color = _POS_COLOR if window_total >= 0 else _NEG_COLOR

    parts = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"'
        f' style="display:block;overflow:visible">',
        f'<line x1="0" y1="{zero_y:.2f}" x2="{width}" y2="{zero_y:.2f}"'
        f' stroke="{_ZERO_LINE_COLOR}" stroke-dasharray="2 2" stroke-width="1"/>',
        f'<path d="{path_d}" fill="{color}" fill-opacity="0.14" stroke="none"/>',
        f'<polyline points="{points_str}" fill="none" stroke="{color}"'
        f' stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>',
    ]
    if show_last_dot:
        last_y = _y(last_val)
        parts.append(
            f'<circle cx="{(n - 1) * step_x:.2f}" cy="{last_y:.2f}" r="2.5" fill="{color}"/>'
        )
    parts.append('</svg>')
    return ''.join(parts)


def sparkline_cid(key: str) -> str:
    """Return the Content-ID string for a given sparkline key (e.g. desk key or 'firm')."""
    return f'spark_{key}@karbone'


def render_sparkline_png(
    data: List[float],
    out_path: Path,
    width_px: int = 120,
    height_px: int = 30,
    show_last_dot: bool = True,
) -> None:
    """
    Render a sparkline to a PNG file.

    Mirrors the Sparkline SVG component (design/utils.jsx:51). Zero line is dashed gray;
    the area between the polyline and zero is lightly filled; color is determined by the
    last data point's sign.
    """
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    if not data:
        _render_blank(out_path, width_px, height_px)
        return

    dpi = 96
    fig_w = width_px / dpi
    fig_h = height_px / dpi

    data = [float(v) for v in data]
    max_v = max(max(data), 0.0)
    min_v = min(min(data), 0.0)
    data_range = max_v - min_v or 1.0

    # Color by the net 30-day total (matches sparkline_svg).
    last_val = data[-1]
    window_total = sum(data)
    color = _POS_COLOR if window_total >= 0 else _NEG_COLOR

    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)
    fig.patch.set_alpha(0.0)
    ax.set_facecolor('none')

    n = len(data)
    xs = np.linspace(0, 1, n)
    ys = np.array(data)

    # Normalised zero line position (for the ax coordinate)
    zero_norm = (0.0 - min_v) / data_range  # 0..1 within data range
    zero_y = min_v  # in data coordinates

    # Dashed zero line
    ax.axhline(y=zero_y, color=_ZERO_LINE_COLOR, linewidth=0.8, linestyle=(0, (3, 3)), zorder=1)

    # Filled area between polyline and zero
    ax.fill_between(xs, ys, zero_y, color=color, alpha=0.14, zorder=2)

    # Polyline
    ax.plot(xs, ys, color=color, linewidth=1.5,
            solid_joinstyle='round', solid_capstyle='round', zorder=3)

    # Last-point dot
    if show_last_dot:
        ax.scatter([xs[-1]], [ys[-1]], s=12, color=color, zorder=4, linewidths=0)

    # Remove all axes chrome
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(min_v - data_range * 0.1, max_v + data_range * 0.1)

    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_path), format='png', dpi=dpi, bbox_inches='tight',
                pad_inches=0, transparent=True)
    plt.close(fig)


def _render_blank(out_path: Path, width_px: int, height_px: int) -> None:
    """Write a fully transparent PNG as a no-data placeholder."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    dpi = 96
    fig, ax = plt.subplots(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    fig.patch.set_alpha(0.0)
    ax.set_axis_off()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_path), format='png', dpi=dpi, bbox_inches='tight',
                pad_inches=0, transparent=True)
    plt.close(fig)
