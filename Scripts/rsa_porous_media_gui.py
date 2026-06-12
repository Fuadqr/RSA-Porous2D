"""
================================================================================
 RSA POROUS-MEDIA GENERATOR  --  PySide6 GUI  (modern layout)
================================================================================

A desktop front-end over `rsa_porous_media.py`. The generator module stays pure
(no Qt imports); this file builds a Config, runs it in a worker thread, and
renders the returned arrays.

LAYOUT (this revision: single board, QML)
-----------------------------------------
- TOP   : one command bar -- Generate / Stop, live progress, status badges
          (run state, achieved porosity, throat floor), save actions, log.
- LEFT  : ALL parameter cards visible at once (Domain, Grains, Throat &
          diagnostics, Output, Run, full-width Heterogeneity) with compact
          label-over-input cells -- no form tabs, no per-tab scrolling.
          Conditional sections still expand/collapse in place (e.g. lognormal
          params only when lognormal; band vs drawn polygon per layer_shape).
- RIGHT : permanent live preview (debounced, never blocks) with Preview |
          Result tabs. The preview follows the card being edited (Domain
          fields -> domain sketch, others -> PSD / heterogeneity overview) and can be
          switched manually with the chips above it.
- The log opens as a drawer over the preview pane.
- RSA_GUI_TABBED=1 restores the previous tabbed QML layout (MainTabbed.qml);
  RSA_GUI_WIDGETS=1 still selects the QtWidgets fallback.

HONEST SCOPE (unchanged from before)
------------------------------------
- GUI runs realisations sequentially with live progress + working Stop;
  the parallel ensemble (n_workers) remains a CLI/batch feature.
- PSD preview shows the SAMPLED distribution, not the final placed grains.
- Heterogeneity overview shows nominal rectangle edges; the rough interface wanders.

Run:  python rsa_porous_media_gui.py
================================================================================
"""

from __future__ import annotations

import os
import re
import sys
import time
import queue
import math
import traceback
import multiprocessing as mp
from dataclasses import fields

from PySide6.QtCore import (
    Qt, QThread, QObject, Signal, Slot, QTimer,
    QPropertyAnimation, QEasingCurve, QRect, QSequentialAnimationGroup,
    QVariantAnimation, Property, QUrl,
)
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QComboBox, QCheckBox,
    QPushButton, QFormLayout, QVBoxLayout, QHBoxLayout, QGroupBox, QScrollArea,
    QSplitter, QProgressBar, QTextEdit, QFileDialog, QMessageBox, QSizePolicy,
    QTabWidget, QFrame, QToolButton, QStackedWidget, QGridLayout,
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure

import importlib
import rsa_porous_media as gen
# In an interactive kernel (Jupyter/IPython/Spyder), `import` returns the cached
# module from a previous run. If you edit rsa_porous_media.py and re-run the GUI
# without restarting the kernel, `gen` can be a STALE copy missing newer
# functions (e.g. make_preview_figure) -> the live preview silently never works.
# Reload once if a required entry point is absent so the GUI self-heals.
# `from ... import Config` below then binds the freshly-reloaded class, keeping
# Config identity consistent with `gen`.
if not hasattr(gen, "make_preview_figure"):
    gen = importlib.reload(gen)
from rsa_porous_media import Config

# If it is STILL missing after reload, the on-disk rsa_porous_media.py is itself
# out of date (not just the kernel cache). Flag the exact required functions.
_REQUIRED_GEN = ("make_preview_figure", "make_overview_figure", "generate_single",
                 "check_feasibility", "analyze_pore_network")
ENGINE_MISSING = [n for n in _REQUIRED_GEN if not hasattr(gen, n)]

DEFAULTS = Config()

# -----------------------------------------------------------------------------
# Form spec: tab -> [(section title, [field names])]
# -----------------------------------------------------------------------------
TABS = [
    ("Domain", [
        ("Size  (2D, X-Z plane)", ["width", "height"]),
        ("Output coordinates", ["y_fixed", "x_origin", "z_origin"]),
        ("Boundary", ["snap_wall_enabled", "snap_wall_threshold"]),
    ]),
    ("Grains", [
        ("Distribution", ["dist_type", "r_min", "r_max", "num_sizes"]),
        ("Lognormal", ["ln_sigma", "ln_median"]),
        ("Normal", ["nm_mean", "nm_std"]),
        ("Custom", ["custom_radii", "custom_weights"]),
        ("Porosity", ["target_porosity"]),
    ]),
    ("Heterogeneity", [
        ("Medium", ["medium_type", "layer_shape"]),
        ("Band / patch", ["layer_x_start", "layer_x_end", "layer_z_start", "layer_z_end"]),
        ("Drawn shape", ["layer_polygon", "heterogeneity_polygons"]),
        ("Grains", ["layer_dist_type", "layer_r_min", "layer_r_max",
                    "layer_num_sizes", "layer_ln_sigma", "layer_ln_median",
                    "layer_nm_mean", "layer_nm_std",
                    "layer_porosity"]),
        ("Interface", ["rough_interface", "interface_amplitude", "interface_freqs"]),
    ]),
    ("Throat & Output", [
        ("Minimum pore throat", ["throat_mode", "min_throat", "k_candidates"]),
        ("Output", ["out_dir", "out_basename", "output_format", "use_diameter"]),
        ("Diagnostics", ["run_throat_analysis", "throat_bin_width_um", "make_plots"]),
    ]),
    ("Run", [
        ("Reproducibility", ["seed"]),
        ("Ensemble (CLI for parallel)", ["n_realizations", "n_workers"]),
        ("Safety", ["warn_particle_count", "strict_feasibility"]),
    ]),
]

TAB_LABELS = {
    "Throat & Output": "Throat/Output",
    "Run & Save": "Generate/Save",
}
TAB_CANONICAL = {v: k for k, v in TAB_LABELS.items()}

# Heterogeneity tab: two INDEPENDENT columns so every section keeps its
# designated spot when the shape changes. Left: Medium with the
# shape-specific box (Band/patch or Drawn shape) always directly under it,
# then Interface. Right: always Grains. A hidden section collapses inside
# its own column without reflowing the other one.
HETEROGENEITY_RIGHT_SECTIONS = {"Grains"}

COMBO = {
    "dist_type": ["lognormal", "normal", "uniform", "custom"],
    "layer_dist_type": ["lognormal", "normal", "uniform"],
    "medium_type": ["homogeneous", "layer"],
    "layer_shape": ["rectangle", "polygon"],
    "throat_mode": ["none", "soft", "hard"],
    "output_format": ["dat", "csv", "cin"],
}

COMBO_LABELS = {
    "medium_type": {
        "homogeneous": "Homogeneous",
        "layer": "Heterogeneous",
    },
}

# Human-readable labels for every form field (engine names stay as the
# Config attributes; this map is display-only and feeds both frontends).
FIELD_LABELS = {
    # Domain
    "width": "Domain width",
    "height": "Domain height",
    "y_fixed": "Fixed Y value",
    "x_origin": "X origin",
    "z_origin": "Z origin",
    "snap_wall_enabled": "Snap grains to walls",
    "snap_wall_threshold": "Snap distance",
    # Grains (matrix)
    "dist_type": "Distribution function",
    "r_min": "Min radius",
    "r_max": "Max radius",
    "num_sizes": "Number of sizes",
    "ln_sigma": "Sigma",
    "ln_median": "Median radius",
    "nm_mean": "Mean radius",
    "nm_std": "Std deviation",
    "custom_radii": "Radii list",
    "custom_weights": "Weights list",
    "target_porosity": "Target porosity",
    # Heterogeneity
    "medium_type": "Porous media",
    "layer_shape": "Shape",
    "layer_x_start": "X start",
    "layer_x_end": "X end",
    "layer_z_start": "Z start",
    "layer_z_end": "Z end",
    "layer_polygon": "Polygon points",
    "heterogeneity_polygons": "Polygons",
    "heterogeneity_centers": "Centers",
    "layer_dist_type": "Distribution function",
    "layer_r_min": "Min radius",
    "layer_r_max": "Max radius",
    "layer_num_sizes": "Number of sizes",
    "layer_ln_sigma": "Sigma",
    "layer_ln_median": "Median radius",
    "layer_nm_mean": "Mean radius",
    "layer_nm_std": "Std deviation",
    "layer_porosity": "Target porosity",
    "rough_interface": "Rough interface (adaptive)",
    "interface_amplitude": "Amplitude (0 = auto)",
    "interface_freqs": "Frequencies (blank = auto)",
    # Throat & output
    "throat_mode": "Throat mode",
    "min_throat": "Min throat width",
    "k_candidates": "Candidates per grain (K)",
    "out_dir": "Output folder",
    "out_basename": "File name base",
    "output_format": "File format",
    "use_diameter": "Write diameter (not radius)",
    "run_throat_analysis": "Throat analysis",
    "throat_bin_width_um": "Histogram bin width",
    "make_plots": "Save overview figure",
    # Run
    "seed": "Random seed",
    "n_realizations": "Realisations",
    "n_workers": "Parallel workers (CLI)",
    "warn_particle_count": "Warn above N grains",
    "strict_feasibility": "Abort on warnings",
}

PLACEHOLDERS = {
    "interface_freqs": "blank = adapt to grain size",
}

UNITS = {
    "width": "m", "height": "m", "y_fixed": "m", "x_origin": "m", "z_origin": "m",
    "r_min": "m", "r_max": "m", "ln_median": "m", "nm_mean": "m", "nm_std": "m",
    "layer_x_start": "m", "layer_x_end": "m", "layer_z_start": "m", "layer_z_end": "m",
    "layer_r_min": "m", "layer_r_max": "m",
    "layer_ln_median": "m", "layer_nm_mean": "m", "layer_nm_std": "m",
    "min_throat": "m", "interface_amplitude": "m",
    "throat_bin_um": "um", "throat_bin_width_um": "\u00b5m", "snap_wall_threshold": "m",
}


def _combo_display_items(name):
    labels = COMBO_LABELS.get(name, {})
    return [labels.get(value, value) for value in COMBO.get(name, [])]


def _combo_internal_value(widget):
    values = widget.property("comboValues") if isinstance(widget, QComboBox) else None
    if values:
        idx = widget.currentIndex()
        if 0 <= idx < len(values):
            return str(values[idx])
    return widget.currentText()


def _combo_value(widgets, name):
    return _combo_internal_value(widgets[name])


def _is_heterogeneous_mode(value):
    return value == "layer"

# sections (by title) hidden wholesale when not applicable
SECTION_RULES = {
    "Lognormal": lambda w: w["dist_type"].currentText() == "lognormal",
    "Normal": lambda w: w["dist_type"].currentText() == "normal",
    "Custom": lambda w: w["dist_type"].currentText() == "custom",
    "Band / patch": lambda w: _combo_value(w, "medium_type") == "layer"
    and _combo_value(w, "layer_shape") == "rectangle",
    "Drawn shape": lambda w: _combo_value(w, "medium_type") == "layer"
    and _combo_value(w, "layer_shape") == "polygon",
    "Grains": lambda w: _is_heterogeneous_mode(_combo_value(w, "medium_type")),
    "Interface": lambda w: _combo_value(w, "medium_type") == "layer"
    and _combo_value(w, "layer_shape") == "rectangle",
}

# individual field rules within visible sections
FIELD_RULES = {
    "snap_wall_threshold": lambda w: w["snap_wall_enabled"].isChecked(),
    "layer_shape": lambda w: _combo_value(w, "medium_type") == "layer",
    "min_throat": lambda w: w["throat_mode"].currentText() in ("soft", "hard"),
    "k_candidates": lambda w: w["throat_mode"].currentText() in ("soft", "hard"),
    # interface roughness is adaptive (grain-scale); the GUI exposes only the
    # on/off toggle. The Config fields remain for programmatic overrides.
    "interface_amplitude": lambda w: False,
    "interface_freqs": lambda w: False,
    "layer_ln_sigma": lambda w: _combo_value(w, "layer_dist_type") == "lognormal",
    "layer_ln_median": lambda w: _combo_value(w, "layer_dist_type") == "lognormal",
    "layer_nm_mean": lambda w: _combo_value(w, "layer_dist_type") == "normal",
    "layer_nm_std": lambda w: _combo_value(w, "layer_dist_type") == "normal",
    "heterogeneity_polygons": lambda w: False,
}

