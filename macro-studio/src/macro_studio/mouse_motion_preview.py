import math
from bisect import bisect_left
from dataclasses import replace
from itertools import pairwise
from secrets import randbits

from PySide6.QtCore import QElapsedTimer, QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from .mouse_motion import MotionGenerator, PreviewContext


def speed_samples(trajectory):
    dx = trajectory.target[0] - trajectory.start[0]
    dy = trajectory.target[1] - trajectory.start[1]
    distance = math.hypot(dx, dy)
    if not distance:
        return ()
    ux, uy = dx / distance, dy / distance
    rows = []
    for first, second in pairwise(trajectory.points):
        vx, vy = second.x - first.x, second.y - first.y
        elapsed = (second.time_offset_ns - first.time_offset_ns) / 1e9
        time_ms = (first.time_offset_ns + second.time_offset_ns) / 2e6
        forward, total = (vx * ux + vy * uy) / elapsed, math.hypot(vx, vy) / elapsed
        if not math.isfinite(total):
            raise ValueError("속도 범위를 표시할 수 없습니다.")
        rows.append((time_ms, max(0, forward), total))
    return tuple(rows)


class PreviewResult(QObject):
    ready = Signal(int, object, object, str)


class PreviewJob(QRunnable):
    def __init__(self, revision, profile, context):
        super().__init__()
        self.revision = revision
        self.profile = profile
        self.context = context
        self.result = PreviewResult()

    def run(self):
        try:
            trajectory = MotionGenerator.generate(
                self.context.start_xy, self.context.target_xy, self.profile, self.context.seed,
                self.context.duration_override_ms,
            )
            speeds = speed_samples(trajectory)
        except (ValueError, ArithmeticError) as error:
            self.result.ready.emit(self.revision, None, (), str(error))
        else:
            self.result.ready.emit(self.revision, trajectory, speeds, "")


