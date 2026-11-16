from time import monotonic

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from macro_studio.mouse_motion import DEFAULT_PROFILE, MouseTrajectory, TimedPoint
from macro_studio.mouse_motion_preview import MotionPreviewDialog, speed_samples


def wait_for(predicate):
    deadline = monotonic() + 3
    while not predicate():
        assert monotonic() < deadline
        QTest.qWait(10)


def test_speed_graph_reads_displacements_and_times_from_the_same_trajectory():
    points = (TimedPoint(0, 0, 0), TimedPoint(1_000_000_000, 3, 4), TimedPoint(2_000_000_000, 6, 0))
    trajectory = MouseTrajectory(points, (0, 0), (6, 0), 2_000_000_000, DEFAULT_PROFILE.id, 7, 1)
    assert speed_samples(trajectory) == ((500, 3, 5), (1500, 3, 5))


def test_preview_reproduces_seed_regenerates_compares_and_animates_without_changing_source(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    dialog = MotionPreviewDialog(None, DEFAULT_PROFILE)
    dialog.show()
    try:
        wait_for(lambda: dialog.trajectory is not None)
        original = dialog.trajectory
        dialog.regenerate.click()
        wait_for(lambda: dialog.trajectory.seed != original.seed)
        assert dialog.trajectory.points != original.points
        dialog.compare.setChecked(True)
        assert dialog.path_plot.previous == original

        dialog.seed.setText(str(original.seed))
        dialog.seed.textEdited.emit(dialog.seed.text())
        wait_for(lambda: dialog.trajectory is not original and dialog.trajectory == original)
        dialog.magnification.setText("8")
        dialog.magnification.editingFinished.emit()
        assert dialog.path_plot.magnification == 8
        assert dialog.trajectory.points == original.points
        assert dialog.speed_plot.speeds == speed_samples(original)

        dialog.play.click()
        wait_for(lambda: dialog.position_ns > 0)
        assert dialog.path_plot._virtual_cursor() != original.start
        dialog.play.click()
        paused = dialog.position_ns
        QTest.qWait(55)
        assert dialog.position_ns == paused
        dialog.reset.click()
        assert dialog.path_plot._virtual_cursor() == original.start

        dialog.target_x.setText(dialog.start_x.text())
        dialog.target_y.setText(dialog.start_y.text())
        dialog.target_y.textEdited.emit(dialog.target_y.text())
        wait_for(lambda: dialog.trajectory.duration_ns == 0)
        assert len(dialog.path_plot.trajectory.points) == 1
        assert not dialog.play.isEnabled()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