QSS = """
/* ---------- base ---------------------------------------------------------- */
* { font-family: "Segoe UI", "Inter", "SF Pro Text", Arial, sans-serif; }
QMainWindow, QWidget { background: #f5f7fb; color: #182230; font-size: 14px; }
/* labels and check/radio text must not paint the page background -- on white
   cards the inherited fill reads as a highlight patch behind the text */
QLabel, QCheckBox, QRadioButton { background: transparent; }

/* ---------- tabs ---------------------------------------------------------- */
QTabWidget::pane { border: none; background: transparent; top: 0; }
QTabBar { background: transparent; qproperty-drawBase: 0; }
QTabBar::tab {
    background: transparent; border: 1px solid transparent; color: #667085;
    padding: 8px 9px 9px 9px; margin-right: 3px; font-weight: 700;
    border-radius: 0px;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:hover { color: #182230; background: #eef4ff; border-color: #d8e6ff; }
QTabBar::tab:selected {
    color: #1f5eff; background: #ffffff; border-color: #d9e2f2;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:disabled { color: #b8c0cc; }

/* ---------- cards --------------------------------------------------------- */
/* The title sits INSIDE the card (below the top border) instead of floating
   on the border line, so headings read as part of the card. */
QGroupBox {
    background: #ffffff; border: 1px solid #dbe4f0; border-radius: 0px;
    margin-top: 6px; padding: 32px 16px 14px 16px;
    font-size: 12px; font-weight: 800; color: #74839a;
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 16px; top: 17px; padding: 0; background: transparent;
}
QWidget#canvasCard { background: #ffffff; border: 1px solid #dbe4f0; border-radius: 0px; }

/* ---------- inputs -------------------------------------------------------- */
QLineEdit, QComboBox {
    background: #f8fafc; border: 1px solid #d8e1ee; border-radius: 0px;
    padding: 8px 11px; color: #182230; min-height: 20px;
    selection-background-color: #2f6fed; selection-color: #ffffff;
}
QLineEdit:hover, QComboBox:hover { border: 1px solid #b8c7dc; background: #ffffff; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #2f6fed; background: #ffffff; }
QLineEdit:disabled, QComboBox:disabled { color: #a8b2c1; background: #eef2f7; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView {
    background: #ffffff; border: 1px solid #dbe4f0; border-radius: 0px; padding: 5px;
    selection-background-color: #eaf1ff; selection-color: #1f5eff; outline: none;
}

/* ---------- checkboxes ---------------------------------------------------- */
QCheckBox { spacing: 9px; color: #182230; }
QCheckBox::indicator {
    width: 19px; height: 19px; border: 1px solid #b8c7dc;
    border-radius: 0px; background: #ffffff;
}
QCheckBox::indicator:hover { border: 1px solid #2f6fed; background: #f8fbff; }
QCheckBox::indicator:checked { background: #2f6fed; border: 1px solid #2f6fed; }

/* ---------- buttons ------------------------------------------------------- */
QPushButton {
    background: #ffffff; border: 1px solid #d5dfec; border-radius: 0px;
    padding: 8px 15px; color: #344054; font-weight: 700;
}
QPushButton:hover { background: #f8fbff; border-color: #b8c7dc; }
QPushButton:pressed { background: #eef4ff; }
QPushButton:disabled { color: #a8b2c1; background: #f1f5f9; border-color: #e2e8f0; }
QPushButton#primary {
    background: #2f6fed; border: none; color: #ffffff; font-weight: 800; padding: 10px 17px;
}
QPushButton#primary:hover { background: #255ed6; }
QPushButton#primary:pressed { background: #1f4fb4; }
QPushButton#primary:disabled { background: #a9c2f7; color: #eef4ff; }
QPushButton#danger { background: #ffffff; color: #b42318; border: 1px solid #f2bbb6; }
QPushButton#danger:hover { background: #fff1f0; }
QPushButton#danger:disabled { background: #ffffff; color: #d99a94; border-color: #f3d6d3; }

/* ---------- progress ------------------------------------------------------ */
QProgressBar {
    background: #e6edf6; border: none; border-radius: 0px;
    min-height: 9px; max-height: 9px;
}
QProgressBar::chunk { background: #2f6fed; border-radius: 0px; }

/* ---------- log / misc ---------------------------------------------------- */
QTextEdit {
    background: #ffffff; border: 1px solid #dbe4f0; border-radius: 0px;
    padding: 8px; color: #344054;
}
QScrollArea { border: none; background: transparent; }
QToolButton { border: 1px solid transparent; color: #667085; font-weight: 700; padding: 5px 10px; border-radius: 0px; }
QToolButton:hover { background: #eef4ff; color: #182230; border-color: #d8e6ff; }
QToolButton:checked { color: #1f5eff; background: #ffffff; border-color: #dbe4f0; }
QStatusBar { background: #eef2f7; color: #667085; }
QStatusBar::item { border: none; }
QToolTip { background: #182230; color: #ffffff; border: none; padding: 7px 10px; border-radius: 0px; }

/* ---------- splitter + scrollbars ---------------------------------------- */
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 10px; }
QSplitter::handle:vertical { height: 10px; }
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar::handle:vertical { background: #c5d1df; border-radius: 0px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #9fb0c4; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
QScrollBar::handle:horizontal { background: #c5d1df; border-radius: 0px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #9fb0c4; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ---------- labels -------------------------------------------------------- */
QLabel#badge { border-radius: 0px; padding: 5px 12px; font-weight: 800; font-size: 12px; }
QLabel#hint { color: #7b8797; font-size: 12px; }
QLabel#paneltitle { font-size: 24px; font-weight: 900; color: #0f172a; }
QLabel#panelsub { color: #667085; font-size: 12px; }
QLabel#progcap { color: #74839a; font-size: 12px; font-weight: 800; }
QLabel#progdetail {
    color: #526070; font-size: 12px;
    font-family: "Consolas", "Cascadia Mono", monospace;
}
"""

BADGE_STYLES = {
    "idle": "background:#e8eef6; color:#667085;",
    "ok":   "background:#dcfce7; color:#166534;",
    "warn": "background:#fef3c7; color:#92400e;",
    "bad":  "background:#fee2e2; color:#991b1b;",
    "run":  "background:#eaf1ff; color:#1f5eff;",
}


def _app_icon_path():
    """Locate the application .ico: next to this script first, then one level
    up (where the project keeps rsa_icon_white.ico)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (here, os.path.dirname(here)):
        p = os.path.join(d, "rsa_icon_white.ico")
        if os.path.exists(p):
            return p
        try:
            for name in sorted(os.listdir(d)):
                if name.lower().endswith(".ico"):
                    return os.path.join(d, name)
        except OSError:
            pass
    return None


def _parse_tuple(text):
    text = text.strip()
    if not text:
        return None
    return tuple(float(p) for p in text.replace(";", ",").split(",") if p.strip())


# live "N=... phi=..." stats embedded in generator progress messages
_PROGRESS_STATS_RE = re.compile(r"N=(\d+)\s+phi=([0-9.]+)")


def _progress_stats(msg):
    m = _PROGRESS_STATS_RE.search(msg)
    return f"N={m.group(1)} phi={m.group(2)}" if m else None


def _format_progress_detail(state, key, pct, stats, freeze=False):
    """tqdm-style per-bar readout: '42% [00:07]  N=299 phi=0.412'.

    `state` is a dict keyed by bar name holding the phase start time, the last
    seen stats, and (once the phase ends) the frozen elapsed time, so the
    clock stops when the bar completes.
    """
    now = time.time()
    st = state.setdefault(key, {})
    if st.get("t0") is None:
        st["t0"] = now
    if stats:
        st["stats"] = stats
    if freeze and st.get("frozen") is None:
        st["frozen"] = now - st["t0"]
    elapsed = st["frozen"] if st.get("frozen") is not None else now - st["t0"]
    mm, ss = divmod(int(max(0.0, elapsed)), 60)
    txt = f"{int(round(pct * 100)):3d}% [{mm:02d}:{ss:02d}]"
    if st.get("stats"):
        txt += "  " + st["stats"]
    return txt


class NoWheelComboBox(QComboBox):
    """Combo box that ignores mouse-wheel changes so scroll areas keep scrolling."""
    def wheelEvent(self, event):
        event.ignore()


class LazyResizeCanvas(FigureCanvas):
    """FigureCanvas that DEBOUNCES resizes. The stock canvas re-renders the
    whole figure on every resize step, so dragging the splitter (or a window
    edge) re-rendered thousands of patches per pixel moved -- that is the
    lag. Here the Qt geometry updates immediately while the expensive
    matplotlib re-render runs once, shortly after the resize burst ends."""

    def __init__(self, fig):
        super().__init__(fig)
        self._resize_defer = QTimer(self)
        self._resize_defer.setSingleShot(True)
        self._resize_defer.setInterval(120)
        self._resize_defer.timeout.connect(self._apply_deferred_resize)

    def resizeEvent(self, event):
        QWidget.resizeEvent(self, event)   # geometry bookkeeping only
        self._resize_defer.start()

    def _apply_deferred_resize(self):
        from PySide6.QtGui import QResizeEvent
        super().resizeEvent(QResizeEvent(self.size(), self.size()))


class Badge(QLabel):
    def __init__(self, text="-"):
        super().__init__(text)
        self.setObjectName("badge")
        # Pulse by tweening the background COLOUR (pure stylesheet) rather than a
        # QGraphicsOpacityEffect -- opacity effects render to an offscreen pixmap
        # and are crash-prone when combined with other painting / teardown.
        self._pulse = QVariantAnimation(self)
        self._pulse.setDuration(1500)
        self._pulse.setStartValue(QColor("#eaf1ff"))
        self._pulse.setKeyValueAt(0.5, QColor("#d9e7ff"))
        self._pulse.setEndValue(QColor("#eaf1ff"))
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.InOutSine)
        self._pulse.valueChanged.connect(self._apply_pulse)
        self.set_state("idle")

    def _apply_pulse(self, color):
        self.setStyleSheet(f"background:{color.name()}; color:#1f5eff;")

    def set_state(self, state, text=None):
        if text is not None:
            self.setText(text)
        self._pulse.stop()
        if state == "run":
            self.setStyleSheet(BADGE_STYLES["run"])
            self._pulse.start()
        else:
            self.setStyleSheet(BADGE_STYLES.get(state, BADGE_STYLES["idle"]))


class AnimatedProgressBar(QProgressBar):
    """Progress bar whose value glides to its target instead of jumping, so
    phase progress reads as fluid motion. Busy/indeterminate mode (range 0,0)
    is left to Qt's own marquee animation."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def animate_to(self, value):
        if self.minimum() == 0 and self.maximum() == 0:
            return  # busy marquee; nothing to interpolate
        self._anim.stop()
        # value() can be -1 right after leaving indeterminate mode; clamp so an
        # interrupted animation never leaves the bar resting at the sentinel.
        self._anim.setStartValue(max(0, self.value()))
        self._anim.setEndValue(int(value))
        self._anim.start()

    def set_busy(self, on):
        self._anim.stop()
        if on:
            self.setRange(0, 0)
        else:
            self.setRange(0, 100)


class TabUnderline(QWidget):
    """A thin accent bar that slides under the active tab of a QTabBar. Uses a
    styled background (Qt paints it) instead of a custom QPainter, which avoids
    backing-store/painter conflicts with the tab bar's own painting."""
    def __init__(self, tabbar, color="#2f6fed", height=3):
        super().__init__(tabbar)
        self._bar = tabbar
        self._h = height
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"background:{color}; border-top-left-radius:0px; "
            f"border-top-right-radius:0px;")
        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        tabbar.currentChanged.connect(lambda *_: self._move(animate=True))
        QTimer.singleShot(0, lambda: self._move(animate=False))

    def _target(self):
        i = self._bar.currentIndex()
        if i < 0:
            return None
        r = self._bar.tabRect(i)
        if not r.isValid() or r.width() <= 0:
            return None
        pad = 14
        return QRect(r.x() + pad, r.bottom() - self._h + 1,
                    max(2, r.width() - 2 * pad), self._h)

    def _move(self, animate=True):
        t = self._target()
        if t is None:
            return
        self.raise_()
        if animate and self.geometry().width() > 0:
            self._anim.stop()
            self._anim.setStartValue(self.geometry())
            self._anim.setEndValue(t)
            self._anim.start()
        else:
            self.setGeometry(t)

    def reposition(self):
        self._move(animate=False)


# Button design tokens (mirror the global QSS so converted buttons look
# identical, but their background colour can be *animated* on hover -- QSS
# :hover is instant and cannot tween).
_BTN_TOKENS = {
    "normal":  dict(bg="#ffffff", hover="#f8fbff", fg="#344054", border="#d5dfec",
                    dis_bg="#f1f5f9", dis_fg="#a8b2c1", dis_border="#e2e8f0",
                    weight=700, pad="8px 15px"),
    "primary": dict(bg="#2f6fed", hover="#255ed6", fg="#ffffff", border=None,
                    dis_bg="#a9c2f7", dis_fg="#eef4ff", dis_border=None,
                    weight=800, pad="10px 17px"),
    "danger":  dict(bg="#ffffff", hover="#fff1f0", fg="#b42318", border="#f2bbb6",
                    dis_bg="#ffffff", dis_fg="#d99a94", dis_border="#f3d6d3",
                    weight=700, pad="8px 15px"),
}


class AnimatedButton(QPushButton):
    """QPushButton whose hover background colour tweens smoothly instead of
    snapping. Self-contained styling (does not rely on global QSS) so the tween
    has a single source of truth; disabled state is handled via a :disabled rule
    that always wins regardless of the animated colour."""
    def __init__(self, text, variant="normal"):
        super().__init__(text)
        self._t = _BTN_TOKENS[variant]
        self._cur = QColor(self._t["bg"])
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._apply)
        self._apply(self._cur)

    def _sheet(self, bg_hex):
        t = self._t
        border = "none" if t["border"] is None else f"1px solid {t['border']}"
        dis_border = "none" if t["dis_border"] is None else f"1px solid {t['dis_border']}"
        return (
            f"QPushButton{{background:{bg_hex};color:{t['fg']};border:{border};"
            f"border-radius: 0px;padding:{t['pad']};font-weight:{t['weight']};}}"
            f"QPushButton:disabled{{background:{t['dis_bg']};color:{t['dis_fg']};"
            f"border:{dis_border};}}"
        )

    def _apply(self, color):
        self._cur = color if isinstance(color, QColor) else QColor(color)
        self.setStyleSheet(self._sheet(self._cur.name()))

    def _tween_to(self, target_hex):
        self._anim.stop()
        self._anim.setStartValue(QColor(self._cur))
        self._anim.setEndValue(QColor(target_hex))
        self._anim.start()

    def enterEvent(self, e):
        if self.isEnabled():
            self._tween_to(self._t["hover"])
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._tween_to(self._t["bg"])
        super().leaveEvent(e)


# =============================================================================
# WORKER
# =============================================================================
def _in_interactive_kernel():
    """True if we are running inside a Jupyter/Spyder (ZMQ) kernel, where
    spawning a child process is unreliable on Windows. Terminal IPython and
    plain `python script.py` return False."""
    try:
        from IPython import get_ipython
        ip = get_ipython()
        if ip is None:
            return False
        return type(ip).__name__ == "ZMQInteractiveShell"
    except Exception:
        return False


def _can_use_process():
    """Use a separate process only when it is actually safe to spawn one.
    Spawn re-imports __main__, which needs a real file and a non-interactive
    context; otherwise (notebook/Spyder) we stay threaded to avoid a hang."""
    if _in_interactive_kernel():
        return False
    main_mod = sys.modules.get("__main__")
    if main_mod is None or not getattr(main_mod, "__file__", None):
        return False
    return True


USE_PROCESS = _can_use_process()


