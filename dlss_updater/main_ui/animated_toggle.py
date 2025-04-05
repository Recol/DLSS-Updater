from PyQt6.QtCore import (
    Qt,
    QSize,
    QPoint,
    QPointF,
    QRectF,
    QEasingCurve,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    pyqtSlot,
    pyqtProperty,
)
from PyQt6.QtWidgets import QCheckBox
from PyQt6.QtGui import QColor, QBrush, QPaintEvent, QPen, QPainter


class AnimatedToggle(QCheckBox):
    def __init__(
        self,
        parent=None,
        track_color="#888888",
        thumb_color="#FFFFFF",
        track_active_color="#2D6E88",
        thumb_active_color="#FFFFFF",
        animation_duration=120,
    ):
        super().__init__(parent)

        # Colors
        self._track_color = QColor(track_color)
        self._thumb_color = QColor(thumb_color)
        self._track_active_color = QColor(track_active_color)
        self._thumb_active_color = QColor(thumb_active_color)

        # Dimensions
        self._track_radius = 11
        self._thumb_radius = 8

        # Animation
        self._animation_duration = animation_duration
        self._margin = max(0, self._thumb_radius - self._track_radius)
        self._offset = 0
        self._pulse_radius = 0

        # Setup animations
        self.animation = QPropertyAnimation(self, b"offset")
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.setDuration(self._animation_duration)

        self.pulse_animation = QPropertyAnimation(self, b"pulse_radius")
        self.pulse_animation.setDuration(self._animation_duration)
        self.pulse_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.animations_group = QSequentialAnimationGroup()
        self.animations_group.addAnimation(self.animation)
        self.animations_group.addAnimation(self.pulse_animation)

        # Setup initial state
        self.setFixedSize(
            self._track_radius * 4 + self._margin * 2,
            self._track_radius * 2 + self._margin * 2,
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        return QSize(
            4 * self._track_radius + 2 * self._margin,
            2 * self._track_radius + 2 * self._margin,
        )

    def hitButton(self, pos: QPoint):
        return self.contentsRect().contains(pos)

    @pyqtSlot(int)
    def setChecked(self, checked):
        super().setChecked(checked)
        self.offset = 1 if checked else 0

    def paintEvent(self, e: QPaintEvent):
        # Set up painter
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Get current state
        checked = self.isChecked()
        enabled = self.isEnabled()

        # Calculate sizes
        track_opacity = 0.6 if enabled else 0.3
        margin = self._margin
        thumb_radius = self._thumb_radius
        track_radius = self._track_radius

        # Get widget dimensions
        width = self.width() - 2 * margin
        height = self.height() - 2 * margin

        # Draw track
        track_brush = QBrush(self._track_active_color if checked else self._track_color)
        track_pen = QPen(Qt.PenStyle.NoPen)

        p.setBrush(track_brush)
        p.setPen(track_pen)
        p.setOpacity(track_opacity)

        p.drawRoundedRect(margin, margin, width, height, track_radius, track_radius)

        # Calculate thumb position
        total_offset = width - 2 * thumb_radius
        offset = total_offset * self.offset

        # Draw thumb
        p.setBrush(QBrush(self._thumb_active_color if checked else self._thumb_color))
        p.setPen(QPen(Qt.PenStyle.NoPen))
        p.setOpacity(1.0)

        p.drawEllipse(
            QPointF(margin + thumb_radius + offset, margin + height / 2),
            thumb_radius,
            thumb_radius,
        )

        # Draw pulse if animating
        if self._pulse_radius > 0:
            p.setBrush(QBrush(QColor(0, 0, 0, 0)))
            p.setPen(QPen(QColor(0, 0, 0, 0)))
            p.setOpacity(0.1)

            p.drawEllipse(
                QPointF(margin + thumb_radius + offset, margin + height / 2),
                self._pulse_radius,
                self._pulse_radius,
            )

        p.end()

    # Property animations
    @pyqtProperty(float)
    def offset(self):
        return self._offset

    @offset.setter
    def offset(self, value):
        self._offset = value
        self.update()

    @pyqtProperty(float)
    def pulse_radius(self):
        return self._pulse_radius

    @pulse_radius.setter
    def pulse_radius(self, value):
        self._pulse_radius = value
        self.update()

    # Override the change event to handle state changes
    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)

        if self.isEnabled():
            if self.isChecked():
                self.animation.setStartValue(0)
                self.animation.setEndValue(1)
            else:
                self.animation.setStartValue(1)
                self.animation.setEndValue(0)

            self.animations_group.start()
