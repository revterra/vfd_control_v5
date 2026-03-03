"""
Analog gauge widget drawn with QPainter.
Renders a 270-degree arc with tick marks, labels, a needle, and a digital readout.
"""

import math
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QPen, QColor, QFont, QConicalGradient, QBrush
from PyQt6.QtWidgets import QWidget


class AnalogMeter(QWidget):
    """Industrial-style analog gauge widget.

    Parameters
    ----------
    min_val : float
        Minimum scale value.
    max_val : float
        Maximum scale value.
    label : str
        Unit label displayed below the digital readout.
    num_major : int
        Number of major tick divisions.
    warning_pct : float or None
        Fraction (0-1) of scale where the arc turns from green to yellow.
    danger_pct : float or None
        Fraction (0-1) of scale where the arc turns from yellow to red.
    """

    def __init__(self, min_val=0.0, max_val=100.0, label="", num_major=10,
                 warning_pct=None, danger_pct=None, parent=None):
        super().__init__(parent)
        self._min = min_val
        self._max = max_val
        self._value = min_val
        self._label = label
        self._num_major = num_major
        self._warning_pct = warning_pct
        self._danger_pct = danger_pct
        self.setMinimumSize(180, 180)

    def set_value(self, value: float):
        clamped = max(self._min, min(self._max, value))
        if clamped != self._value:
            self._value = clamped
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        side = min(self.width(), self.height())
        painter.translate(self.width() / 2, self.height() / 2)
        scale = side / 220.0
        painter.scale(scale, scale)

        # Constants for the 270-degree arc
        # Arc spans from 225 deg (lower-left) counter-clockwise to -45 deg (lower-right)
        # In Qt, angles are 1/16th degree, positive = counter-clockwise
        START_ANGLE = -225  # degrees (math convention: 225 from 3-o'clock)
        SPAN = 270          # degrees of arc
        ARC_RADIUS = 85

        # --- Draw arc segments (green / yellow / red) ---
        arc_rect = QRectF(-ARC_RADIUS, -ARC_RADIUS, ARC_RADIUS * 2, ARC_RADIUS * 2)
        arc_pen = QPen()
        arc_pen.setWidth(8)
        arc_pen.setCapStyle(Qt.PenCapStyle.FlatCap)

        if self._warning_pct is not None and self._danger_pct is not None:
            segments = [
                (0.0, self._warning_pct, QColor("#4caf50")),
                (self._warning_pct, self._danger_pct, QColor("#f9a825")),
                (self._danger_pct, 1.0, QColor("#ef5350")),
            ]
        elif self._warning_pct is not None:
            segments = [
                (0.0, self._warning_pct, QColor("#4caf50")),
                (self._warning_pct, 1.0, QColor("#f9a825")),
            ]
        else:
            segments = [
                (0.0, 1.0, QColor("#4caf50")),
            ]

        for frac_start, frac_end, color in segments:
            arc_pen.setColor(color)
            painter.setPen(arc_pen)
            seg_start = START_ANGLE - frac_start * SPAN
            seg_span = -(frac_end - frac_start) * SPAN
            painter.drawArc(arc_rect, int(seg_start * 16), int(seg_span * 16))

        # --- Tick marks and labels ---
        painter.setPen(QPen(QColor("#333333"), 1.5))
        label_font = QFont("", 8)
        painter.setFont(label_font)

        for i in range(self._num_major + 1):
            frac = i / self._num_major
            angle_deg = 225 - frac * SPAN  # math convention
            angle_rad = math.radians(angle_deg)

            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)

            # Major tick
            inner = 72
            outer = 85
            painter.setPen(QPen(QColor("#333333"), 1.5))
            painter.drawLine(
                QPointF(cos_a * inner, -sin_a * inner),
                QPointF(cos_a * outer, -sin_a * outer),
            )

            # Label
            val = self._min + frac * (self._max - self._min)
            text = f"{val:.0f}"
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(text)
            th = fm.height()
            lx = cos_a * 60 - tw / 2
            ly = -sin_a * 60 + th / 4
            painter.drawText(QPointF(lx, ly), text)

            # Minor ticks (4 between each major)
            if i < self._num_major:
                for j in range(1, 5):
                    mfrac = (i + j / 5) / self._num_major
                    ma_deg = 225 - mfrac * SPAN
                    ma_rad = math.radians(ma_deg)
                    mc = math.cos(ma_rad)
                    ms = math.sin(ma_rad)
                    painter.setPen(QPen(QColor("#999999"), 0.8))
                    painter.drawLine(
                        QPointF(mc * 78, -ms * 78),
                        QPointF(mc * 85, -ms * 85),
                    )

        # --- Needle ---
        frac = (self._value - self._min) / (self._max - self._min)
        needle_deg = 225 - frac * SPAN
        needle_rad = math.radians(needle_deg)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#cc0000"))
        painter.save()
        painter.rotate(-needle_deg + 90)
        # Draw needle as a thin triangle
        from PyQt6.QtGui import QPolygonF
        needle = QPolygonF([
            QPointF(-3, 0),
            QPointF(0, -78),
            QPointF(3, 0),
            QPointF(0, 12),
        ])
        painter.drawPolygon(needle)
        painter.restore()

        # Center circle
        painter.setBrush(QColor("#555555"))
        painter.drawEllipse(QPointF(0, 0), 6, 6)

        # --- Digital readout ---
        painter.setPen(QColor("#222222"))
        val_font = QFont("Monospace", 14, QFont.Weight.Bold)
        painter.setFont(val_font)
        val_text = f"{self._value:.0f}"
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(val_text)
        painter.drawText(QPointF(-tw / 2, 40), val_text)

        # Unit label
        painter.setPen(QColor("#666666"))
        unit_font = QFont("", 9)
        painter.setFont(unit_font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(self._label)
        painter.drawText(QPointF(-tw / 2, 55), self._label)

        painter.end()