class GenWorker(QObject):
    # (msg, local_frac, realisation_index, n_realisations).
    # local_frac < 0 means "indeterminate / busy" (e.g. throat analysis).
    progress = Signal(str, float, int, int)
    finished = Signal(list)
    failed = Signal(str)
    # Emitted once at start with a short note about which execution mode is used
    # (so the GUI can advise running standalone for full responsiveness).
    mode = Signal(str)

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self._cancel = False          # threaded-path flag
        self._proc = None             # child process (process path)
        # Create the spawn context + cancel event up front so a Stop pressed
        # before the worker thread starts is never lost (it sets a live event,
        # which the child sees on its first check).
        self._ctx = mp.get_context("spawn") if USE_PROCESS else None
        self._cancel_event = self._ctx.Event() if self._ctx is not None else None

    def cancel(self):
        self._cancel = True
        if self._cancel_event is not None:
            self._cancel_event.set()

    @Slot()
    def run(self):
        seeds = [self.cfg.seed + i for i in range(max(1, self.cfg.n_realizations))]
        if USE_PROCESS:
            self.mode.emit("process")
            self._run_process(seeds)
        else:
            self.mode.emit("thread")
            self._run_threaded(seeds)

    # -- separate process: heavy work off the GUI's GIL -> responsive UI -------
    def _run_process(self, seeds):
        try:
            ctx = self._ctx
            q = ctx.Queue()
            self._proc = ctx.Process(
                target=gen.run_realisations_to_queue,
                args=(self.cfg, seeds, q, self._cancel_event),
                daemon=True,
            )
            self._proc.start()
        except Exception:
            # Could not spawn (restricted env): fall back to threaded.
            self._run_threaded(seeds)
            return

        first = True
        while True:
            try:
                item = q.get(timeout=0.2)   # blocks releasing the GIL -> UI free
            except queue.Empty:
                if not self._proc.is_alive():
                    # Died without posting a result.
                    if first:
                        # Never produced output -> likely a spawn problem; retry
                        # threaded so the user still gets a result.
                        self._run_threaded(seeds)
                    else:
                        self.failed.emit("Worker process exited unexpectedly.")
                    return
                continue
            first = False
            kind = item[0]
            if kind == "progress":
                _, msg, frac, k, n = item
                self.progress.emit(msg, frac, k, n)
            elif kind == "result":
                self._proc.join(timeout=2)
                self.finished.emit(item[1])
                return
            elif kind == "cancelled":
                self._proc.join(timeout=2)
                self.failed.emit("Cancelled by user.")
                return
            elif kind == "error":
                self._proc.join(timeout=2)
                self.failed.emit(item[1])
                return

    # -- threaded fallback (kernel / spawn unavailable) ------------------------
    def _run_threaded(self, seeds):
        # Lower the thread-switch interval so the GUI thread gets the GIL more
        # often during the CPU-bound loops -> less (not zero) jank. Restored
        # afterwards. This is the best a same-process thread can do.
        old_interval = sys.getswitchinterval()
        try:
            sys.setswitchinterval(0.001)
            n = len(seeds)
            results = []

            def cancel_cb():
                return self._cancel

            for k, s in enumerate(seeds):
                def pcb(frac, msg, k=k):
                    self.progress.emit(msg, frac, k, n)

                res = gen.generate_single(self.cfg, s, progress_cb=pcb, cancel_cb=cancel_cb)
                if self.cfg.run_throat_analysis:
                    self.progress.emit("[analysis] throat network", -1.0, k, n)
                    floor_um = (self.cfg.min_throat * 1e6) if self.cfg.throat_mode in ("soft", "hard") else None
                    res["net"] = gen.analyze_pore_network(res["centers"], res["radii"],
                                                          self.cfg.throat_bin_width_um, floor_um=floor_um,
                                                          cancel_cb=cancel_cb)
                else:
                    res["net"] = None
                results.append(res)
                if self._cancel:
                    break
            self.finished.emit(results)
        except gen.CancelledError:
            self.failed.emit("Cancelled by user.")
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            sys.setswitchinterval(old_interval)