class MotionPlot(QWidget):
    def __init__(self, *, speed=False):
        super().__init__()
        self.speed = speed
        self.setMinimumHeight(138 if speed else 182)
        self.setAccessibleName("시간별 속도 그래프" if speed else "2D 마우스 궤적")
        self.trajectory = None
        self.speeds = ()
        self.previous = None
        self.baseline = True
        self.magnification = 1
        self.time_ns = 0
        self.cache = None

    def show_data(self, trajectory, speeds, *, previous=None, baseline=True, magnification=1):
        self.trajectory, self.speeds, self.previous = trajectory, speeds, previous
        self.baseline, self.magnification = baseline, magnification
        self.cache = None
        self.update()

    def show_time(self, time_ns):
        self.time_ns = time_ns
        self.update()

    def resizeEvent(self, event):
        self.cache = None
        super().resizeEvent(event)

    def _magnify(self, x, y):
        if self.magnification == 1:
            return x, y
        t = self.trajectory
        dx, dy = t.target[0] - t.start[0], t.target[1] - t.start[1]
        distance = math.hypot(dx, dy)
        if not distance:
            return x, y
        nx, ny = -dy / distance, dx / distance
        offset = ((x - t.start[0]) * nx + (y - t.start[1]) * ny) * (self.magnification - 1)
        return x + nx * offset, y + ny * offset

    @staticmethod
    def _sample(rows):
        stride = max(1, (len(rows) + 2399) // 2400)
        return rows if stride == 1 else (*rows[::stride], rows[-1])

    @staticmethod
    def _tick_label(value):
        if abs(value) >= 1e6:
            return f"{value:.2g}"
        return f"{value:,.0f}" if abs(value) >= 100 else f"{value:.3g}"

    @staticmethod
    def _path(rows, project):
        path = QPainterPath()
        for index, (x, y) in enumerate(rows):
            point = project(x, y)
            if index:
                path.lineTo(point)
            else:
                path.moveTo(point)
        return path

    def _geometry(self, rect):
        t = self.trajectory
        if self.speed:
            width = max(1, t.duration_ns / 1e6)
            height = max(1, max((row[2] for row in self.speeds), default=0)) * 1.05

            def project(x, y):
                return QPointF(rect.left() + x / width * rect.width(), rect.bottom() - y / height * rect.height())

            rows = self._sample(self.speeds)
            primary = self._path(((time, speed) for time, speed, _ in rows), project)
            secondary = self._path(((time, speed) for time, _, speed in rows), project)
            return project, primary, secondary, (0, width, height, 0)

        def coordinates(trajectory):
            return [self._magnify(point.x, point.y) for point in self._sample(trajectory.points)]

        current = coordinates(t)
        previous = coordinates(self.previous) if self.previous else []
        xs, ys = zip(*(current + previous))
        cx, cy = min(xs) / 2 + max(xs) / 2, min(ys) / 2 + max(ys) / 2
        scale = min(rect.width() / max(2, max(xs) - min(xs)), rect.height() / max(2, max(ys) - min(ys))) * 0.86

        def project(x, y):
            return QPointF(rect.center().x() + (x - cx) * scale, rect.center().y() + (y - cy) * scale)

        half_width, half_height = rect.width() / (2 * scale), rect.height() / (2 * scale)
        return (project, self._path(current, project), self._path(previous, project),
                (cx - half_width, cx + half_width, cy - half_height, cy + half_height))

    def _virtual_cursor(self):
        points = self.trajectory.points
        index = bisect_left(points, self.time_ns, key=lambda point: point.time_offset_ns)
        if index == 0:
            return points[0].x, points[0].y
        if index == len(points):
            return points[-1].x, points[-1].y
        first, second = points[index - 1:index + 1]
        if self.time_ns == second.time_offset_ns:
            return second.x, second.y
        fraction = (self.time_ns - first.time_offset_ns) / (second.time_offset_ns - first.time_offset_ns)
        return first.x + (second.x - first.x) * fraction, first.y + (second.y - first.y) * fraction

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if self.trajectory is None:
            return
        rect = QRectF(self.rect()).adjusted(66, 16, -28, -27)
        if self.cache is None:
            self.cache = self._geometry(rect)
        project, primary, secondary, (left, right, top, bottom) = self.cache
        painter.setPen(QPen(QColor("#dce5ee")))
        painter.drawRect(rect)
        for tick in range(5):
            ratio = tick / 4
            x = rect.left() + ratio * rect.width()
            y = rect.top() + ratio * rect.height()
            painter.setPen(QColor("#e9eef4"))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor("#475b74"))
            painter.drawText(QRectF(x - 27, rect.bottom() + 4, 54, 18), Qt.AlignmentFlag.AlignCenter,
                             self._tick_label(left + ratio * (right - left)))
            painter.drawText(QRectF(3, y - 9, 56, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             self._tick_label(top + ratio * (bottom - top)))
        painter.setClipRect(rect.adjusted(-1, -1, 1, 1))
        if self.speed:
            painter.setPen(QPen(QColor("#13827d"), 1.3, Qt.PenStyle.DashLine))
            painter.drawPath(secondary)
        else:
            painter.setPen(QPen(QColor("#64748b"), 1.4, Qt.PenStyle.DashLine))
            if self.baseline:
                painter.drawLine(project(*self.trajectory.start), project(*self.trajectory.target))
            painter.setPen(QPen(QColor("#9874b2"), 1.6, Qt.PenStyle.DashLine))
            painter.drawPath(secondary)
        painter.setPen(QPen(QColor("#2455cb"), 1.9))
        painter.drawPath(primary)
        if self.speed:
            x = project(self.time_ns / 1e6, 0).x()
            painter.setPen(QPen(QColor("#a14613"), 1.3))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        else:
            painter.setBrush(QColor("#ffffff"))
            t = self.trajectory
            endpoints = ((t.start, "시작·목표"),) if t.start == t.target else (
                (t.start, "시작"), (t.target, "목표"))
            for coordinate, label in endpoints:
                point = project(*coordinate)
                painter.setPen(QPen(QColor("#2455cb"), 1.9))
                painter.drawEllipse(point, 4, 4)
                painter.setPen(QColor("#475b74"))
                label_y = point.y() + 6 if point.y() - rect.top() < 25 else point.y() - 24
                painter.drawText(QRectF(point.x() - 32, label_y, 64, 18),
                                 Qt.AlignmentFlag.AlignCenter, label)
            painter.setPen(QPen(QColor("#ffffff"), 1.2))
            painter.setBrush(QColor("#a14613"))
            painter.drawEllipse(project(*self._magnify(*self._virtual_cursor())), 5.5, 5.5)


class MotionPreviewDialog(QDialog):
    def __init__(self, parent, profile):
        super().__init__(parent, windowTitle="마우스 궤적 미리보기")
        self.profile = profile
        self.trajectory = self.previous = None
        self.speeds = ()
        self.revision = 0
        self.rendered_revision = None
        self.job = None
        self.position_ns = 0
        self.play_from_ns = 0
        self.play_clock = QElapsedTimer()
        self.resize(850, 715)
        layout = QVBoxLayout(self)
        title = QLabel(profile.name, objectName="section")
        title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(title)

        defaults = PreviewContext()
        self.start_x, self.start_y = [self.number_field(value) for value in defaults.start_xy]
        self.target_x, self.target_y = [self.number_field(value) for value in defaults.target_xy]
        self.duration = self.number_field(profile.duration_ms)
        self.sampling = self.number_field(profile.sampling_hz)
        self.seed = self.number_field(defaults.seed)
        self.fixed = QCheckBox("시드 고정", checked=True)
        self.fixed.toggled.connect(self.seed.setEnabled)
        self.regenerate = QPushButton("새 시드로 다시 생성", clicked=lambda: self.request(new_seed=True))
        form = QGridLayout()
        layout.addLayout(form)
        for column, (label, widget) in enumerate((
            ("시작 X", self.start_x), ("시작 Y", self.start_y), ("목표 X", self.target_x), ("목표 Y", self.target_y),
            ("시간 (ms)", self.duration), ("샘플링 (Hz)", self.sampling), ("시드", self.seed),
        )):
            caption = QLabel(label)
            caption.setBuddy(widget)
            widget.setAccessibleName(label)
            form.addWidget(caption, 0, column)
            form.addWidget(widget, 1, column)
        form.addWidget(self.fixed, 0, 7)
        form.addWidget(self.regenerate, 1, 7)

        self.path_plot = MotionPlot()
        self.speed_plot = MotionPlot(speed=True)
        for name, description, plot, stretch in (
            ("논리적 예상 궤적 · 화면 좌표 (px)", "○ 시작·목표   ● 가상 커서", self.path_plot, 3),
            ("시간 (ms) · 속도 (px/s)", "파란 실선 종방향 속도   ·   청록 점선 전체 속도", self.speed_plot, 2),
        ):
            group = QGroupBox()
            group_layout = QVBoxLayout(group)
            heading = QHBoxLayout()
            heading.addWidget(QLabel(name, objectName="section"))
            heading.addStretch()
            heading.addWidget(QLabel(description, objectName="muted"))
            group_layout.addLayout(heading)
            group_layout.addWidget(plot, 1)
            layout.addWidget(group, stretch)

        display = QHBoxLayout()
        layout.addLayout(display)
        self.baseline = QCheckBox("기준 직선 표시", checked=True)
        self.compare = QCheckBox("이전 궤적과 겹쳐 비교")
        self.magnification = self.number_field(1)
        self.magnification.setAccessibleName("횡방향 흔들림 표시 배율")
        self.magnification.setMaximumWidth(80)
        display.addWidget(self.baseline)
        display.addWidget(self.compare)
        display.addStretch()
        label = QLabel("흔들림 표시 배율")
        label.setBuddy(self.magnification)
        display.addWidget(label)
        display.addWidget(self.magnification)
        self.baseline.toggled.connect(self.redraw)
        self.compare.toggled.connect(self.redraw)
        self.magnification.editingFinished.connect(self.redraw)

        controls = QHBoxLayout()
        layout.addLayout(controls)
        self.play = QPushButton("재생", clicked=self.toggle_play, enabled=False)
        self.reset = QPushButton("처음부터", clicked=self.reset_playback, enabled=False)
        for button in (self.regenerate, self.play, self.reset):
            button.setAutoDefault(False)
        self.status = QLabel(objectName="muted", wordWrap=True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        controls.addWidget(self.play)
        controls.addWidget(self.reset)
        controls.addWidget(self.status, 1)
        close = QPushButton("닫기", clicked=self.reject)
        close.setAutoDefault(False)
        controls.addWidget(close)

        self.debounce = QTimer(self, interval=90, singleShot=True)
        self.debounce.timeout.connect(self.generate)
        self.timer = QTimer(self, interval=16)
        self.timer.timeout.connect(self.advance_playback)
        self.finished.connect(lambda: (self.timer.stop(), self.debounce.stop()))
        for field in (self.start_x, self.start_y, self.target_x, self.target_y, self.duration, self.sampling, self.seed):
            field.textEdited.connect(lambda _text: self.request())
        self.fixed.toggled.connect(lambda _checked: self.request())
        self.request()

    @staticmethod
    def number_field(value):
        field = QLineEdit(str(value))
        field.setMinimumWidth(60)
        return field

    def request(self, *, new_seed=False):
        if new_seed or not self.fixed.isChecked():
            seed = randbits(32)
            self.seed.setText(str(seed if str(seed) != self.seed.text() else seed + 1))
        self.revision += 1
        self.timer.stop()
        self.play.setText("재생")
        self.play.setEnabled(False)
        self.reset.setEnabled(False)
        self.status.setText("궤적 계산 중…")
        self.debounce.start()

    def generate(self):
        if self.job is not None:
            return
        try:
            profile = replace(self.profile, sampling_hz=int(self.sampling.text()))
            context = PreviewContext(
                start_xy=(float(self.start_x.text()), float(self.start_y.text())),
                target_xy=(float(self.target_x.text()), float(self.target_y.text())),
                duration_override_ms=float(self.duration.text()),
                seed=int(self.seed.text()),
            )
        except ValueError as error:
            self.status.setText(f"입력값을 확인하세요. {error}")
            return
        self.job = PreviewJob(self.revision, profile, context)
        self.job.result.ready.connect(self.show_result)
        QThreadPool.globalInstance().start(self.job)

    def show_result(self, revision, trajectory, speeds, error):
        self.job = None
        if revision != self.revision:
            if not self.debounce.isActive():
                self.generate()
            return
        if error:
            self.status.setText(f"궤적을 계산할 수 없습니다. {error}")
            return
        self.previous, self.trajectory = self.trajectory, trajectory
        self.speeds = speeds
        self.rendered_revision = revision
        self.reset_playback()
        self.redraw()
        enabled = trajectory.duration_ns > 0
        self.play.setEnabled(enabled)
        self.reset.setEnabled(enabled)

    def redraw(self):
        trajectory = self.trajectory
        if trajectory is None:
            return
        notice = ""
        try:
            magnification = float(self.magnification.text())
            if not math.isfinite(magnification) or not 0.1 <= magnification <= 20:
                raise ValueError()
        except ValueError:
            magnification = self.path_plot.magnification
            notice = "흔들림 표시 배율은 0.1~20 사이 값으로 입력하세요."
        previous = self.previous if self.compare.isChecked() else None
        subtitle = f"시드 {trajectory.seed} · {len(trajectory.points):,}개 점"
        if previous and (previous.start, previous.target) != (trajectory.start, trajectory.target):
            previous = None
            subtitle = "이전 궤적과 비교하려면 시작점과 목표점이 같아야 합니다."
        self.path_plot.show_data(trajectory, self.speeds, previous=previous,
                                 baseline=self.baseline.isChecked(), magnification=magnification)
        self.speed_plot.show_data(trajectory, self.speeds)
        if self.rendered_revision == self.revision:
            self.status.setText(notice or subtitle)

    def seek(self, position_ns):
        self.position_ns = position_ns
        self.path_plot.show_time(position_ns)
        self.speed_plot.show_time(position_ns)

    def reset_playback(self):
        self.timer.stop()
        self.seek(0)
        self.play.setText("재생")

    def toggle_play(self):
        if not self.trajectory or not self.trajectory.duration_ns:
            return
        if self.timer.isActive():
            self.advance_playback()
            self.timer.stop()
            self.play.setText("재생")
            return
        if self.position_ns >= self.trajectory.duration_ns:
            self.seek(0)
        self.play_from_ns = self.position_ns
        self.play_clock.start()
        self.timer.start()
        self.play.setText("일시정지")

    def advance_playback(self):
        duration = self.trajectory.duration_ns
        self.seek(min(duration, self.play_from_ns + self.play_clock.nsecsElapsed()))
        if self.position_ns == duration:
            self.timer.stop()
            self.play.setText("재생")
