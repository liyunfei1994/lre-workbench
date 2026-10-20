"""Regression checks for streamed throttle plots and compact label layout.

The numeric fixtures are chart-only inputs with deliberately simple arrays;
they are not exported or represented as computed engine performance.
"""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
import warnings
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from workbench import WorkbenchWindow
from workbench_fonts import configure_fonts
from gui_pyside6 import ThrottleWorker


def chart_record(ratio, *, converged=True):
    """Return a labelled plotting fixture without running a physical model."""
    return {"throttle_ratio": ratio, "thrust_target_N": 2.4e6 * ratio,
            "pc_Pa": 33e6 * ratio, "converged": converged,
            "results": SimpleNamespace(summary={
                "thrust_N": 2.39e6 * ratio, "Isp_s": 310 + 20 * ratio,
                "mdot_total_kg_s": 700 * ratio, "chamber_temperature_K": 3500 + 50 * ratio,
                "ox_preburner_T_combustion_K": 710 + 30 * ratio,
                "fuel_preburner_T_combustion_K": 840 + 40 * ratio,
            })}


class ThrottleChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        configure_fonts(cls.app)
        cls.temporary = tempfile.TemporaryDirectory()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cls.window = WorkbenchWindow(recovery_directory=cls.temporary.name)
            configure_fonts(cls.app)
            cls.window.navigate("engchar")
            cls.window.engchar_tabs.setCurrentIndex(0)
            cls.window.show()
            cls.app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.window._remember_saved_case()
        cls.window.close()
        cls.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        cls.app.processEvents()
        cls.temporary.cleanup()

    def setUp(self):
        self.window._init_throttle_axes()
        self.window._partial_throttle = []
        self.window.last_throttle = []
        self.window.throttle_table.setRowCount(0)

    @staticmethod
    def sample():
        # Preserve the native scan order; omit only the failed point.
        return [chart_record(1.0), chart_record(0.85), chart_record(0.7, converged=False),
                chart_record(0.55), chart_record(0.4)]

    def assert_single_flow_curve(self, records):
        window = self.window
        accepted = [record for record in records if record["converged"]]
        self.assertEqual(len(window.throttle_fig.axes), 5, "Streamed updates must not accumulate secondary axes")
        self.assertEqual(len(window._ax_mdot.lines), 1, "Mass flow must have one current curve")
        flow = window._ax_mdot.lines[0]
        self.assertEqual(list(flow.get_xdata()), [record["throttle_ratio"] * 100 for record in accepted])
        self.assertEqual(list(flow.get_ydata()), [record["results"].summary["mdot_total_kg_s"] for record in accepted])
        self.assertEqual(len(window._ax_flow.lines), 1)
        pressure = window._ax_flow.lines[0]
        self.assertEqual(list(pressure.get_ydata()), [record["pc_Pa"] / 1e6 for record in accepted])

    def test_streamed_records_reuse_five_axes_and_replace_flow_curve(self):
        window = self.window
        self.assertEqual(len(window.throttle_fig.axes), 5)
        mass_axis = window._ax_mdot
        prefix = []
        for record in self.sample():
            prefix.append(record)
            window._on_throttle_record(record)
            self.app.processEvents()
            self.assertIs(window._ax_mdot, mass_axis)
            self.assert_single_flow_curve(prefix)
            self.assertEqual(window.throttle_table.rowCount(), len(prefix))
        # Final completion/repaint repeats the last full dataset. Previously
        # even this generated another overlaid right-side tick scale.
        for _ in range(8):
            window._update_throttle_charts(prefix)
            self.app.processEvents()
            self.assertIs(window._ax_mdot, mass_axis)
            self.assert_single_flow_curve(prefix)

    def test_new_scan_replaces_old_points_instead_of_retaining_curves(self):
        self.window._update_throttle_charts(self.sample())
        replacement = [chart_record(0.65), chart_record(0.9)]
        self.window._update_throttle_charts(replacement)
        self.assert_single_flow_curve(replacement)
        self.assertEqual(len(self.window._ax_thrust.lines), 2)
        self.assertEqual(len(self.window._ax_temp.lines), 3)
        self.assertEqual(list(self.window._ax_thrust.lines[0].get_ydata()),
                         [record["results"].summary["thrust_N"] / 1e3 for record in replacement])

    def test_gg_temperature_plot_uses_only_defined_cycle_temperatures(self):
        records = [chart_record(1.0), chart_record(0.7, converged=False), chart_record(0.4)]
        for record in records:
            record["results"].inputs = SimpleNamespace(cycle_type="gg")
            summary = record["results"].summary
            del summary["ox_preburner_T_combustion_K"]
            del summary["fuel_preburner_T_combustion_K"]
            summary["gg_T_combustion_K"] = 880 + 50 * record["throttle_ratio"]
        self.window._update_throttle_charts(records)
        accepted = [record for record in records if record["converged"]]
        lines = self.window._ax_temp.lines
        self.assertEqual(len(lines), 2, "GG must not draw nonexistent rich-side preburners at 0 K")
        self.assertEqual(list(lines[0].get_xdata()), [record["throttle_ratio"] * 100 for record in accepted])
        self.assertEqual(list(lines[0].get_ydata()),
                         [record["results"].summary["chamber_temperature_K"] for record in accepted])
        self.assertEqual(list(lines[1].get_ydata()),
                         [record["results"].summary["gg_T_combustion_K"] for record in accepted])
        labels = [text.get_text() for text in self.window._ax_temp.get_legend().get_texts()]
        self.assertTrue(any("燃气发生器" in label for label in labels))
        self.assertFalse(any("富氧" in label or "富燃" in label for label in labels))

    def test_valid_scan_start_clears_previous_table_and_graphs_before_first_record(self):
        window = self.window
        window.last_throttle = self.sample()
        window._populate_throttle(window.last_throttle)
        self.assert_single_flow_curve(window.last_throttle)
        self.assertGreater(window.throttle_table.rowCount(), 0)
        previous_worker = getattr(window, "_active_worker", None)
        try:
            # Keep real validation, state transitions, worker creation and
            # signal wiring; replace only dispatch so no calculation starts.
            with patch.object(window.thread_pool, "start") as dispatch:
                window.run_throttle()
            self.assertTrue(window._busy)
            worker = window._active_worker
            self.assertIsInstance(worker, ThrottleWorker)
            dispatch.assert_called_once_with(worker)
            self.assertEqual(window.last_throttle, [])
            self.assertEqual(window._partial_throttle, [])
            self.assertEqual(window.throttle_table.rowCount(), 0)
            self.assertEqual(len(window.throttle_fig.axes), 5)
            self.assertTrue(all(not axis.lines and not axis.collections for axis in window.throttle_fig.axes))
            self.assertFalse(window._ax_mdot.get_visible())
            # A first streamed record must populate only the new scan, through
            # the real signal connection established by run_throttle.
            first = chart_record(0.9)
            worker.signals.record_ready.emit(first)
            self.app.processEvents()
            self.assertEqual(window.throttle_table.rowCount(), 1)
            self.assertEqual(window.last_throttle, [first])
            self.assert_single_flow_curve([first])
        finally:
            window._set_busy(False, "测试结束")
            window._task_kind = None
            window._active_worker = previous_worker
            self.app.processEvents()

    def test_invalid_scan_input_keeps_previous_table_and_graphs(self):
        window = self.window
        previous = self.sample()
        window.last_throttle = previous
        window._populate_throttle(previous)
        before_rows = [[window.throttle_table.item(row, column).text()
                        for column in range(window.throttle_table.columnCount())]
                       for row in range(window.throttle_table.rowCount())]
        mass_axis = window._ax_mdot
        mass_curve = mass_axis.lines[0]
        original_pressure = window.input_edits["pc"].text()
        previous_worker = getattr(window, "_active_worker", None)
        try:
            window.input_edits["pc"].setText("nan")
            with patch.object(window.thread_pool, "start") as dispatch, \
                    patch("gui_pyside6.QMessageBox.warning") as warning:
                window.run_throttle()
            dispatch.assert_not_called()
            warning.assert_called_once()
            self.assertFalse(window._busy)
            self.assertIs(getattr(window, "_active_worker", None), previous_worker)
            self.assertEqual(window.last_throttle, previous)
            self.assertIs(window._ax_mdot, mass_axis)
            self.assertIs(window._ax_mdot.lines[0], mass_curve)
            self.assert_single_flow_curve(previous)
            after_rows = [[window.throttle_table.item(row, column).text()
                           for column in range(window.throttle_table.columnCount())]
                          for row in range(window.throttle_table.rowCount())]
            self.assertEqual(after_rows, before_rows)
        finally:
            window.input_edits["pc"].setText(original_pressure)
            window._change_timer.stop()
            window._case_recovery_timer.stop()
            window._remember_saved_case()
            window._task_kind = None
            self.app.processEvents()

    def test_empty_and_all_failed_updates_remove_previous_data(self):
        for empty_case in ([], [chart_record(0.8, converged=False), chart_record(0.4, converged=False)]):
            with self.subTest(records=len(empty_case)):
                self.window._update_throttle_charts(self.sample())
                self.assert_single_flow_curve(self.sample())
                self.window._update_throttle_charts(empty_case)
                self.app.processEvents()
                self.assertEqual(len(self.window.throttle_fig.axes), 5)
                self.assertFalse(self.window._ax_mdot.get_visible(), "No-data plots must hide the irrelevant right scale")
                for axis in self.window.throttle_fig.axes:
                    self.assertEqual(len(axis.lines), 0, "Old performance curves must disappear after reset or failed scan")
                    self.assertEqual(len(axis.collections), 0, "Old filled Isp area must also disappear")
                self.assertTrue(any(axis.texts for axis in self.window.throttle_fig.axes if axis.get_visible()))

    def test_compact_and_regular_rendered_labels_stay_within_canvas(self):
        window = self.window
        window._update_throttle_charts(self.sample())
        figure, canvas = window.throttle_fig, window.throttle_canvas
        original_size = figure.get_size_inches().copy()
        try:
            for width, height in ((640, 520), (900, 700)):
                with self.subTest(width=width, height=height):
                    # Test the actual minimum scrollable chart size and a
                    # larger pane. Do not shrink below the supported minimum.
                    figure.set_size_inches(width / figure.dpi, height / figure.dpi, forward=False)
                    canvas.draw()
                    renderer = canvas.get_renderer()
                    bounds = figure.bbox
                    flow_bounds = window._ax_flow.get_window_extent(renderer)
                    right_axis = window._ax_mdot.yaxis
                    self.assertEqual(right_axis.get_label_position(), "right")
                    self.assertEqual(right_axis.get_ticks_position(), "right")
                    right_label = right_axis.label.get_window_extent(renderer)
                    self.assertGreaterEqual(
                        right_label.x0, flow_bounds.x1,
                        f"Mass-flow label overlaps the pressure plot: {right_label.bounds}, axes {flow_bounds.bounds}")
                    lower, upper = sorted(right_axis.get_view_interval())
                    for tick in right_axis.get_major_ticks():
                        if not lower <= tick.get_loc() <= upper:
                            continue
                        self.assertFalse(tick.label1.get_visible(), "Mass-flow labels must not appear on the pressure side")
                        self.assertTrue(tick.label2.get_visible())
                        right_tick = tick.label2.get_window_extent(renderer)
                        self.assertGreaterEqual(right_tick.x0, flow_bounds.x1,
                                                "Mass-flow ticks must lie to the right of the plot")
                    checked = 0
                    for axis in figure.axes:
                        if not axis.get_visible():
                            continue
                        texts = [axis.title]
                        for coordinate_axis in (axis.xaxis, axis.yaxis):
                            if not coordinate_axis.get_visible():
                                continue
                            texts.extend((coordinate_axis.label, coordinate_axis.get_offset_text()))
                            lower, upper = sorted(coordinate_axis.get_view_interval())
                            # Locators return one extra tick beyond each edge;
                            # Axis.draw does not paint those outside its limits.
                            # Include only labels that are actually rendered.
                            for tick in (*coordinate_axis.get_major_ticks(), *coordinate_axis.get_minor_ticks()):
                                if lower <= tick.get_loc() <= upper:
                                    texts.extend((tick.label1, tick.label2))
                        for text in texts:
                            if not text.get_visible() or not text.get_text():
                                continue
                            extent = text.get_window_extent(renderer)
                            margin = 1.5  # Subpixel font rasterization tolerance.
                            message = f"{text.get_text()!r}: {extent.bounds}, canvas {bounds.bounds}"
                            self.assertGreaterEqual(extent.x0, bounds.x0 - margin, message)
                            self.assertGreaterEqual(extent.y0, bounds.y0 - margin, message)
                            self.assertLessEqual(extent.x1, bounds.x1 + margin, message)
                            self.assertLessEqual(extent.y1, bounds.y1 + margin, message)
                            checked += 1
                    self.assertGreater(checked, 20, "Layout verification must include ticks as well as titles")
        finally:
            figure.set_size_inches(original_size, forward=False)
            canvas.draw()


if __name__ == "__main__":
    unittest.main()