# =============================================================================
# MAIN WINDOW
# =============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RSA Porous-Media Generator")
        self.resize(1560, 920)
        self.widgets = {}
        self.labels = {}
        self.sections = {}        # section title -> QGroupBox
        self.results = []
        self.cfg = DEFAULTS
        self.result_fig = None
        self.canvas = None
        self.toolbar = None
        self.preview_fig = None
        self.preview_canvas = None
        self.thread = None
        self.worker = None
        self._running = False
        self._drawing_polygon = False
        self._dragging_polygon_index = None
        self._polygon_press_screen = None
        self._polygon_drag_started = False
        self._polygon_drag_threshold_px = 5
        self._dragging_shape_kind = None
        self._dragging_shape_last = None
        self._pending_delete_group_index = None
        self._polygon_group_press_screen = None
        self._dragging_group_index = None

        root = QSplitter(Qt.Horizontal)
        root.setChildrenCollapsible(False)
        # rubber-band resize: panes (and the heavy matplotlib canvases in
        # them) are resized once on mouse RELEASE instead of on every drag
        # pixel, so moving the divider stays fluid
        root.setOpaqueResize(False)
        root.addWidget(self._build_left())
        root.addWidget(self._build_right())
        # right (preview) side gets the larger share by default and absorbs
        # most of any extra width when the window grows
        root.setStretchFactor(0, 1)
        root.setStretchFactor(1, 2)
        root.setSizes([660, 880])
        root.splitterMoved.connect(lambda *_: self._reposition_log_overlay())
        self.setCentralWidget(root)

        domain_idx = self._tab_index("Domain")
        if domain_idx >= 0:
            self.form_tabs.setCurrentIndex(domain_idx)
        self.load_config(DEFAULTS)
        self._update_polygon_buttons()
        self.update_visibility()
        self._refresh_previews()
        self._toggle_save(False)

    def showEvent(self, event):
        # The __init__ refresh runs before the window is on screen, so its draw
        # can fail to stick. Do one guaranteed refresh after the first show so
        # the live preview is populated even on platforms where the pre-show
        # draw is dropped.
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            QTimer.singleShot(0, self._refresh_previews)
        QTimer.singleShot(0, self._reposition_underlines)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_underlines()
        self._reposition_log_overlay()

    def closeEvent(self, event):
        # Stop the infinitely-looping badge pulses so no animation tick fires
        # against a half-destroyed widget during teardown.
        for b in (getattr(self, "badge_state", None),
                  getattr(self, "badge_phi", None),
                  getattr(self, "badge_throat", None)):
            if b is not None and getattr(b, "_pulse", None) is not None:
                b._pulse.stop()
        super().closeEvent(event)

    def _reposition_underlines(self):
        for name in ("_form_underline", "_view_underline"):
            u = getattr(self, name, None)
            if u is not None:
                u.reposition()

    # ------------------------------------------------------------------ left
    def _build_left(self):
        panel = QWidget()
        panel.setMinimumHeight(240)
        panel.setMinimumWidth(640)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 8, 14, 10)
        lay.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(10)
        icon_path = _app_icon_path()
        if icon_path:
            logo = QLabel()
            logo.setPixmap(QIcon(icon_path).pixmap(40, 40))
            logo.setFixedSize(40, 40)
            header.addWidget(logo)
        brand = QVBoxLayout()
        brand.setContentsMargins(0, 0, 0, 0)
        brand.setSpacing(0)
        title = QLabel("RSA Porous Media")
        title.setObjectName("paneltitle")
        subtitle = QLabel("2D random-sequential-adsorption packing generator")
        subtitle.setObjectName("panelsub")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header.addLayout(brand)
        header.addStretch(1)
        lay.addLayout(header)

        self.form_tabs = QTabWidget()
        self.form_tabs.setDocumentMode(True)   # flat tabs, no Windows bevels
        self.form_tabs.tabBar().setExpanding(False)
        self.form_tabs.tabBar().setUsesScrollButtons(True)
        for tab_name, sections in TABS:
            page = QWidget()
            boxes = []
            for sec_title, names in sections:
                box = QGroupBox(sec_title.upper())   # uppercase eyebrow label
                fl = QFormLayout(box)
                fl.setLabelAlignment(Qt.AlignRight)
                fl.setHorizontalSpacing(14)
                fl.setVerticalSpacing(6)
                for name in names:
                    lbl = QLabel(self._label_for(name))
                    w = self._make_widget(name)
                    fl.addRow(lbl, w)
                    self.labels[name] = lbl
                self.sections[sec_title] = box
                boxes.append((sec_title, box))

            if tab_name == "Heterogeneity":
                # Two INDEPENDENT columns: hidden sections collapse inside
                # their own column, so nothing jumps when the shape changes.
                cols = QHBoxLayout(page)
                cols.setContentsMargins(2, 8, 8, 8)
                cols.setSpacing(10)
                vleft, vright = QVBoxLayout(), QVBoxLayout()
                for v in (vleft, vright):
                    v.setSpacing(8)
                for sec_title, box in boxes:
                    (vright if sec_title in HETEROGENEITY_RIGHT_SECTIONS
                     else vleft).addWidget(box)
                vleft.addStretch(1)
                vright.addStretch(1)
                cols.addLayout(vleft, 1)
                cols.addLayout(vright, 1)
            else:
                # Single column; each box hugs its content (no dead space).
                vbox = QVBoxLayout(page)
                vbox.setContentsMargins(2, 8, 8, 8)
                vbox.setSpacing(8)
                for _sec_title, box in boxes:
                    vbox.addWidget(box)
                vbox.addStretch(1)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(page)
            label = TAB_LABELS.get(tab_name, tab_name).replace("&", "&&")
            self.form_tabs.addTab(scroll, label)
        self.form_tabs.addTab(self._build_actions_tab(), TAB_LABELS["Run & Save"])
        lay.addWidget(self.form_tabs, 1)
        self._form_underline = TabUnderline(self.form_tabs.tabBar())
        self.form_tabs.currentChanged.connect(self._on_form_tab_changed)

        # polygon drawing controls
        self.draw_box = QGroupBox("HETEROGENEITY DRAWING")
        dv = QVBoxLayout(self.draw_box)
        dv.setContentsMargins(12, 12, 12, 12)
        dv.setSpacing(8)
        draw_hint = QLabel("Draw a polygon with the old controls. Use New polygon to keep it and start another; click a saved polygon to remove it; hold the RIGHT mouse button inside any polygon to move it.")
        draw_hint.setObjectName("hint")
        draw_hint.setWordWrap(True)
        dv.addWidget(draw_hint)
        drow = QHBoxLayout()
        self.btn_draw_polygon = AnimatedButton("Draw points", "primary")
        self.btn_draw_polygon.clicked.connect(self.on_draw_polygon)
        self.btn_new_polygon = AnimatedButton("New polygon", "normal")
        self.btn_new_polygon.clicked.connect(self.on_new_polygon)
        self.btn_undo_polygon = AnimatedButton("Undo", "normal")
        self.btn_undo_polygon.clicked.connect(self.on_undo_polygon_point)
        self.btn_clear_polygon = AnimatedButton("Clear", "danger")
        self.btn_clear_polygon.clicked.connect(self.on_clear_polygon)
        self.btn_clear_all_polygons = AnimatedButton("Clear all", "danger")
        self.btn_clear_all_polygons.clicked.connect(self.on_clear_all_polygons)
        for b in (self.btn_draw_polygon, self.btn_new_polygon,
                  self.btn_undo_polygon, self.btn_clear_polygon,
                  self.btn_clear_all_polygons):
            drow.addWidget(b)
        dv.addLayout(drow)
        lay.addWidget(self.draw_box)

        return panel

    def _build_actions_tab(self):
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(2, 8, 8, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        run_box = QGroupBox("RUN")
        rv = QVBoxLayout(run_box)
        rv.setContentsMargins(12, 12, 12, 12)
        rv.setSpacing(8)
        row = QHBoxLayout()
        self.btn_generate = AnimatedButton("Generate", "primary")
        self.btn_generate.clicked.connect(self.on_generate)
        self.btn_stop = AnimatedButton("Stop", "danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.on_stop)
        row.addWidget(self.btn_generate, 2)
        row.addWidget(self.btn_stop, 1)
        rv.addLayout(row)
        hint = QLabel("Runs use the current settings from all tabs.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        rv.addWidget(hint)

        progress_box = QGroupBox("PROGRESS")
        pv = QVBoxLayout(progress_box)
        pv.setContentsMargins(12, 12, 12, 12)
        pv.setSpacing(8)
        self.prog_primary = self._make_progress_row("Packing")
        self.prog_secondary = self._make_progress_row("Heterogeneity")
        pv.addWidget(self.prog_primary["row"])
        pv.addWidget(self.prog_secondary["row"])
        self.prog_secondary["row"].setVisible(False)

        save_box = QGroupBox("SAVE")
        sv = QVBoxLayout(save_box)
        sv.setContentsMargins(12, 12, 12, 12)
        sv.setSpacing(8)
        srow = QHBoxLayout()
        self.btn_save_geom = AnimatedButton("Save geometry\u2026", "normal")
        self.btn_save_geom.clicked.connect(self.on_save_geometry)
        self.btn_save_csv = AnimatedButton("Throats CSV\u2026", "normal")
        self.btn_save_csv.clicked.connect(self.on_save_csv)
        self.btn_save_fig = AnimatedButton("Figure\u2026", "normal")
        self.btn_save_fig.clicked.connect(self.on_save_figure)
        for b in (self.btn_save_geom, self.btn_save_csv, self.btn_save_fig):
            srow.addWidget(b)
        sv.addLayout(srow)
        save_hint = QLabel("Save buttons become active after a successful run.")
        save_hint.setObjectName("hint")
        save_hint.setWordWrap(True)
        sv.addWidget(save_hint)

        # stacked full-width boxes: the progress readouts need the whole row
        # (percent / elapsed / N / phi), side-by-side boxes clipped them
        grid.addWidget(run_box, 0, 0)
        grid.addWidget(progress_box, 1, 0)
        grid.addWidget(save_box, 2, 0)
        grid.setColumnStretch(0, 1)
        grid.setRowStretch(3, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    def _make_progress_row(self, caption):
        """A captioned progress bar: small label on the left, bar on the right.
        Returns a dict with the container row, the caption label, and the bar."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        cap = QLabel(caption)
        cap.setObjectName("progcap")
        cap.setMinimumWidth(76)
        bar = AnimatedProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        detail = QLabel("")
        detail.setObjectName("progdetail")
        h.addWidget(cap)
        h.addWidget(bar, 1)
        h.addWidget(detail)
        return {"row": row, "caption": cap, "bar": bar, "detail": detail}

    def _reset_progress(self, is_layer):
        """Prepare the bars for a run. Show the second bar for heterogeneous media."""
        self.prog_primary["bar"].setRange(0, 100)
        self.prog_primary["bar"].setValue(0)
        self.prog_secondary["bar"].setRange(0, 100)
        self.prog_secondary["bar"].setValue(0)
        self.prog_primary["detail"].setText("")
        self.prog_secondary["detail"].setText("")
        self._progress_state = {}
        if is_layer:
            self.prog_primary["caption"].setText("Background")
            self.prog_secondary["caption"].setText("Heterogeneity")
            self.prog_secondary["row"].setVisible(True)
        else:
            self.prog_primary["caption"].setText("Packing")
            self.prog_secondary["row"].setVisible(False)

    # ----------------------------------------------------------------- right
    def _build_right(self):
        panel = QWidget()
        panel.setMinimumHeight(360)
        panel.setMinimumWidth(520)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(6, 10, 10, 10)
        lay.setSpacing(8)

        # status badge strip
        strip = QHBoxLayout()
        strip.addStretch(1)
        self.badge_state = Badge("idle")
        self.badge_phi = Badge("porosity -")
        self.badge_throat = Badge("throat -")
        for b in (self.badge_state, self.badge_phi, self.badge_throat):
            strip.addWidget(b)
        self.log_toggle = QToolButton()
        self.log_toggle.setText("Log \u25be")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        strip.addWidget(self.log_toggle)
        lay.addLayout(strip)

        # collapsible log: an OVERLAY floating above the preview area instead
        # of an in-layout drawer. Animating an in-layout widget resized the
        # matplotlib canvas on every animation frame, forcing full figure
        # re-renders (thousands of patches) -- that is what made open/close
        # feel laggy. The overlay slides over the canvas without touching it.
        self.log = QTextEdit(panel)
        self.log.setReadOnly(True)
        self.log.hide()
        self._log_open_h = 120
        self._log_anim = QPropertyAnimation(self.log, b"geometry", self)
        self._log_anim.setDuration(180)
        self._log_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._log_anim.finished.connect(self._on_log_anim_done)

        # right-side view tabs: Preview (always-on live figure) + Result
        self.view_tabs = QTabWidget()
        self.view_tabs.setDocumentMode(True)
        self.view_tabs.tabBar().setExpanding(True)
        self._view_underline = TabUnderline(self.view_tabs.tabBar())

        # Preview tab (live combined figure) -- ONE persistent canvas
        self.preview_host = QWidget()
        self.preview_host.setObjectName("canvasCard")
        self.preview_layout = QVBoxLayout(self.preview_host)
        self.preview_layout.setContentsMargins(10, 10, 10, 10)
        pnote = QLabel("Live preview \u00b7 domain editor or sampled PSD overview, depending on the active settings.")
        pnote.setObjectName("hint")
        pnote.setWordWrap(True)
        self.preview_layout.addWidget(pnote)
        self.preview_fig = Figure(facecolor="#ffffff")
        self.preview_canvas = LazyResizeCanvas(self.preview_fig)
        self._preview_click_cid = self.preview_canvas.mpl_connect(
            "button_press_event", self.on_preview_click)
        self._preview_motion_cid = self.preview_canvas.mpl_connect(
            "motion_notify_event", self.on_preview_motion)
        self._preview_release_cid = self.preview_canvas.mpl_connect(
            "button_release_event", self.on_preview_release)
        self.preview_layout.addWidget(self.preview_canvas, 1)
        self.view_tabs.addTab(self.preview_host, "Preview")

        # Result tab (generated geometry) -- ONE persistent canvas + toolbar
        self.result_host = QWidget()
        self.result_host.setObjectName("canvasCard")
        self.result_layout = QVBoxLayout(self.result_host)
        self.result_layout.setContentsMargins(10, 10, 10, 10)
        self.result_fig = Figure(figsize=(7, 6), facecolor="#ffffff")
        self.canvas = LazyResizeCanvas(self.result_fig)
        self.toolbar = NavigationToolbar(self.canvas, self)
        self.result_layout.addWidget(self.toolbar)
        self.result_layout.addWidget(self.canvas, 1)
        self.view_tabs.addTab(self.result_host, "Result")

        lay.addWidget(self.view_tabs, 1)
        return panel

    def _show_view(self, which):
        self.view_tabs.setCurrentWidget(
            self.preview_host if which == "preview" else self.result_host)

    def _on_form_tab_changed(self, idx):
        # convenience: jump to Preview while editing any live-preview parameter.
        self._update_draw_box_visibility()
        self._refresh_previews()
        self.view_tabs.setCurrentWidget(self.preview_host)

    def _tab_index(self, title):
        for i in range(self.form_tabs.count()):
            tab_title = self.form_tabs.tabText(i).replace("&&", "&")
            if TAB_CANONICAL.get(tab_title, tab_title) == title:
                return i
        return -1

    def _set_combo_value(self, name, value):
        w = self.widgets.get(name)
        if not isinstance(w, QComboBox):
            return
        values = list(w.property("comboValues") or [])
        if values:
            try:
                idx = values.index(value)
            except ValueError:
                idx = -1
        else:
            idx = w.findText(value)
        if idx >= 0:
            w.setCurrentIndex(idx)

    def _combo_value(self, name):
        w = self.widgets.get(name)
        if not isinstance(w, QComboBox):
            return ""
        return _combo_internal_value(w)

    def _active_form_tab_title(self):
        idx = self.form_tabs.currentIndex()
        if 0 <= idx < self.form_tabs.count():
            tab_title = self.form_tabs.tabText(idx).replace("&&", "&")
            return TAB_CANONICAL.get(tab_title, tab_title)
        return ""

    def _is_left_mouse_button(self, event):
        button = getattr(event, "button", None)
        return button in (None, 1) or getattr(button, "name", "") == "LEFT"

    def _is_right_mouse_button(self, event):
        button = getattr(event, "button", None)
        return button == 3 or getattr(button, "name", "") == "RIGHT"

    def _polygon_mode_active(self):
        if self.widgets.get("medium_type") is None:
            return False
        medium = self._combo_value("medium_type")
        return (
            self.widgets.get("layer_shape") is not None
            and medium == "layer"
            and self._combo_value("layer_shape") == "polygon"
        )

    def _rectangle_mode_active(self):
        return (
            self.widgets.get("medium_type") is not None
            and self.widgets.get("layer_shape") is not None
            and self._combo_value("medium_type") == "layer"
            and self._combo_value("layer_shape") == "rectangle"
        )

    def _domain_preview_active(self):
        return self._active_form_tab_title() == "Domain"

    def _draw_box_should_show(self):
        tab = self._active_form_tab_title()
        has_polygon_state = (
            self._drawing_polygon
            or bool(self._layer_polygon_values())
            or bool(self._polygon_groups())
        )
        return self._polygon_mode_active() and (
            tab == "Heterogeneity" or (tab == "Domain" and has_polygon_state)
        )

    def _update_draw_box_visibility(self):
        if not hasattr(self, "draw_box"):
            return
        show = self._draw_box_should_show()
        self.draw_box.setVisible(show)
        if not show:
            self._drawing_polygon = False
            self._dragging_polygon_index = None
            self._polygon_press_screen = None
            self._polygon_drag_started = False
            self._dragging_shape_kind = None
            self._dragging_shape_last = None
            self._pending_delete_group_index = None
            self._polygon_group_press_screen = None
        self._update_polygon_buttons()

    def _layer_polygon_values(self):
        w = self.widgets.get("layer_polygon")
        if w is None:
            return []
        try:
            vals = _parse_tuple(w.text())
        except (TypeError, ValueError):
            vals = None
        return list(vals or [])

    def _polygon_groups(self):
        w = self.widgets.get("heterogeneity_polygons")
        if w is None:
            return []
        try:
            vals = list(_parse_tuple(w.text()) or [])
        except (TypeError, ValueError):
            return []
        groups = []
        current = []
        for i in range(0, len(vals) - 1, 2):
            x, z = vals[i], vals[i + 1]
            if not (math.isfinite(float(x)) and math.isfinite(float(z))):
                groups.append(current)
                current = []
                continue
            current.extend([float(x), float(z)])
        groups.append(current)
        while groups and not groups[-1]:
            groups.pop()
        return groups

    def _set_polygon_groups(self, groups):
        w = self.widgets.get("heterogeneity_polygons")
        if w is None:
            return
        vals = []
        for idx, group in enumerate(groups):
            if idx:
                vals.extend([float("nan"), float("nan")])
            vals.extend(float(v) for v in group)
        old = w.blockSignals(True)
        w.setText(", ".join("nan" if isinstance(v, float) and math.isnan(v) else f"{float(v):.8g}"
                            for v in vals))
        w.blockSignals(old)

    def _group_to_points(self, group):
        return [(group[i], group[i + 1]) for i in range(0, len(group) - 1, 2)]

    def _valid_polygon_values(self, vals):
        return len(vals) >= 6

    def _same_polygon_values(self, a, b, tol=1e-12):
        if len(a) != len(b):
            return False
        return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))

    def _append_current_polygon_to_groups(self):
        vals = self._layer_polygon_values()
        if not self._valid_polygon_values(vals):
            return False
        groups = self._polygon_groups()
        if not any(self._same_polygon_values(vals, g) for g in groups):
            groups.append(vals)
            self._set_polygon_groups(groups)
        return True

    def _remove_polygon_group(self, index):
        groups = self._polygon_groups()
        if 0 <= int(index) < len(groups):
            del groups[int(index)]
            self._set_polygon_groups(groups)
            return True
        return False

    def _stored_group_contains_point(self, x, z):
        for idx, group in enumerate(self._polygon_groups()):
            points = self._group_to_points(group)
            if len(points) >= 3 and gen._point_in_polygon(x, z, points):
                return idx
        return None

    def _set_layer_polygon_values(self, vals, store=True):
        w = self.widgets.get("layer_polygon")
        if w is None:
            return
        old = w.blockSignals(True)
        w.setText(", ".join(f"{float(v):.8g}" for v in vals))
        w.blockSignals(old)
        self._update_polygon_buttons()

    def _clamped_domain_point(self, event):
        if event.inaxes is None or event.xdata is None or event.ydata is None:
            return None
        try:
            cfg = self.read_config(strict=False)
        except Exception:
            cfg = DEFAULTS
        x = min(max(float(event.xdata), 0.0), float(cfg.width))
        z = min(max(float(event.ydata), 0.0), float(cfg.height))
        return x, z

    def _nearest_polygon_point(self, event, max_px=16):
        vals = self._layer_polygon_values()
        if len(vals) < 2 or event.inaxes is None:
            return None
        if getattr(event, "x", None) is None or getattr(event, "y", None) is None:
            return None

        points = [(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]
        try:
            screen_points = event.inaxes.transData.transform(points)
            ex, ey = float(event.x), float(event.y)
        except Exception:
            return None

        best_idx = None
        best_d2 = float("inf")
        for idx, (px, py) in enumerate(screen_points):
            d2 = (float(px) - ex) ** 2 + (float(py) - ey) ** 2
            if d2 < best_d2:
                best_idx, best_d2 = idx, d2
        return best_idx if best_d2 <= max_px ** 2 else None

    def _replace_polygon_point(self, index, x, z):
        vals = self._layer_polygon_values()
        pos = 2 * int(index)
        if pos + 1 >= len(vals):
            return
        vals[pos] = x
        vals[pos + 1] = z
        self._set_layer_polygon_values(vals)

    def _remove_polygon_point(self, index):
        vals = self._layer_polygon_values()
        pos = 2 * int(index)
        if pos + 1 >= len(vals):
            return False
        del vals[pos:pos + 2]
        self._set_layer_polygon_values(vals)
        return True

    def _set_float_field(self, name, value):
        w = self.widgets.get(name)
        if w is None:
            return
        old = w.blockSignals(True)
        w.setText(f"{float(value):.8g}")
        w.blockSignals(old)

    def _polygon_points(self):
        vals = self._layer_polygon_values()
        return [(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]

    def _polygon_contains_point(self, x, z):
        points = self._polygon_points()
        if len(points) < 3:
            return False
        return gen._point_in_polygon(x, z, points)

    def _rectangle_contains_point(self, x, z):
        try:
            cfg = self.read_config(strict=False)
        except Exception:
            return False
        xs, xe = sorted((float(cfg.layer_x_start), float(cfg.layer_x_end)))
        zs, ze = sorted((float(cfg.layer_z_start), float(cfg.layer_z_end)))
        return xs <= x <= xe and zs <= z <= ze

    def _shape_contains_point(self, x, z):
        if self._polygon_mode_active():
            return self._polygon_contains_point(x, z)
        if self._rectangle_mode_active():
            return self._rectangle_contains_point(x, z)
        return False

    def _move_polygon_by(self, dx, dz):
        points = self._polygon_points()
        if not points:
            return 0.0, 0.0
        try:
            cfg = self.read_config(strict=False)
        except Exception:
            cfg = DEFAULTS
        min_x = min(p[0] for p in points)
        max_x = max(p[0] for p in points)
        min_z = min(p[1] for p in points)
        max_z = max(p[1] for p in points)
        dx = max(-min_x, min(float(dx), float(cfg.width) - max_x))
        dz = max(-min_z, min(float(dz), float(cfg.height) - max_z))
        vals = []
        for x, z in points:
            vals.extend([x + dx, z + dz])
        self._set_layer_polygon_values(vals)
        return dx, dz

    def _move_rectangle_by(self, dx, dz):
        try:
            cfg = self.read_config(strict=False)
        except Exception:
            cfg = DEFAULTS
        xs, xe = sorted((float(cfg.layer_x_start), float(cfg.layer_x_end)))
        zs, ze = sorted((float(cfg.layer_z_start), float(cfg.layer_z_end)))
        wx = max(0.0, xe - xs)
        wz = max(0.0, ze - zs)
        if wx <= 0.0 or wz <= 0.0:
            return 0.0, 0.0
        new_xs = max(0.0, min(xs + float(dx), float(cfg.width) - wx))
        new_zs = max(0.0, min(zs + float(dz), float(cfg.height) - wz))
        actual_dx = new_xs - xs
        actual_dz = new_zs - zs
        self._set_float_field("layer_x_start", new_xs)
        self._set_float_field("layer_x_end", new_xs + wx)
        self._set_float_field("layer_z_start", new_zs)
        self._set_float_field("layer_z_end", new_zs + wz)
        return actual_dx, actual_dz

    def _move_group_by(self, index, dx, dz):
        groups = self._polygon_groups()
        if not (0 <= int(index) < len(groups)):
            return 0.0, 0.0
        points = self._group_to_points(groups[int(index)])
        if not points:
            return 0.0, 0.0
        try:
            cfg = self.read_config(strict=False)
        except Exception:
            cfg = DEFAULTS
        min_x = min(p[0] for p in points)
        max_x = max(p[0] for p in points)
        min_z = min(p[1] for p in points)
        max_z = max(p[1] for p in points)
        dx = max(-min_x, min(float(dx), float(cfg.width) - max_x))
        dz = max(-min_z, min(float(dz), float(cfg.height) - max_z))
        groups[int(index)] = [v for x, z in points for v in (x + dx, z + dz)]
        self._set_polygon_groups(groups)
        return dx, dz

    def _move_current_shape_by(self, dx, dz):
        if self._dragging_shape_kind == "group" and self._dragging_group_index is not None:
            return self._move_group_by(self._dragging_group_index, dx, dz)
        if self._polygon_mode_active():
            return self._move_polygon_by(dx, dz)
        if self._rectangle_mode_active():
            return self._move_rectangle_by(dx, dz)
        return 0.0, 0.0

    def _update_polygon_buttons(self):
        vals = self._layer_polygon_values()
        npts = len(vals) // 2
        is_polygon = self._polygon_mode_active()
        has_saved_polygons = bool(self._polygon_groups())
        if hasattr(self, "btn_draw_polygon"):
            self.btn_draw_polygon.setText("Finish" if self._drawing_polygon else "Draw points")
            self.btn_draw_polygon.setEnabled(is_polygon)
        if hasattr(self, "btn_new_polygon"):
            self.btn_new_polygon.setEnabled(is_polygon and npts >= 3)
        if hasattr(self, "btn_undo_polygon"):
            self.btn_undo_polygon.setEnabled(is_polygon and npts > 0)
        if hasattr(self, "btn_clear_polygon"):
            self.btn_clear_polygon.setEnabled(is_polygon and (npts > 0 or has_saved_polygons))
        if hasattr(self, "btn_clear_all_polygons"):
            self.btn_clear_all_polygons.setEnabled(is_polygon and (npts > 0 or has_saved_polygons))

    def on_new_polygon(self):
        if self._running:
            return
        if not self._append_current_polygon_to_groups():
            QMessageBox.information(
                self, "Incomplete polygon",
                "Finish the current polygon with at least three points before starting another.")
            return
        self._set_combo_value("medium_type", "layer")
        self._set_combo_value("layer_shape", "polygon")
        self._set_layer_polygon_values([])
        self._drawing_polygon = True
        idx = self._tab_index("Domain")
        if idx >= 0:
            self.form_tabs.setCurrentIndex(idx)
        self._update_draw_box_visibility()
        self._update_polygon_buttons()
        self._refresh_previews()
        self.statusBar().showMessage("Saved polygon. Draw the next polygon.")

    def on_draw_polygon(self):
        if self._running:
            return
        self._dragging_polygon_index = None
        self._polygon_press_screen = None
        self._polygon_drag_started = False
        self._dragging_shape_kind = None
        self._dragging_shape_last = None
        self._pending_delete_group_index = None
        self._polygon_group_press_screen = None
        starting = not self._drawing_polygon
        was_polygon = self._polygon_mode_active()
        self._set_combo_value("medium_type", "layer")
        self._set_combo_value("layer_shape", "polygon")
        if starting and not was_polygon:
            self._set_layer_polygon_values([])
        self._drawing_polygon = starting
        if self._drawing_polygon:
            idx = self._tab_index("Domain")
            if idx >= 0:
                self.form_tabs.setCurrentIndex(idx)
        self._update_draw_box_visibility()
        self._update_polygon_buttons()

        self.view_tabs.setCurrentWidget(self.preview_host)
        if self._drawing_polygon:
            self.statusBar().showMessage(
                "Drawing custom shape: click empty space to add; click a point to remove; "
                "right-button drag moves a polygon.")
        else:
            npts = len(self._layer_polygon_values()) // 2
            self.statusBar().showMessage(
                f"Finished polygon with {npts} point(s). Use New polygon to add another, or click a saved polygon to delete it.")
        self._refresh_previews()

    def on_undo_polygon_point(self):
        vals = self._layer_polygon_values()
        if len(vals) >= 2:
            vals = vals[:-2]
            self._set_layer_polygon_values(vals)
            self.statusBar().showMessage(f"Polygon has {len(vals) // 2} point(s).")
            self._refresh_previews()

    def on_clear_polygon(self):
        self._dragging_polygon_index = None
        self._polygon_press_screen = None
        self._polygon_drag_started = False
        self._dragging_shape_kind = None
        self._dragging_shape_last = None
        self._pending_delete_group_index = None
        self._polygon_group_press_screen = None
        self._set_combo_value("medium_type", "layer")
        self._set_combo_value("layer_shape", "polygon")
        if self._layer_polygon_values():
            self._set_layer_polygon_values([])
            self.statusBar().showMessage("Active polygon cleared.")
        elif self._polygon_groups():
            groups = self._polygon_groups()
            groups.pop()
            self._set_polygon_groups(groups)
            self._update_polygon_buttons()
            self.statusBar().showMessage("Removed last saved polygon.")
        else:
            self.statusBar().showMessage("No polygons to clear.")
        self._refresh_previews()

    def on_clear_all_polygons(self):
        self._dragging_polygon_index = None
        self._polygon_press_screen = None
        self._polygon_drag_started = False
        self._dragging_shape_kind = None
        self._dragging_shape_last = None
        self._pending_delete_group_index = None
        self._polygon_group_press_screen = None
        self._drawing_polygon = False
        self._set_combo_value("medium_type", "layer")
        self._set_combo_value("layer_shape", "polygon")
        self._set_polygon_groups([])
        self._set_layer_polygon_values([])
        self.statusBar().showMessage("Cleared all polygons.")
        self._refresh_previews()

    def on_preview_click(self, event):
        if self._running:
            return
        if not self._domain_preview_active():
            return
        point = self._clamped_domain_point(event)
        if point is None:
            return
        x, z = point

        # RIGHT button: hold-and-drag moves a polygon -- the active one or any
        # saved one (left click on a saved polygon still deletes it).
        if self._is_right_mouse_button(event):
            if self._drawing_polygon or not self._polygon_mode_active():
                return
            if self._polygon_contains_point(x, z):
                self._dragging_shape_kind = "polygon"
                self._dragging_shape_last = (x, z)
                self.statusBar().showMessage(
                    "Moving polygon: hold the right button and drag.")
                return
            group_hit = self._stored_group_contains_point(x, z)
            if group_hit is not None:
                self._dragging_shape_kind = "group"
                self._dragging_group_index = group_hit
                self._dragging_shape_last = (x, z)
                self.statusBar().showMessage(
                    f"Moving saved polygon {group_hit + 1}: hold the right button and drag.")
            return

        if not self._is_left_mouse_button(event):
            return

        if self._polygon_mode_active():
            hit = self._nearest_polygon_point(event)
            if hit is not None:
                self._dragging_polygon_index = hit
                self._polygon_press_screen = (getattr(event, "x", None),
                                              getattr(event, "y", None))
                self._polygon_drag_started = False
                self.statusBar().showMessage(
                    f"Point {hit + 1}: release to remove, or drag to move.")
                return

        if self._polygon_mode_active() and not self._drawing_polygon:
            group_hit = self._stored_group_contains_point(x, z)
            if group_hit is not None:
                self._pending_delete_group_index = group_hit
                self._polygon_group_press_screen = (getattr(event, "x", None),
                                                    getattr(event, "y", None))
                self.statusBar().showMessage(
                    f"Release to delete saved polygon {group_hit + 1}.")
                return

        # left-drag move is now reserved for the rectangle patch; polygons
        # move with the right button
        if (not self._drawing_polygon and self._rectangle_mode_active()
                and self._rectangle_contains_point(x, z)):
            self._dragging_shape_kind = "rectangle"
            self._dragging_shape_last = (x, z)
            self.statusBar().showMessage("Moving rectangle.")
            return

        if not self._drawing_polygon:
            return

        vals = self._layer_polygon_values()
        vals.extend([x, z])
        self._set_layer_polygon_values(vals)
        npts = len(vals) // 2
        self.statusBar().showMessage(
            f"Point {npts}: x={x*1e3:.2f} mm, z={z*1e3:.2f} mm")
        self._refresh_previews()

    def on_preview_motion(self, event):
        if self._running:
            return
        point = self._clamped_domain_point(event)
        if point is None:
            return
        x, z = point

        if self._dragging_polygon_index is not None:
            if not self._polygon_drag_started:
                px, py = self._polygon_press_screen or (None, None)
                if px is not None and py is not None:
                    dx = float(getattr(event, "x", px)) - float(px)
                    dy = float(getattr(event, "y", py)) - float(py)
                    if dx * dx + dy * dy < self._polygon_drag_threshold_px ** 2:
                        return
                self._polygon_drag_started = True
            self._replace_polygon_point(self._dragging_polygon_index, x, z)
            self.statusBar().showMessage(
                f"Point {self._dragging_polygon_index + 1}: x={x*1e3:.2f} mm, z={z*1e3:.2f} mm")
            self._refresh_previews()
            return

        if self._dragging_shape_kind is not None and self._dragging_shape_last is not None:
            last_x, last_z = self._dragging_shape_last
            actual_dx, actual_dz = self._move_current_shape_by(x - last_x, z - last_z)
            self._dragging_shape_last = (last_x + actual_dx, last_z + actual_dz)
            kind_label = ("saved polygon" if self._dragging_shape_kind == "group"
                          else self._dragging_shape_kind)
            self.statusBar().showMessage(
                f"Moving {kind_label}: x={x*1e3:.2f} mm, z={z*1e3:.2f} mm")
            self._refresh_previews()

    def on_preview_release(self, event):
        if (self._dragging_polygon_index is None
                and self._dragging_shape_kind is None
                and self._pending_delete_group_index is None):
            return
        point = self._clamped_domain_point(event)

        if self._pending_delete_group_index is not None:
            idx = self._pending_delete_group_index
            px, py = self._polygon_group_press_screen or (None, None)
            delete = True
            if px is not None and py is not None:
                dx = float(getattr(event, "x", px)) - float(px)
                dy = float(getattr(event, "y", py)) - float(py)
                delete = dx * dx + dy * dy < self._polygon_drag_threshold_px ** 2
            self._pending_delete_group_index = None
            self._polygon_group_press_screen = None
            if delete and self._remove_polygon_group(idx):
                self._refresh_previews()
                self.statusBar().showMessage(f"Deleted saved polygon {idx + 1}.")
            return

        if self._dragging_polygon_index is not None:
            point_index = self._dragging_polygon_index
            if not self._polygon_drag_started:
                removed = self._remove_polygon_point(point_index)
                self._dragging_polygon_index = None
                self._polygon_press_screen = None
                self._polygon_drag_started = False
                if removed:
                    self._refresh_previews()
                    self.statusBar().showMessage(f"Removed polygon point {point_index + 1}.")
                return
            if point is not None:
                self._replace_polygon_point(point_index, *point)
                self._refresh_previews()
            self._dragging_polygon_index = None
            self._polygon_press_screen = None
            self._polygon_drag_started = False
            self.statusBar().showMessage(f"Moved polygon point {point_index + 1}.")
            return

        kind = self._dragging_shape_kind
        if point is not None and self._dragging_shape_last is not None:
            x, z = point
            last_x, last_z = self._dragging_shape_last
            self._move_current_shape_by(x - last_x, z - last_z)
            self._refresh_previews()
        self._dragging_shape_kind = None
        self._dragging_shape_last = None
        self._dragging_group_index = None
        self.statusBar().showMessage(
            "Moved saved polygon." if kind == "group" else f"Moved {kind}.")

    # ------------------------------------------------------------- widgets
    def _label_for(self, name):
        label = FIELD_LABELS.get(name, name)
        unit = f"  [{UNITS[name]}]" if name in UNITS else ""
        return f"{label}{unit}"

    def _make_widget(self, name):
        default = getattr(DEFAULTS, name)
        if isinstance(default, bool):
            w = QCheckBox()
        elif name in COMBO:
            w = NoWheelComboBox()
            w.setProperty("comboValues", COMBO[name])
            w.addItems(_combo_display_items(name))
        elif isinstance(default, tuple) or default is None:
            w = QLineEdit()
            w.setPlaceholderText(PLACEHOLDERS.get(name, "comma-separated, blank = none"))
        else:
            w = QLineEdit()
        self.widgets[name] = w

        # signal wiring: selectors drive visibility; everything refreshes previews
        if isinstance(w, QComboBox):
            w.currentTextChanged.connect(self._on_field_changed)
        elif isinstance(w, QCheckBox):
            w.toggled.connect(self._on_field_changed)
        else:
            def _line_changed(*_, field=name):
                if field == "layer_polygon":
                    self._update_polygon_buttons()
                self._schedule_previews()
            w.textChanged.connect(_line_changed)
        return w

    def _on_field_changed(self, *_):
        self.update_visibility()
        self._update_polygon_buttons()
        self._schedule_previews()

    # -------------------------------------------------------- config <-> form
    def load_config(self, cfg: Config):
        for name, w in self.widgets.items():
            val = getattr(cfg, name)
            if isinstance(w, QCheckBox):
                w.setChecked(bool(val))
            elif isinstance(w, QComboBox):
                self._set_combo_value(name, str(val))
            else:
                if val is None:
                    w.setText("")
                elif isinstance(val, tuple):
                    w.setText(", ".join(str(v) for v in val))
                else:
                    w.setText(str(val))

    def read_config(self, strict: bool = True) -> Config:
        """Build a Config from the form.

        strict=True  (Generate): any unparseable field raises -> the user gets a
                     hard error, as they should before a real run.
        strict=False (live preview): a transiently empty/half-typed field falls
                     back to the LAST GOOD value for that field instead of
                     killing the whole read. This keeps the preview live while
                     you are mid-typing one box (e.g. you cleared r_max to retype
                     it) -- every other field still updates the plot."""
        kwargs = {}
        for f in fields(Config):
            name = f.name
            w = self.widgets.get(name)
            default = getattr(DEFAULTS, name)
            if w is None:
                # engine-only field (no GUI widget, e.g. RSA solver
                # termination limits): carry the current value through
                kwargs[name] = getattr(self.cfg, name, default)
                continue
            try:
                if isinstance(w, QCheckBox):
                    kwargs[name] = w.isChecked()
                elif isinstance(w, QComboBox):
                    kwargs[name] = _combo_internal_value(w)
                else:
                    text = w.text().strip()
                    if isinstance(default, tuple) or default is None:
                        kwargs[name] = _parse_tuple(text)
                    elif isinstance(default, bool):
                        kwargs[name] = text.lower() in ("1", "true", "yes")
                    elif isinstance(default, int):
                        kwargs[name] = int(float(text))
                    elif isinstance(default, float):
                        kwargs[name] = float(text)
                    else:
                        kwargs[name] = text
            except (ValueError, TypeError):
                if strict:
                    raise
                # last good value (from the most recent valid config), else default
                kwargs[name] = getattr(self.cfg, name, default)
        return Config(**kwargs)

    # -------------------------------------------------- conditional visibility
    def update_visibility(self):
        w = self.widgets
        for title, rule in SECTION_RULES.items():
            self.sections[title].setVisible(bool(rule(w)))
        for name, rule in FIELD_RULES.items():
            show = bool(rule(w))
            self.widgets[name].setVisible(show)
            self.labels[name].setVisible(show)
        self._update_draw_box_visibility()

    # ------------------------------------------------------------ live preview
    def _schedule_previews(self):
        if not hasattr(self, "_preview_timer"):
            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.setInterval(200)
            self._preview_timer.timeout.connect(self._refresh_previews)
        self._preview_timer.start()

    def _refresh_previews(self):
        # Don't recompute/redraw the live preview while a generation is running:
        # it competes with the worker for the GIL and makes the UI (e.g. tab
        # switching) feel laggy. Previews resume when the run finishes.
        if getattr(self, "_running", False):
            return
        # Tolerant read: one half-typed box falls back to its last good value
        # instead of freezing the whole preview.
        try:
            cfg = self.read_config(strict=False)
        except Exception:
            return  # truly unrecoverable; keep last good plot
        try:
            if self._domain_preview_active():
                _make_domain_preview_figure(cfg, fig=self.preview_fig)
            else:
                gen.make_preview_figure(cfg, fig=self.preview_fig)
            # draw_idle(): non-blocking. Avoids stalling the main thread during a
            # redraw (synchronous draw() made tab switching janky under load).
            self.preview_canvas.draw_idle()
        except Exception:
            # Never swallow silently: a hidden error here is indistinguishable
            # from a dead preview. Surface it (once) so it can be diagnosed.
            self._report_preview_error(traceback.format_exc())

    def _report_preview_error(self, tb):
        sys.stderr.write("\n[preview error]\n" + tb + "\n")
        if not getattr(self, "_preview_error_shown", False):
            self._preview_error_shown = True
            self._log("Live preview error (first occurrence):\n" + tb)
            self.log_toggle.setChecked(True)
            self.statusBar().showMessage("Live preview failed - see log.")

    def draw_result(self, idx):
        res = self.results[idx]
        gen.make_overview_figure(self.cfg, res, res.get("net"), fig=self.result_fig)
        self.canvas.draw()

    # ----------------------------------------------------------------- actions
    def _log(self, text, clear=False):
        if clear:
            self.log.clear()
        self.log.append(text)

    def _log_overlay_rect(self, h):
        """Overlay position: pinned just below the view tab bar, spanning the
        preview width. Coordinates are relative to the right panel."""
        tabs = self.view_tabs
        x = tabs.x() + 10
        y = tabs.y() + tabs.tabBar().height() + 8
        w = max(120, tabs.width() - 20)
        return QRect(x, y, w, h)

    def _reposition_log_overlay(self):
        if self.log.isVisible() and self._log_anim.state() != QPropertyAnimation.Running:
            self.log.setGeometry(self._log_overlay_rect(
                self._log_open_h if self.log_toggle.isChecked() else 0))

    def _on_log_anim_done(self):
        if not self.log_toggle.isChecked():
            self.log.hide()

    def _toggle_log(self, on):
        self.log_toggle.setText("Log \u25b4" if on else "Log \u25be")
        self._log_anim.stop()
        start = (self.log.geometry() if self.log.isVisible()
                 else self._log_overlay_rect(0))
        end = self._log_overlay_rect(self._log_open_h if on else 0)
        self.log.show()
        self.log.raise_()
        self._log_anim.setStartValue(start)
        self._log_anim.setEndValue(end)
        self._log_anim.start()

    def on_generate(self):
        try:
            cfg = self.read_config()
        except Exception as e:
            QMessageBox.critical(self, "Invalid input", f"Could not read the form:\n{e}")
            return
        if cfg.medium_type == "layer" and getattr(cfg, "layer_shape", "rectangle") == "polygon":
            if not gen.make_layer_geometry(cfg).get("polygons"):
                QMessageBox.warning(
                    self, "Incomplete polygon",
                    "Draw at least three points for a custom heterogeneity polygon.")
                return
        try:
            n_est, warns = gen.check_feasibility(cfg)
        except Exception as e:
            QMessageBox.critical(self, "Config error", str(e))
            return

        self._drawing_polygon = False
        self._dragging_polygon_index = None
        self._dragging_shape_kind = None
        self._dragging_shape_last = None
        self._pending_delete_group_index = None
        self._polygon_group_press_screen = None
        self._update_polygon_buttons()
        self._log(f"Estimated grains: ~{n_est:,}", clear=True)
        for wmsg in warns:
            self._log("\u26a0 " + wmsg)
        if warns:
            self.log_toggle.setChecked(True)  # surface warnings
            if cfg.strict_feasibility:
                QMessageBox.warning(self, "Aborted", "strict_feasibility=True with warnings present.")
                return

        self.cfg = cfg
        self._running = True
        self.btn_generate.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._reset_progress(is_layer=_is_heterogeneous_mode(cfg.medium_type))
        self.badge_state.set_state("run", "running\u2026")
        self.badge_phi.set_state("idle", "porosity -")
        self.badge_throat.set_state("idle", "throat -")

        self.thread = QThread()
        self.worker = GenWorker(cfg)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.mode.connect(self.on_mode)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    @Slot(str)
    def on_mode(self, mode):
        if mode == "process":
            self.statusBar().showMessage("Generating in a separate process - UI stays responsive.")
        elif not getattr(self, "_thread_mode_noted", False):
            # Only nag once per session.
            self._thread_mode_noted = True
            self._log("Running inside an interactive kernel (Jupyter/Spyder): heavy "
                      "generation runs in a background THREAD, so the UI can feel laggy "
                      "while it works. For a fully responsive UI, launch the app standalone "
                      "from a terminal:  python rsa_porous_media_gui.py")
            self.log_toggle.setChecked(True)

    @Slot(str, float, int, int)
    def on_progress(self, msg, frac, k, n):
        ens = f"realisation {k + 1}/{n} - " if n > 1 else ""

        if "[analysis]" in msg:
            # Uninstrumented phase: show a busy/indeterminate state on the primary
            # bar so a full bar doesn't read as "stuck / done" while it runs.
            self.prog_primary["bar"].set_busy(True)
            self.badge_state.set_state("run", "analyzing\u2026")
            self.statusBar().showMessage(ens + "analyzing throat network\u2026")
            return

        if not hasattr(self, "_progress_state"):
            self._progress_state = {}
        frac01 = max(0.0, min(1.0, frac))
        pct = int(100 * frac01)
        stats = _progress_stats(msg)
        if "heterogeneity:grains" in msg or "layer:fine" in msg:
            self.prog_primary["bar"].set_busy(False)
            self.prog_primary["bar"].animate_to(100)     # background done
            self.prog_secondary["bar"].animate_to(pct)
            self.prog_primary["detail"].setText(_format_progress_detail(
                self._progress_state, "primary", 1.0, None, freeze=True))
            self.prog_secondary["detail"].setText(_format_progress_detail(
                self._progress_state, "secondary", frac01, stats,
                freeze=frac01 >= 1.0))
        elif "heterogeneity:bg" in msg or "layer:bg" in msg:
            self.prog_primary["bar"].set_busy(False)
            self.prog_primary["bar"].animate_to(pct)
            self.prog_secondary["bar"].setValue(0)
            self.prog_primary["detail"].setText(_format_progress_detail(
                self._progress_state, "primary", frac01, stats,
                freeze=frac01 >= 1.0))
        else:  # homogeneous (single bar)
            self.prog_primary["bar"].set_busy(False)
            self.prog_primary["bar"].animate_to(pct)
            self.prog_primary["detail"].setText(_format_progress_detail(
                self._progress_state, "primary", frac01, stats,
                freeze=frac01 >= 1.0))

        self.statusBar().showMessage(ens + msg)

    @Slot(list)
    def on_finished(self, results):
        self._reset_run_buttons()
        if not results:
            self.badge_state.set_state("warn", "no result")
            self._log("No realisations produced.")
            return
        self.results = results
        for k, res in enumerate(results):
            m = res["meta"]
            line = (f"[r{k}] seed={res['seed']}  N={m['n']}  "
                    f"phi={m['phi']:.4f} (target {self.cfg.target_porosity})")
            if m["phi"] > self.cfg.target_porosity + 0.01:
                line += "  - jammed above target"
            self._log(line)
            status = gen.throat_floor_status(self.cfg, res.get("net"))
            if status:
                self._log("   " + status)

        # badges from the first realisation
        m0 = self.results[0]["meta"]
        jammed = m0["phi"] > self.cfg.target_porosity + 0.01
        self.badge_phi.set_state("warn" if jammed else "ok",
                                 f"\u03c6 {m0['phi']:.3f}" + (" (jammed)" if jammed else ""))
        net0 = self.results[0].get("net")
        if net0 is None or self.cfg.throat_mode == "none":
            self.badge_throat.set_state("idle", "throat -")
        else:
            nbf = net0.get("n_below_floor") or 0
            t_um = self.cfg.min_throat * 1e6
            if self.cfg.throat_mode == "hard":
                ok = net0["min_um"] >= t_um - 1e-6
                self.badge_throat.set_state("ok" if ok else "bad",
                                            f"min throat {net0['min_um']:.0f}\u00b5m "
                                            + ("\u2265" if ok else "<") + f" {t_um:.0f}")
            else:
                n_tot = int(net0.get("n_throats") or 0)
                pct = f" ({100.0 * nbf / n_tot:.1f}%)" if n_tot else ""
                self.badge_throat.set_state("warn" if nbf else "ok",
                                            f"{nbf} below {t_um:.0f}\u00b5m{pct} (soft)")

        self._running = False
        self.prog_primary["bar"].set_busy(False)
        self.prog_primary["bar"].animate_to(100)
        if self.prog_secondary["row"].isVisible():
            self.prog_secondary["bar"].animate_to(100)
        self.badge_state.set_state("ok", "done")
        self.draw_result(0)
        self._show_view("result")
        self._toggle_save(True)
        self.statusBar().showMessage("Done.")

    @Slot(str)
    def on_failed(self, msg):
        self._running = False
        self.prog_primary["bar"].set_busy(False)  # stop the busy marquee
        self._reset_run_buttons()
        if msg.startswith("Cancelled"):
            self.badge_state.set_state("warn", "cancelled")
            self.statusBar().showMessage("Cancelled.")
            self._log("Cancelled.")
        else:
            self.badge_state.set_state("bad", "failed")
            QMessageBox.critical(self, "Generation failed", msg)
            self._log("ERROR:\n" + msg)

    def on_stop(self):
        if self.worker is not None:
            self.worker.cancel()
            self.statusBar().showMessage("Stopping\u2026")

    def _reset_run_buttons(self):
        self.btn_generate.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def _toggle_save(self, on):
        for b in (self.btn_save_geom, self.btn_save_csv, self.btn_save_fig):
            b.setEnabled(on)

    # ------------------------------------------------------------------ saves
    def on_save_geometry(self):
        if not self.results:
            return
        res = self.results[0]
        ext = self.cfg.output_format
        path, _ = QFileDialog.getSaveFileName(self, "Save geometry",
                                              f"{self.cfg.out_basename}.{ext}",
                                              f"Geometry (*.{ext})")
        if not path:
            return
        stem = os.path.splitext(path)[0]
        out = gen.write_geometry(self.cfg, res["centers"], res["radii"], stem)
        self.statusBar().showMessage(f"Saved {out}")

    def on_save_csv(self):
        if not self.results or not self.results[0].get("net"):
            QMessageBox.information(self, "No throats", "Run with throat analysis enabled first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save throat CSV",
                                              f"{self.cfg.out_basename}_throats.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        gen.write_throat_csv(self.results[0]["net"], path)
        self.statusBar().showMessage(f"Saved {path}")

    def on_save_figure(self):
        if self.result_fig is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save figure",
                                              f"{self.cfg.out_basename}_overview.png",
                                              "PNG (*.png);;PDF (*.pdf)")
        if not path:
            return
        self.result_fig.savefig(path, dpi=200, bbox_inches="tight")
        self.statusBar().showMessage(f"Saved {path}")


def _value_to_form(val):
    if val is None:
        return ""
    if isinstance(val, tuple):
        return ", ".join(str(v) for v in val)
    if isinstance(val, bool):
        return val
    return str(val)


def _field_type(name):
    default = getattr(DEFAULTS, name)
    if isinstance(default, bool):
        return "bool"
    if name in COMBO:
        return "combo"
    return "text"


def _field_label(name):
    label = FIELD_LABELS.get(name, name).replace("_", " ")
    unit = f" [{UNITS[name]}]" if name in UNITS else ""
    return label + unit


def _build_qml_tabs():
    tabs = []
    for tab_name, sections in TABS:
        qml_sections = []
        for section_title, names in sections:
            qml_fields = []
            for name in names:
                default = getattr(DEFAULTS, name)
                qml_fields.append({
                    "name": name,
                    "label": _field_label(name),
                    "type": _field_type(name),
                    "options": _combo_display_items(name) if name in COMBO else [],
                    "values": COMBO.get(name, []),
                    "value": _value_to_form(default),
                    "placeholder": PLACEHOLDERS.get(name, (
                        "comma-separated, blank = none"
                        if isinstance(default, tuple) or default is None else ""
                    )),
                })
            qml_sections.append({"title": section_title, "fields": qml_fields})
        tabs.append({"title": tab_name, "sections": qml_sections})
    return tabs


def _polygon_points_from_flat(values, cfg):
    if values is None:
        return []
    try:
        vals = [float(v) for v in values]
    except (TypeError, ValueError):
        return []
    pts = []
    for i in range(0, len(vals) - 1, 2):
        x = min(max(vals[i], 0.0), float(cfg.width))
        z = min(max(vals[i + 1], 0.0), float(cfg.height))
        pts.append((x, z))
    return pts


def _polygon_center_from_points(points):
    if not points:
        return None
    if len(points) < 3:
        return (sum(p[0] for p in points) / len(points),
                sum(p[1] for p in points) / len(points))
    area2 = 0.0
    cx = 0.0
    cz = 0.0
    for i, (x0, z0) in enumerate(points):
        x1, z1 = points[(i + 1) % len(points)]
        cross = x0 * z1 - x1 * z0
        area2 += cross
        cx += (x0 + x1) * cross
        cz += (z0 + z1) * cross
    if abs(area2) < 1e-18:
        return (sum(p[0] for p in points) / len(points),
                sum(p[1] for p in points) / len(points))
    return (cx / (3.0 * area2), cz / (3.0 * area2))


def _make_domain_preview_figure(cfg, fig=None):
    if fig is None:
        fig = Figure(figsize=(7, 5), facecolor="#ffffff")
    fig.clear()
    ax = fig.add_subplot(1, 1, 1)

    from matplotlib.patches import Polygon, Rectangle

    w_mm = cfg.width * 1e3
    h_mm = cfg.height * 1e3
    pad_x = max(cfg.width * 0.22, 1e-6)
    pad_z = max(cfg.height * 0.22, 1e-6)

    ax.add_patch(Rectangle((0, 0), cfg.width, cfg.height,
                           facecolor="#edf4ff", edgecolor="#1d4ed8", lw=2.2))
    ax.annotate("", xy=(0, -0.07 * cfg.height),
                xytext=(cfg.width, -0.07 * cfg.height),
                arrowprops=dict(arrowstyle="<->", color="#344054", lw=1.6))
    ax.text(cfg.width / 2, -0.13 * cfg.height, f"width = {w_mm:.1f} mm",
            ha="center", va="center", fontsize=16, fontweight="bold", color="#344054")

    ax.annotate("", xy=(-0.07 * cfg.width, 0),
                xytext=(-0.07 * cfg.width, cfg.height),
                arrowprops=dict(arrowstyle="<->", color="#344054", lw=1.6))
    ax.text(-0.14 * cfg.width, cfg.height / 2, f"height = {h_mm:.1f} mm",
            ha="center", va="center", rotation=90, fontsize=16, fontweight="bold",
            color="#344054")

    ax.text(cfg.width / 2, cfg.height / 2, f"{w_mm:.1f} x {h_mm:.1f} mm",
            ha="center", va="center", fontsize=18, fontweight="bold",
            color="#1d4ed8")
    if cfg.medium_type == "layer":
        geo = gen.make_layer_geometry(cfg)
        if geo.get("shape") in ("polygon", "multipolygon"):
            polygons = geo.get("polygons", [])
            if geo.get("shape") == "polygon":
                polygons = [geo.get("polygon", [])]
            for points in polygons:
                if not points:
                    continue
                if len(points) >= 3:
                    ax.add_patch(Polygon(points, closed=True, facecolor="#ccfbf1",
                                         edgecolor="#0f766e", lw=2.0, alpha=0.72))
                    px = [p[0] for p in points] + [points[0][0]]
                    pz = [p[1] for p in points] + [points[0][1]]
                else:
                    px = [p[0] for p in points]
                    pz = [p[1] for p in points]
                ax.plot(px, pz, color="#0f766e", lw=1.5)
                ax.scatter([p[0] for p in points], [p[1] for p in points],
                           s=42, color="#0f766e", edgecolor="#ffffff",
                           linewidth=1.0, zorder=5)
            centers = geo.get("centers") or []
            if not centers and geo.get("polygon"):
                center = _polygon_center_from_points(geo.get("polygon", []))
                centers = [center] if center is not None else []
            for center in centers:
                ax.scatter([center[0]], [center[1]], s=78, marker="P",
                           color="#f97316", edgecolor="#ffffff",
                           linewidth=1.0, zorder=6)
        elif geo.get("shape") == "rectangle":
            xs, xe, zs, ze = geo["xs"], geo["xe"], geo["zs"], geo["ze"]
            ax.add_patch(Rectangle((xs, zs), xe - xs, ze - zs,
                                   facecolor="#dbeafe", edgecolor="#0f766e",
                                   lw=2.0, alpha=0.62))
            ax.scatter([(xs + xe) / 2], [(zs + ze) / 2], s=78, marker="P",
                       color="#f97316", edgecolor="#ffffff",
                       linewidth=1.0, zorder=6)
    ax.set_title("Domain dimensions", fontsize=24, fontweight="bold", pad=16)
    ax.set_xlim(-pad_x, cfg.width + pad_x)
    ax.set_ylim(cfg.height + pad_z, -pad_z)
    ax.set_aspect("equal", adjustable="box")
    ax.set_axis_off()
    fig.tight_layout()
    return fig


def _to_float_list(values):
    if values is None:
        return []
    return [float(v) for v in values]


def _psd_canvas_data(diag, title, color):
    radii = diag["radii"]
    weights = diag["weights"]
    curve_r = diag.get("curve_r")
    curve_pdf = diag.get("curve_pdf")
    curve_y = []
    if curve_pdf is not None and len(weights) and max(weights) > 0:
        max_curve = float(max(curve_pdf))
        curve_y = [float(v) / max_curve * float(max(weights)) if max_curve > 0 else 0.0
                   for v in curve_pdf]
    return {
        "title": title,
        "color": color,
        "diametersMm": [float(r) * 2.0e3 for r in radii],
        "weights": _to_float_list(weights),
        "curveDiametersMm": [float(r) * 2.0e3 for r in curve_r] if curve_r is not None else [],
        "curveWeights": curve_y,
        "coverage": float(diag.get("coverage", 1.0)),
        "maxWeight": float(diag.get("max_weight", 1.0)),
    }


def _layer_canvas_data(cfg):
    geo = gen.make_layer_geometry(cfg)
    xs, xe, zs, ze = geo["xs"], geo["xe"], geo["zs"], geo["ze"]
    ei, eo = geo["edge_internal"], geo["edge_offset"]
    curves = []

    def sample(lo, hi, n=160):
        if n <= 1 or hi <= lo:
            return [lo]
        return [lo + (hi - lo) * i / (n - 1) for i in range(n)]

    if ei["x_lo"]:
        z_vals = sample(zs, ze)
        curves.append({
            "x": [xs + eo("x_lo", z, zs, ze) for z in z_vals],
            "z": z_vals,
        })
    if ei["x_hi"]:
        z_vals = sample(zs, ze)
        curves.append({
            "x": [xe - eo("x_hi", z, zs, ze) for z in z_vals],
            "z": z_vals,
        })
    if ei["z_lo"]:
        x_vals = sample(xs, xe)
        curves.append({
            "x": x_vals,
            "z": [zs + eo("z_lo", x, xs, xe) for x in x_vals],
        })
    if ei["z_hi"]:
        x_vals = sample(xs, xe)
        curves.append({
            "x": x_vals,
            "z": [ze - eo("z_hi", x, xs, xe) for x in x_vals],
        })

    return {
        "shape": geo.get("shape", "rectangle"),
        "polygon": [{"x": float(x), "z": float(z)}
                    for x, z in geo.get("polygon", [])],
        "polygons": [
            [{"x": float(x), "z": float(z)} for x, z in poly]
            for poly in geo.get("polygons", [])
        ],
        "centers": [{"x": float(x), "z": float(z)}
                    for x, z in geo.get("centers", [])],
        "width": float(cfg.width),
        "height": float(cfg.height),
        "xs": float(xs),
        "xe": float(xe),
        "zs": float(zs),
        "ze": float(ze),
        "wxMm": float(geo["wx"] * 1e3),
        "wzMm": float(geo["wz"] * 1e3),
        "areaPercent": float(100.0 * geo["area"] / cfg.domain_area) if cfg.domain_area else 0.0,
        "roughEdges": int(sum(ei.values())),
        "curves": curves,
    }


def _preview_canvas_data(cfg, active_tab):
    if active_tab == "Domain":
        return {
            "mode": "domain",
            "width": float(cfg.width),
            "height": float(cfg.height),
            "widthMm": float(cfg.width * 1e3),
            "heightMm": float(cfg.height * 1e3),
            "layer": _layer_canvas_data(cfg) if cfg.medium_type == "layer" else None,
        }

    matrix_diag = gen.psd_diagnostics(
        cfg.dist_type, cfg.r_min, cfg.r_max, cfg.num_sizes,
        cfg.ln_sigma, cfg.ln_median, cfg.nm_mean, cfg.nm_std,
        cfg.custom_radii, cfg.custom_weights,
    )
    data = {
        "mode": "combined",
        "mediumType": cfg.medium_type,
        "matrix": _psd_canvas_data(matrix_diag, "Matrix PSD (sampled)", "#d2b48c"),
        "isLayer": cfg.medium_type == "layer",
    }
    if cfg.medium_type == "layer":
        fine_diag = gen.psd_diagnostics(
            cfg.layer_dist_type, cfg.layer_r_min, cfg.layer_r_max,
            cfg.layer_num_sizes, cfg.layer_ln_sigma, cfg.layer_ln_median,
            cfg.layer_nm_mean, cfg.layer_nm_std,
        )
        data["fine"] = _psd_canvas_data(fine_diag, "Heterogeneity grains PSD (sampled)", "#4682b4")
        data["layer"] = _layer_canvas_data(cfg)
    return data


class QmlBackend(QObject):
    formRevisionChanged = Signal()
    previewSourceChanged = Signal()
    previewDataChanged = Signal()
    resultSourceChanged = Signal()
    runningChanged = Signal()
    canSaveChanged = Signal()
    statusTextChanged = Signal()
    logTextChanged = Signal()
    badgesChanged = Signal()
    progressChanged = Signal()
    showMessage = Signal(str, str)
    openLogRequested = Signal()
    showResultRequested = Signal()
    showPreviewRequested = Signal()

    def __init__(self):
        super().__init__()
        self._tabs = _build_qml_tabs()
        self._values = {f.name: _value_to_form(getattr(DEFAULTS, f.name))
                        for f in fields(Config)}
        self._form_revision = 0
        self.cfg = DEFAULTS
        self.results = []
        self.thread = None
        self.worker = None
        self.preview_fig = Figure(facecolor="#ffffff")
        self.result_fig = Figure(figsize=(7, 6), facecolor="#ffffff")
        self._cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "qml", ".cache")
        os.makedirs(self._cache_dir, exist_ok=True)
        self._cache_token = 0
        self._preview_data = {}
        self._preview_source = ""
        self._result_source = ""
        self._running = False
        self._can_save = False
        self._status_text = "Ready."
        self._log_text = ""
        self._state_badge = ("idle", "idle")
        self._phi_badge = ("idle", "porosity -")
        self._throat_badge = ("idle", "throat -")
        self._primary_caption = "Packing"
        self._secondary_caption = "Heterogeneity"
        self._primary_progress = 0.0
        self._secondary_progress = 0.0
        self._primary_busy = False
        self._secondary_visible = False
        self._primary_detail = ""
        self._secondary_detail = ""
        self._progress_state = {}
        self._preview_error_shown = False
        self._thread_mode_noted = False
        self._active_tab = "Domain"

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(80)
        self._preview_timer.timeout.connect(self.refreshPreview)

    @Property("QVariantList", constant=True)
    def tabs(self):
        return self._tabs

    @Property(str, constant=True)
    def appIconSource(self):
        p = _app_icon_path()
        return QUrl.fromLocalFile(p).toString() if p else ""

    @Property(int, notify=formRevisionChanged)
    def formRevision(self):
        return self._form_revision

    @Property("QVariantMap", notify=formRevisionChanged)
    def fieldValues(self):
        return dict(self._values)

    @Property(str, notify=previewSourceChanged)
    def previewSource(self):
        return self._preview_source

    @Property("QVariantMap", notify=previewDataChanged)
    def previewData(self):
        return self._preview_data

    @Property(str, notify=resultSourceChanged)
    def resultSource(self):
        return self._result_source

    @Property(bool, notify=runningChanged)
    def running(self):
        return self._running

    @Property(bool, notify=canSaveChanged)
    def canSave(self):
        return self._can_save

    @Property(str, notify=statusTextChanged)
    def statusText(self):
        return self._status_text

    @Property(str, notify=logTextChanged)
    def logText(self):
        return self._log_text

    @Property(str, notify=badgesChanged)
    def stateBadgeText(self):
        return self._state_badge[1]

    @Property(str, notify=badgesChanged)
    def stateBadgeKind(self):
        return self._state_badge[0]

    @Property(str, notify=badgesChanged)
    def phiBadgeText(self):
        return self._phi_badge[1]

    @Property(str, notify=badgesChanged)
    def phiBadgeKind(self):
        return self._phi_badge[0]

    @Property(str, notify=badgesChanged)
    def throatBadgeText(self):
        return self._throat_badge[1]

    @Property(str, notify=badgesChanged)
    def throatBadgeKind(self):
        return self._throat_badge[0]

    @Property(str, notify=progressChanged)
    def primaryCaption(self):
        return self._primary_caption

    @Property(str, notify=progressChanged)
    def secondaryCaption(self):
        return self._secondary_caption

    @Property(float, notify=progressChanged)
    def primaryProgress(self):
        return self._primary_progress

    @Property(float, notify=progressChanged)
    def secondaryProgress(self):
        return self._secondary_progress

    @Property(bool, notify=progressChanged)
    def primaryBusy(self):
        return self._primary_busy

    @Property(bool, notify=progressChanged)
    def secondaryVisible(self):
        return self._secondary_visible

    @Property(str, notify=progressChanged)
    def primaryDetail(self):
        return self._primary_detail

    @Property(str, notify=progressChanged)
    def secondaryDetail(self):
        return self._secondary_detail

    @Slot(str, result="QVariant")
    def fieldValue(self, name):
        return self._values.get(name, "")

    def _set_field_value(self, name, value):
        if name not in self._values:
            return
        current = self._values[name]
        new_value = bool(value) if isinstance(current, bool) else str(value)
        if current == new_value:
            return
        self._values[name] = new_value
        self._form_revision += 1
        self.formRevisionChanged.emit()
        self.showPreviewRequested.emit()
        self._schedule_previews()
        QTimer.singleShot(0, self.refreshPreview)

    @Slot(str, "QVariant")
    def setField(self, name, value):
        self._set_field_value(name, value)

    @Slot(str, str)
    def setTextField(self, name, value):
        self._set_field_value(name, value)

    @Slot(str, bool)
    def setBoolField(self, name, value):
        self._set_field_value(name, value)

    @Slot(str)
    def setActiveTab(self, title):
        if not title:
            return
        if self._active_tab == title:
            self.refreshPreview()
            return
        self._active_tab = title
        self.refreshPreview()

    @Slot(str, result=bool)
    def isSectionVisible(self, title):
        if title == "Lognormal":
            return self._values.get("dist_type") == "lognormal"
        if title == "Normal":
            return self._values.get("dist_type") == "normal"
        if title == "Custom":
            return self._values.get("dist_type") == "custom"
        if title == "Band / patch":
            return (self._values.get("medium_type") == "layer"
                    and self._values.get("layer_shape") == "rectangle")
        if title == "Drawn shape":
            return (self._values.get("medium_type") == "layer"
                    and self._values.get("layer_shape") == "polygon")
        if title == "Interface":
            return (self._values.get("medium_type") == "layer"
                    and self._values.get("layer_shape") == "rectangle")
        if title == "Grains":
            return self._values.get("medium_type") == "layer"
        return True

    @Slot(str, result=bool)
    def isFieldVisible(self, name):
        if name == "snap_wall_threshold":
            return bool(self._values.get("snap_wall_enabled"))
        if name == "layer_shape":
            return self._values.get("medium_type") == "layer"
        if name == "heterogeneity_polygons":
            return False
        if name in ("min_throat", "k_candidates"):
            return self._values.get("throat_mode") in ("soft", "hard")
        if name in ("interface_amplitude", "interface_freqs"):
            # adaptive interface: only the on/off toggle is exposed in the GUI
            return False
        if name in ("layer_ln_sigma", "layer_ln_median"):
            return self._values.get("layer_dist_type") == "lognormal"
        if name in ("layer_nm_mean", "layer_nm_std"):
            return self._values.get("layer_dist_type") == "normal"
        return True

    def _schedule_previews(self):
        if not getattr(self, "_running", False):
            self._set_status("Updating preview...")
        self._preview_timer.start()

    def read_config(self, strict: bool = True) -> Config:
        kwargs = {}
        for f in fields(Config):
            name = f.name
            default = getattr(DEFAULTS, name)
            raw = self._values[name]
            try:
                if isinstance(default, bool):
                    kwargs[name] = bool(raw)
                elif isinstance(default, tuple) or default is None:
                    kwargs[name] = _parse_tuple(str(raw))
                elif isinstance(default, int):
                    kwargs[name] = int(float(str(raw).strip()))
                elif isinstance(default, float):
                    kwargs[name] = float(str(raw).strip())
                else:
                    kwargs[name] = str(raw)
            except (ValueError, TypeError):
                if strict:
                    raise
                kwargs[name] = getattr(self.cfg, name, default)
        return Config(**kwargs)

    def _source_for(self, path):
        return QUrl.fromLocalFile(os.path.abspath(path)).toString()

    def _set_preview_source(self, path):
        self._preview_source = self._source_for(path)
        self.previewSourceChanged.emit()

    def _set_preview_data(self, data):
        self._preview_data = data
        self.previewDataChanged.emit()

    def _set_result_source(self, path):
        self._result_source = self._source_for(path)
        self.resultSourceChanged.emit()

    def _set_status(self, text):
        self._status_text = text
        self.statusTextChanged.emit()

    def _next_cache_path(self, stem):
        self._cache_token += 1
        return os.path.join(self._cache_dir, f"{stem}_{self._cache_token:06d}.png")

    def _set_badge(self, badge, kind, text):
        if badge == "state":
            self._state_badge = (kind, text)
        elif badge == "phi":
            self._phi_badge = (kind, text)
        elif badge == "throat":
            self._throat_badge = (kind, text)
        self.badgesChanged.emit()

    def _set_running(self, running):
        if self._running == running:
            return
        self._running = running
        self.runningChanged.emit()

    def _set_can_save(self, can_save):
        if self._can_save == can_save:
            return
        self._can_save = can_save
        self.canSaveChanged.emit()

    def _set_progress(self, *, primary=None, secondary=None, primary_busy=None,
                      secondary_visible=None, primary_caption=None,
                      secondary_caption=None):
        if primary is not None:
            self._primary_progress = max(0.0, min(1.0, float(primary)))
        if secondary is not None:
            self._secondary_progress = max(0.0, min(1.0, float(secondary)))
        if primary_busy is not None:
            self._primary_busy = bool(primary_busy)
        if secondary_visible is not None:
            self._secondary_visible = bool(secondary_visible)
        if primary_caption is not None:
            self._primary_caption = primary_caption
        if secondary_caption is not None:
            self._secondary_caption = secondary_caption
        self.progressChanged.emit()

    def _reset_progress(self, is_layer):
        self._primary_detail = ""
        self._secondary_detail = ""
        self._progress_state = {}
        self._set_progress(
            primary=0.0,
            secondary=0.0,
            primary_busy=False,
            secondary_visible=is_layer,
            primary_caption="Background" if is_layer else "Packing",
            secondary_caption="Heterogeneity",
        )

    def _log(self, text, clear=False):
        self._log_text = text if clear else (self._log_text + ("\n" if self._log_text else "") + text)
        self.logTextChanged.emit()

    @Slot()
    def refreshPreview(self):
        if self._running:
            return
        try:
            cfg = self.read_config(strict=False)
        except Exception:
            return
        try:
            self._set_preview_data(_preview_canvas_data(cfg, self._active_tab))
            self.cfg = cfg
            self._set_status("Preview updated.")
        except Exception:
            self._report_preview_error(traceback.format_exc())

    def _report_preview_error(self, tb):
        sys.stderr.write("\n[preview error]\n" + tb + "\n")
        if not self._preview_error_shown:
            self._preview_error_shown = True
            self._log("Live preview error (first occurrence):\n" + tb)
            self.openLogRequested.emit()
            self._set_status("Live preview failed - see log.")

    def draw_result(self, idx):
        res = self.results[idx]
        gen.make_overview_figure(self.cfg, res, res.get("net"), fig=self.result_fig)
        path = self._next_cache_path("result")
        self.result_fig.savefig(path, dpi=170, bbox_inches="tight",
                                facecolor="#ffffff")
        self._set_result_source(path)

    @Slot()
    def generate(self):
        try:
            cfg = self.read_config()
        except Exception as e:
            self.showMessage.emit("Invalid input", f"Could not read the form:\n{e}")
            return
        if cfg.medium_type == "layer" and getattr(cfg, "layer_shape", "rectangle") == "polygon":
            if not gen.make_layer_geometry(cfg).get("polygons"):
                self.showMessage.emit(
                    "Incomplete polygon",
                    "Draw at least three points for a custom heterogeneity polygon.",
                )
                return
        try:
            n_est, warns = gen.check_feasibility(cfg)
        except Exception as e:
            self.showMessage.emit("Config error", str(e))
            return

        self._log(f"Estimated grains: ~{n_est:,}", clear=True)
        for wmsg in warns:
            self._log("Warning: " + wmsg)
        if warns:
            self.openLogRequested.emit()
            if cfg.strict_feasibility:
                self.showMessage.emit(
                    "Aborted",
                    "strict_feasibility=True with warnings present.",
                )
                return

        self.cfg = cfg
        self._set_running(True)
        self._reset_progress(is_layer=_is_heterogeneous_mode(cfg.medium_type))
        self._set_badge("state", "run", "running...")
        self._set_badge("phi", "idle", "porosity -")
        self._set_badge("throat", "idle", "throat -")
        self._set_status("Generating...")

        self.thread = QThread()
        self.worker = GenWorker(cfg)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.mode.connect(self.on_mode)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.start()

    @Slot(str)
    def on_mode(self, mode):
        if mode == "process":
            self._set_status("Generating in a separate process - UI stays responsive.")
        elif not self._thread_mode_noted:
            self._thread_mode_noted = True
            self._log("Running inside an interactive kernel (Jupyter/Spyder): heavy "
                      "generation runs in a background THREAD, so the UI can feel laggy "
                      "while it works. For a fully responsive UI, launch the app standalone "
                      "from a terminal: python rsa_porous_media_gui.py")
            self.openLogRequested.emit()

    @Slot(str, float, int, int)
    def on_progress(self, msg, frac, k, n):
        ens = f"realisation {k + 1}/{n} - " if n > 1 else ""

        if "[analysis]" in msg:
            self._set_progress(primary_busy=True)
            self._set_badge("state", "run", "analyzing...")
            self._set_status(ens + "analyzing throat network...")
            return

        pct = max(0.0, min(1.0, frac))
        stats = _progress_stats(msg)
        if "heterogeneity:grains" in msg or "layer:fine" in msg:
            self._primary_detail = _format_progress_detail(
                self._progress_state, "primary", 1.0, None, freeze=True)
            self._secondary_detail = _format_progress_detail(
                self._progress_state, "secondary", pct, stats, freeze=pct >= 1.0)
            self._set_progress(primary=1.0, secondary=pct, primary_busy=False)
        elif "heterogeneity:bg" in msg or "layer:bg" in msg:
            self._primary_detail = _format_progress_detail(
                self._progress_state, "primary", pct, stats, freeze=pct >= 1.0)
            self._set_progress(primary=pct, secondary=0.0, primary_busy=False)
        else:
            self._primary_detail = _format_progress_detail(
                self._progress_state, "primary", pct, stats, freeze=pct >= 1.0)
            self._set_progress(primary=pct, primary_busy=False)
        self._set_status(ens + msg)

    @Slot(list)
    def on_finished(self, results):
        self._set_running(False)
        if not results:
            self._set_badge("state", "warn", "no result")
            self._log("No realisations produced.")
            return
        self.results = results
        for k, res in enumerate(results):
            m = res["meta"]
            line = (f"[r{k}] seed={res['seed']}  N={m['n']}  "
                    f"phi={m['phi']:.4f} (target {self.cfg.target_porosity})")
            if m["phi"] > self.cfg.target_porosity + 0.01:
                line += " - jammed above target"
            self._log(line)
            status = gen.throat_floor_status(self.cfg, res.get("net"))
            if status:
                self._log("   " + status)

        m0 = self.results[0]["meta"]
        jammed = m0["phi"] > self.cfg.target_porosity + 0.01
        self._set_badge(
            "phi",
            "warn" if jammed else "ok",
            f"phi {m0['phi']:.3f}" + (" (jammed)" if jammed else ""),
        )
        net0 = self.results[0].get("net")
        if net0 is None or self.cfg.throat_mode == "none":
            self._set_badge("throat", "idle", "throat -")
        else:
            nbf = net0.get("n_below_floor") or 0
            t_um = self.cfg.min_throat * 1e6
            if self.cfg.throat_mode == "hard":
                ok = net0["min_um"] >= t_um - 1e-6
                self._set_badge(
                    "throat",
                    "ok" if ok else "bad",
                    f"min throat {net0['min_um']:.0f}um "
                    + (">=" if ok else "<") + f" {t_um:.0f}",
                )
            else:
                n_tot = int(net0.get("n_throats") or 0)
                pct = f" ({100.0 * nbf / n_tot:.1f}%)" if n_tot else ""
                self._set_badge(
                    "throat",
                    "warn" if nbf else "ok",
                    f"{nbf} below {t_um:.0f}um{pct} (soft)",
                )

        self._set_progress(primary=1.0, secondary=1.0, primary_busy=False)
        self._set_badge("state", "ok", "done")
        self.draw_result(0)
        self.showResultRequested.emit()
        self._set_can_save(True)
        self._set_status("Done.")
        self.refreshPreview()

    @Slot(str)
    def on_failed(self, msg):
        self._set_running(False)
        self._set_progress(primary_busy=False)
        if msg.startswith("Cancelled"):
            self._set_badge("state", "warn", "cancelled")
            self._set_status("Cancelled.")
            self._log("Cancelled.")
        else:
            self._set_badge("state", "bad", "failed")
            self.showMessage.emit("Generation failed", msg)
            self._log("ERROR:\n" + msg)

    @Slot()
    def stop(self):
        if self.worker is not None:
            self.worker.cancel()
            self._set_status("Stopping...")

    @Slot()
    def saveGeometry(self):
        if not self.results:
            return
        res = self.results[0]
        ext = self.cfg.output_format
        path, _ = QFileDialog.getSaveFileName(
            None, "Save geometry",
            f"{self.cfg.out_basename}.{ext}",
            f"Geometry (*.{ext})",
        )
        if not path:
            return
        stem = os.path.splitext(path)[0]
        out = gen.write_geometry(self.cfg, res["centers"], res["radii"], stem)
        self._set_status(f"Saved {out}")

    @Slot()
    def saveCsv(self):
        if not self.results or not self.results[0].get("net"):
            self.showMessage.emit("No throats", "Run with throat analysis enabled first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            None, "Save throat CSV",
            f"{self.cfg.out_basename}_throats.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        gen.write_throat_csv(self.results[0]["net"], path)
        self._set_status(f"Saved {path}")

    @Slot()
    def saveFigure(self):
        if self.result_fig is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            None, "Save figure",
            f"{self.cfg.out_basename}_overview.png",
            "PNG (*.png);;PDF (*.pdf)",
        )
        if not path:
            return
        self.result_fig.savefig(path, dpi=200, bbox_inches="tight")
        self._set_status(f"Saved {path}")


def _run_qml_app(app):
    # Main.qml is the single-board layout (all parameter cards visible at
    # once). The previous tabbed layout is kept as MainTabbed.qml and can be
    # selected with RSA_GUI_TABBED=1.
    qml_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml")
    qml_name = "MainTabbed.qml" if os.environ.get("RSA_GUI_TABBED") == "1" else "Main.qml"
    qml_file = os.path.join(qml_dir, qml_name)
    if not os.path.exists(qml_file):
        qml_file = os.path.join(qml_dir, "Main.qml")
    if not os.path.exists(qml_file):
        return False

    from PySide6.QtQml import QQmlApplicationEngine

    backend = QmlBackend()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(qml_file))
    if not engine.rootObjects():
        return False

    # Keep Python-owned QML objects alive for the whole event loop.
    app._qml_engine = engine
    app._qml_backend = backend

    if ENGINE_MISSING:
        QTimer.singleShot(
            0,
            lambda: backend.showMessage.emit(
                "Engine out of date",
                "rsa_porous_media.py is missing required functions:\n  "
                + ", ".join(ENGINE_MISSING)
                + "\n\nThe live preview and/or generation will not work. If you are in a "
                  "Jupyter/IPython kernel, restart the kernel. Otherwise update "
                  "rsa_porous_media.py to the current version.",
            ),
        )
    return True


def main():
    app = QApplication(sys.argv)
    from PySide6.QtGui import QFont
    f = QFont("Segoe UI", 10)
    f.setStyleStrategy(QFont.PreferAntialias)
    app.setFont(f)
    icon_path = _app_icon_path()
    if icon_path:
        if sys.platform == "win32":
            # without an explicit AppUserModelID the Windows taskbar groups the
            # app under python.exe and shows the Python icon instead of ours
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "RSA.PorousMediaGenerator")
            except Exception:
                pass
        app.setWindowIcon(QIcon(icon_path))
    if os.environ.get("RSA_GUI_QML") == "1":
        try:
            if _run_qml_app(app):
                sys.exit(app.exec())
        except Exception:
            sys.stderr.write("\n[QML frontend failed; falling back to Widgets]\n"
                             + traceback.format_exc() + "\n")

    app.setStyleSheet(QSS)
    if ENGINE_MISSING:
        QMessageBox.warning(
            None, "Engine out of date",
            "rsa_porous_media.py is missing required functions:\n  "
            + ", ".join(ENGINE_MISSING)
            + "\n\nThe live preview and/or generation will not work. If you are in a "
              "Jupyter/IPython kernel, restart the kernel. Otherwise update "
              "rsa_porous_media.py to the current version.")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
