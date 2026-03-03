"""
PyQt6 main window with all widgets, signal/slot wiring, and live plots
for the Toshiba AS3 VFD Control System.
"""

import csv
import os
import time
from collections import deque
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QColor, QAction
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QDoubleSpinBox, QSlider, QPushButton,
    QRadioButton, QButtonGroup, QCheckBox, QLineEdit, QSpinBox,
    QToolBar, QStatusBar, QSplitter, QFrame, QMessageBox, QScrollArea,
    QSizePolicy, QDialog,
)

from constants import (
    DEFAULT_IP, DEFAULT_PORT, POLL_INTERVAL_MS,
    MAX_FREQ_HZ, MIN_FREQ_HZ, MAX_RPM, MIN_RPM,
    MIN_RAMP_TIME, MAX_RAMP_TIME, DEFAULT_ACCEL_TIME, DEFAULT_DECEL_TIME,
    MAX_BASE_VOLTAGE, MAX_VF_BOOST_V,
    MIN_CARRIER_FREQ_KHZ, MAX_CARRIER_FREQ_KHZ, DEFAULT_CARRIER_FREQ_KHZ,
    MAX_DC_BRAKE_PCT, DEFAULT_DC_BRAKE_TIME,
    PLOT_WINDOW_SECONDS, PLOT_HISTORY_SIZE,
    MOTOR_RATED_VOLTAGE, MOTOR_RATED_CURRENT, MOTOR_RATED_TORQUE_NM,
    FD01_TRIPPED, FD01_ALARM, FD01_RUN_STOP,
    TACH_IP, TACH_PORT,
    LABJACK_IP,
    VIBRATION_WARNING_MMS,
    TORQUE_SMOOTHING_ALPHA,
)
from physics import (
    freq_to_rpm, rpm_to_freq, voltage_from_percent, current_from_percent,
    calc_power_hp, calc_torque_ftlb, calc_kinetic_energy_wh,
    calc_torque_from_acceleration,
)
from vfd_controller import VFDController, VFDStatus
from tach_reader import TachReader
from labjack_reader import LabJackReader
from analog_meter import AnalogMeter


class StartupDialog(QDialog):
    """Modal splash dialog that auto-connects VFD, Tach, and LabJack."""

    _COLORS = {
        "pending": "#888888",
        "connecting": "#daa520",
        "connected": "#4caf50",
        "failed": "#cc0000",
    }

    def __init__(self, controller, tach_reader, labjack_reader, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connecting Devices...")
        self.setFixedSize(400, 200)
        self.setModal(True)

        self._controller = controller
        self._tach_reader = tach_reader
        self._labjack_reader = labjack_reader

        # Track completion: {device: "pending"|"connecting"|"connected"|"failed"}
        self._states = {"vfd": "pending", "tach": "pending", "labjack": "pending"}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Connecting to devices...")
        title.setFont(QFont("", 12, QFont.Weight.Bold))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Device rows
        self._indicators = {}
        self._status_labels = {}
        devices = [
            ("vfd", f"VFD ({DEFAULT_IP}:{DEFAULT_PORT})"),
            ("tach", f"Tachometer ({TACH_IP}:{TACH_PORT})"),
            ("labjack", f"LabJack T7 ({LABJACK_IP})"),
        ]
        for key, name in devices:
            row = QHBoxLayout()
            indicator = QLabel("\u25cf")
            indicator.setFont(QFont("", 18))
            indicator.setFixedWidth(30)
            indicator.setStyleSheet(f"color: {self._COLORS['pending']};")
            row.addWidget(indicator)
            self._indicators[key] = indicator

            label = QLabel(name)
            label.setFont(QFont("", 10))
            row.addWidget(label, 1)

            status = QLabel("Pending")
            status.setFont(QFont("", 10))
            status.setFixedWidth(100)
            row.addWidget(status)
            self._status_labels[key] = status

            layout.addLayout(row)

        # Connect signals
        self._controller.status_updated.connect(self._on_vfd_status)
        self._tach_reader.connected_changed.connect(self._on_tach_result)
        self._labjack_reader.connected_changed.connect(self._on_labjack_result)

        # Start connections after dialog is shown
        QTimer.singleShot(100, self._start_connections)

    def _set_state(self, device, state):
        self._states[device] = state
        self._indicators[device].setStyleSheet(
            f"color: {self._COLORS[state]};"
        )
        label_text = {"pending": "Pending", "connecting": "Connecting...",
                      "connected": "Connected", "failed": "Failed"}
        self._status_labels[device].setText(label_text[state])

        # Check if all devices have reported
        if all(s in ("connected", "failed") for s in self._states.values()):
            QTimer.singleShot(1500, self.accept)

    def _start_connections(self):
        # VFD
        self._set_state("vfd", "connecting")
        self._controller.connect_vfd(DEFAULT_IP, DEFAULT_PORT)

        # Tachometer
        self._set_state("tach", "connecting")
        self._tach_reader.connect_tach(TACH_IP, TACH_PORT)

        # LabJack
        self._set_state("labjack", "connecting")
        self._labjack_reader.connect_labjack(LABJACK_IP)

    def _on_vfd_status(self, status):
        if self._states["vfd"] not in ("connected", "failed"):
            if status.connected:
                self._set_state("vfd", "connected")
            elif status.error_message:
                self._set_state("vfd", "failed")

    def _on_tach_result(self, connected):
        if self._states["tach"] not in ("connected", "failed"):
            self._set_state("tach", "connected" if connected else "failed")

    def _on_labjack_result(self, connected):
        if self._states["labjack"] not in ("connected", "failed"):
            self._set_state("labjack", "connected" if connected else "failed")

    def get_results(self):
        return dict(self._states)


class PlotWindow(QWidget):
    """Separate window for live plots (designed for second monitor)."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("VFD Plots — 75 HP Flywheel Drive")
        self.setMinimumSize(700, 500)
        self.resize(900, 700)

        self._time_data = deque(maxlen=PLOT_HISTORY_SIZE)
        self._power_data = deque(maxlen=PLOT_HISTORY_SIZE)
        self._torque_data = deque(maxlen=PLOT_HISTORY_SIZE)
        self._calc_torque_data = deque(maxlen=PLOT_HISTORY_SIZE)
        self._sync_rpm_data = deque(maxlen=PLOT_HISTORY_SIZE)
        self._tach_rpm_data = deque(maxlen=PLOT_HISTORY_SIZE)
        self._pressure_data = deque(maxlen=PLOT_HISTORY_SIZE)
        self._vibration_data = deque(maxlen=PLOT_HISTORY_SIZE)
        self._ke_data = deque(maxlen=PLOT_HISTORY_SIZE)
        self._last_pressure = 0.001  # default until first reading
        self._last_vibration = 0.0
        self._start_time = time.time()

        self._build_plots()

        self._timer = QTimer()
        self._timer.timeout.connect(self._refresh)
        self._timer.start(POLL_INTERVAL_MS)

    @staticmethod
    def _show_all_axes(plot_widget):
        """Show axes on all 4 sides with tick marks."""
        pi = plot_widget.getPlotItem()
        for side in ('top', 'right'):
            pi.showAxis(side)
            ax = pi.getAxis(side)
            ax.setStyle(showValues=True)
            ax.setGrid(False)

    @staticmethod
    def _make_readout_text(plot_widget, color="#333333"):
        """Create a TextItem anchored to the top-right corner of a plot."""
        text = pg.TextItem("", anchor=(1, 0), color=color)
        text.setFont(QFont("Monospace", 11, QFont.Weight.Bold))
        plot_widget.addItem(text)
        return text

    def _build_plots(self):
        pg.setConfigOptions(antialias=True, background='w', foreground='k')
        title_font = QFont("sans", 11, QFont.Weight.Bold)
        grid = QGridLayout(self)
        grid.setContentsMargins(6, 6, 6, 6)

        # --- Left column (row 0-2, col 0) ---

        # Power plot
        self._power_plot = pg.PlotWidget(title="Power (kW)")
        self._power_plot.getPlotItem().titleLabel.item.setFont(title_font)
        self._power_plot.setLabel("left", "kW")
        self._power_plot.setLabel("bottom", "Time", units="s")
        self._power_plot.setYRange(0, 60)
        self._power_plot.showGrid(x=True, y=True, alpha=0.15)
        self._power_curve = self._power_plot.plot(
            pen=pg.mkPen(color="#2e7d32", width=2)
        )
        self._power_readout = self._make_readout_text(self._power_plot, "#2e7d32")
        self._show_all_axes(self._power_plot)
        grid.addWidget(self._power_plot, 0, 0)

        # Torque plot
        self._torque_plot = pg.PlotWidget(title="Torque (N·m)")
        self._torque_plot.getPlotItem().titleLabel.item.setFont(title_font)
        self._torque_plot.setLabel("left", "N·m")
        self._torque_plot.setLabel("bottom", "Time", units="s")
        self._torque_plot.enableAutoRange(axis='y')
        self._torque_plot.showGrid(x=True, y=True, alpha=0.15)
        self._torque_curve = self._torque_plot.plot(
            pen=pg.mkPen(color="#e65100", width=2), name="VFD"
        )
        self._calc_torque_curve = self._torque_plot.plot(
            pen=pg.mkPen(color="#0277bd", width=2), name="Calc"
        )
        self._torque_plot.addLegend()
        self._torque_readout = self._make_readout_text(self._torque_plot, "#e65100")
        self._show_all_axes(self._torque_plot)
        grid.addWidget(self._torque_plot, 1, 0)

        # RPM plot
        self._rpm_plot = pg.PlotWidget(title="RPM")
        self._rpm_plot.getPlotItem().titleLabel.item.setFont(title_font)
        self._rpm_plot.setLabel("left", "RPM")
        self._rpm_plot.setLabel("bottom", "Time", units="s")
        self._rpm_plot.enableAutoRange(axis='y')
        self._rpm_plot.showGrid(x=True, y=True, alpha=0.15)
        self._sync_rpm_curve = self._rpm_plot.plot(
            pen=pg.mkPen(color="#7b1fa2", width=2), name="Sync"
        )
        self._tach_rpm_curve = self._rpm_plot.plot(
            pen=pg.mkPen(color="#c62828", width=2), name="Tach"
        )
        self._rpm_plot.addLegend()
        self._rpm_readout = self._make_readout_text(self._rpm_plot, "#333333")
        self._show_all_axes(self._rpm_plot)
        grid.addWidget(self._rpm_plot, 2, 0)

        # --- Right column (row 0-2, col 1) ---

        # Pressure plot (log scale — use explicit Y range instead of setLogMode
        # so the axis labels show real Torr values)
        self._pressure_plot = pg.PlotWidget(title="Pressure (Torr)")
        self._pressure_plot.getPlotItem().titleLabel.item.setFont(title_font)
        self._pressure_plot.setLabel("left", "Torr")
        self._pressure_plot.setLabel("bottom", "Time", units="s")
        self._pressure_plot.setLogMode(y=True)
        self._pressure_plot.setYRange(-4, 3)  # log10 range: 1e-4 to 1e3
        self._pressure_plot.showGrid(x=True, y=True, alpha=0.15)
        self._pressure_curve = self._pressure_plot.plot(
            pen=pg.mkPen(color="#00838f", width=2)
        )
        self._pressure_readout = self._make_readout_text(self._pressure_plot, "#00838f")
        self._show_all_axes(self._pressure_plot)
        grid.addWidget(self._pressure_plot, 0, 1)

        # Vibration plot
        self._vibration_plot = pg.PlotWidget(title="Vibration (mm/s RMS)")
        self._vibration_plot.getPlotItem().titleLabel.item.setFont(title_font)
        self._vibration_plot.setLabel("left", "mm/s")
        self._vibration_plot.setLabel("bottom", "Time", units="s")
        self._vibration_plot.setYRange(0, 25)
        self._vibration_plot.showGrid(x=True, y=True, alpha=0.15)
        self._vibration_curve = self._vibration_plot.plot(
            pen=pg.mkPen(color="#ad1457", width=2)
        )
        self._vibration_readout = self._make_readout_text(self._vibration_plot, "#ad1457")
        self._show_all_axes(self._vibration_plot)
        grid.addWidget(self._vibration_plot, 1, 1)

        # KE plot
        self._ke_plot = pg.PlotWidget(title="Kinetic Energy (Wh)")
        self._ke_plot.getPlotItem().titleLabel.item.setFont(title_font)
        self._ke_plot.setLabel("left", "Wh")
        self._ke_plot.setLabel("bottom", "Time", units="s")
        self._ke_plot.enableAutoRange(axis='y')
        self._ke_plot.showGrid(x=True, y=True, alpha=0.15)
        self._ke_curve = self._ke_plot.plot(
            pen=pg.mkPen(color="#5d4037", width=2)
        )
        self._ke_readout = self._make_readout_text(self._ke_plot, "#5d4037")
        self._show_all_axes(self._ke_plot)
        grid.addWidget(self._ke_plot, 2, 1)

        # Link all X axes to power plot
        self._torque_plot.setXLink(self._power_plot)
        self._rpm_plot.setXLink(self._power_plot)
        self._pressure_plot.setXLink(self._power_plot)
        self._vibration_plot.setXLink(self._power_plot)
        self._ke_plot.setXLink(self._power_plot)

    def append_data(self, power_kw, torque_nm, calc_torque_nm,
                    sync_rpm, tach_rpm,
                    pressure_torr=None, vibration_mms=None, ke_wh=0.0):
        now = time.time() - self._start_time
        self._time_data.append(now)
        self._power_data.append(power_kw)
        self._torque_data.append(torque_nm)
        self._calc_torque_data.append(calc_torque_nm)
        self._sync_rpm_data.append(sync_rpm)
        self._tach_rpm_data.append(tach_rpm)
        self._ke_data.append(ke_wh)
        self._pressure_data.append(
            pressure_torr if pressure_torr is not None else self._last_pressure
        )
        self._vibration_data.append(
            vibration_mms if vibration_mms is not None else self._last_vibration
        )
        if pressure_torr is not None:
            self._last_pressure = pressure_torr
        if vibration_mms is not None:
            self._last_vibration = vibration_mms

    def _update_readout(self, text_item, plot_widget, label):
        """Position a TextItem at the top-right of the visible plot area."""
        vr = plot_widget.getPlotItem().getViewBox().viewRange()
        x_max = vr[0][1]
        y_max = vr[1][1]
        text_item.setText(label)
        text_item.setPos(x_max, y_max)

    def _refresh(self):
        if len(self._time_data) < 2:
            return
        t = np.array(self._time_data)
        self._power_curve.setData(t, np.array(self._power_data))
        self._torque_curve.setData(t, np.array(self._torque_data))
        self._calc_torque_curve.setData(t, np.array(self._calc_torque_data))
        self._sync_rpm_curve.setData(t, np.array(self._sync_rpm_data))
        self._tach_rpm_curve.setData(t, np.array(self._tach_rpm_data))
        pressure = np.array(self._pressure_data)
        pressure = np.clip(pressure, 1e-6, None)  # avoid log(0)
        self._pressure_curve.setData(t, pressure)
        self._vibration_curve.setData(t, np.array(self._vibration_data))
        self._ke_curve.setData(t, np.array(self._ke_data))

        # Update digital readout overlays with latest values
        pw = self._power_data[-1]
        self._update_readout(self._power_readout, self._power_plot,
                             f"{pw:.2f} kW")

        tq = self._torque_data[-1]
        ctq = self._calc_torque_data[-1]
        self._update_readout(self._torque_readout, self._torque_plot,
                             f"VFD {tq:.1f}  Calc {ctq:.1f} N·m")

        sr = self._sync_rpm_data[-1]
        tr = self._tach_rpm_data[-1]
        self._update_readout(self._rpm_readout, self._rpm_plot,
                             f"Sync {sr:.0f}  Tach {tr:.0f} RPM")

        pr = self._pressure_data[-1]
        if pr >= 1.0:
            pr_str = f"{pr:.1f}"
        else:
            pr_str = f"{pr:.2e}"
        self._update_readout(self._pressure_readout, self._pressure_plot,
                             f"{pr_str} Torr")

        vb = self._vibration_data[-1]
        self._update_readout(self._vibration_readout, self._vibration_plot,
                             f"{vb:.2f} mm/s")

        ke = self._ke_data[-1]
        self._update_readout(self._ke_readout, self._ke_plot,
                             f"{ke:.1f} Wh")

    def stop(self):
        self._timer.stop()


class MainDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Toshiba AS3 VFD Control — 75 HP Flywheel Drive")
        self.setMinimumSize(750, 700)
        self.resize(800, 750)

        # --- Plot window (separate window for second monitor) ---
        self._plot_window = PlotWindow()
        self._plot_window.show()

        # --- VFD worker thread ---
        self._controller = VFDController()
        self._controller.status_updated.connect(self._on_status_updated)

        # --- Tachometer thread ---
        self._tach_reader = TachReader()
        self._tach_reader.rpm_updated.connect(self._on_tach_rpm)
        self._tach_reader.connected_changed.connect(self._on_tach_connected)
        self._tach_rpm = 0.0
        self._tach_connected = False
        self._prev_tach_rpm = 0.0
        self._prev_tach_time = 0.0
        self._calc_torque_nm = 0.0
        self._smooth_torque_nm = 0.0

        # --- LabJack T7 thread ---
        self._labjack_reader = LabJackReader()
        self._labjack_reader.sensors_updated.connect(self._on_sensors_updated)
        self._labjack_reader.connected_changed.connect(self._on_labjack_connected)
        self._labjack_connected = False
        self._pressure_torr = 0.0
        self._vibration_mms = 0.0

        # --- CSV logging state ---
        self._logging = False
        self._log_file = None
        self._csv_writer = None

        # --- Current direction state ---
        self._direction = "forward"
        self._is_running = False

        # --- Build UI ---
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        # Start worker threads
        self._controller.start()
        self._tach_reader.start()
        self._labjack_reader.start()

        # Auto-connect via startup dialog
        self._run_startup_dialog()

    # ------------------------------------------------------------------ #
    #  Toolbar                                                            #
    # ------------------------------------------------------------------ #

    def _build_toolbar(self):
        toolbar = QToolBar("Connection")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # IP field
        toolbar.addWidget(QLabel("  IP: "))
        self._ip_edit = QLineEdit(DEFAULT_IP)
        self._ip_edit.setFixedWidth(140)
        toolbar.addWidget(self._ip_edit)

        # Port field
        toolbar.addWidget(QLabel("  Port: "))
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(DEFAULT_PORT)
        self._port_spin.setFixedWidth(80)
        toolbar.addWidget(self._port_spin)

        toolbar.addSeparator()

        # Connect button
        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setFixedWidth(90)
        self._connect_btn.clicked.connect(self._on_connect)
        toolbar.addWidget(self._connect_btn)

        # Connection LED
        self._conn_led = QLabel("  ●  ")
        self._conn_led.setFont(QFont("", 16))
        self._set_led(False)
        toolbar.addWidget(self._conn_led)

        # Spacer
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # E-STOP button
        # Record button
        self._record_btn = QPushButton("Record")
        self._record_btn.setFixedSize(120, 36)
        self._record_btn.setStyleSheet(
            "QPushButton { background-color: #555555; color: white; "
            "font-weight: bold; font-size: 13px; border-radius: 4px; } "
            "QPushButton:hover { background-color: #666666; }"
        )
        self._record_btn.clicked.connect(self._on_record_toggle)
        toolbar.addWidget(self._record_btn)

        toolbar.addSeparator()

        self._estop_btn = QPushButton("E-STOP")
        self._estop_btn.setFixedSize(100, 36)
        self._estop_btn.setStyleSheet(
            "QPushButton { background-color: #cc0000; color: white; "
            "font-weight: bold; font-size: 14px; border-radius: 4px; } "
            "QPushButton:hover { background-color: #ff0000; }"
        )
        self._estop_btn.clicked.connect(self._on_estop)
        toolbar.addWidget(self._estop_btn)

    # ------------------------------------------------------------------ #
    #  Status bar                                                         #
    # ------------------------------------------------------------------ #

    def _build_statusbar(self):
        self.statusBar().showMessage("Ready — not connected")

    # ------------------------------------------------------------------ #
    #  Auto-connect startup                                               #
    # ------------------------------------------------------------------ #

    def _run_startup_dialog(self):
        dlg = StartupDialog(
            self._controller, self._tach_reader, self._labjack_reader, self
        )
        dlg.exec()
        results = dlg.get_results()

        # Update VFD connect button state
        if results["vfd"] == "connected":
            self._connect_btn.setText("Disconnect")
            self._set_led(True)
            # Send current GUI values to VFD
            self._controller.set_frequency(self._hz_spin.value())
            self._controller.set_accel_time(self._accel_spin.value())
            self._controller.set_decel_time(self._decel_spin.value())

        # Update tach button state
        if results["tach"] == "connected":
            self._tach_connect_btn.setText("Disconnect Tach")
            self._tach_connected = True
            self._tach_status_label.setText("Connected")
            self._tach_status_label.setStyleSheet("color: green;")

        # Update LabJack button state
        if results["labjack"] == "connected":
            self._lj_connect_btn.setText("Disconnect LabJack")
            self._labjack_connected = True
            self._lj_status_label.setText("Connected")
            self._lj_status_label.setStyleSheet("color: green;")

        # Summary in status bar
        connected = [k for k, v in results.items() if v == "connected"]
        failed = [k for k, v in results.items() if v == "failed"]
        if failed:
            self.statusBar().showMessage(
                f"Connected: {', '.join(connected) or 'none'} — "
                f"Failed: {', '.join(failed)}", 5000
            )
        elif connected:
            self.statusBar().showMessage(
                f"All devices connected", 3000
            )

    # ------------------------------------------------------------------ #
    #  Central widget                                                     #
    # ------------------------------------------------------------------ #

    def _build_central(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # --- Left panel (controls) with scroll ---
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 4, 0)

        self._build_speed_control(left_layout)
        self._build_vf_boost(left_layout)
        self._build_ramp_rates(left_layout)
        self._build_direction(left_layout)
        self._build_tach_group(left_layout)
        self._build_labjack_group(left_layout)
        self._build_status_group(left_layout)
        left_layout.addStretch()

        left_scroll.setWidget(left_widget)
        left_scroll.setMinimumWidth(320)
        left_scroll.setMaximumWidth(400)

        # --- Right panel (readouts + meters + motor control) ---
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 0, 0, 0)

        self._build_readouts(right_layout)
        self._build_meters(right_layout)
        self._build_motor_control(right_layout)
        self._build_dc_brake(right_layout)
        right_layout.addStretch()

        splitter.addWidget(left_scroll)
        splitter.addWidget(right_widget)
        splitter.setSizes([350, 450])

    # ------------------------------------------------------------------ #
    #  Speed Control                                                      #
    # ------------------------------------------------------------------ #

    def _build_speed_control(self, parent_layout):
        group = QGroupBox("Speed Control")
        layout = QGridLayout(group)

        # RPM spinbox
        layout.addWidget(QLabel("RPM:"), 0, 0)
        self._rpm_spin = QDoubleSpinBox()
        self._rpm_spin.setRange(MIN_RPM, MAX_RPM)
        self._rpm_spin.setDecimals(0)
        self._rpm_spin.setSingleStep(60)
        self._rpm_spin.setSuffix(" RPM")
        self._rpm_spin.valueChanged.connect(self._on_rpm_changed)
        layout.addWidget(self._rpm_spin, 0, 1)

        # Hz spinbox
        layout.addWidget(QLabel("Hz:"), 1, 0)
        self._hz_spin = QDoubleSpinBox()
        self._hz_spin.setRange(MIN_FREQ_HZ, MAX_FREQ_HZ)
        self._hz_spin.setDecimals(2)
        self._hz_spin.setSingleStep(1.0)
        self._hz_spin.setSuffix(" Hz")
        self._hz_spin.valueChanged.connect(self._on_hz_changed)
        layout.addWidget(self._hz_spin, 1, 1)

        # Speed slider
        self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        self._speed_slider.setRange(0, int(MAX_FREQ_HZ * 100))
        self._speed_slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(self._speed_slider, 2, 0, 1, 2)

        parent_layout.addWidget(group)

    def _on_rpm_changed(self, rpm):
        hz = rpm_to_freq(rpm)
        self._hz_spin.blockSignals(True)
        self._hz_spin.setValue(hz)
        self._hz_spin.blockSignals(False)
        self._speed_slider.blockSignals(True)
        self._speed_slider.setValue(int(hz * 100))
        self._speed_slider.blockSignals(False)
        self._controller.set_frequency(hz)

    def _on_hz_changed(self, hz):
        rpm = freq_to_rpm(hz)
        self._rpm_spin.blockSignals(True)
        self._rpm_spin.setValue(rpm)
        self._rpm_spin.blockSignals(False)
        self._speed_slider.blockSignals(True)
        self._speed_slider.setValue(int(hz * 100))
        self._speed_slider.blockSignals(False)
        self._controller.set_frequency(hz)

    def _on_slider_changed(self, value):
        hz = value / 100.0
        self._hz_spin.blockSignals(True)
        self._hz_spin.setValue(hz)
        self._hz_spin.blockSignals(False)
        self._rpm_spin.blockSignals(True)
        self._rpm_spin.setValue(freq_to_rpm(hz))
        self._rpm_spin.blockSignals(False)
        self._controller.set_frequency(hz)

    # ------------------------------------------------------------------ #
    #  V/f Voltage Control                                                #
    # ------------------------------------------------------------------ #

    def _build_vf_boost(self, parent_layout):
        group = QGroupBox("V/f Voltage")
        layout = QGridLayout(group)

        # Base voltage (F171) — voltage at base frequency (60 Hz)
        layout.addWidget(QLabel("Base V:"), 0, 0)
        self._base_voltage_spin = QDoubleSpinBox()
        self._base_voltage_spin.setRange(0, MAX_BASE_VOLTAGE)
        self._base_voltage_spin.setDecimals(1)
        self._base_voltage_spin.setSingleStep(10.0)
        self._base_voltage_spin.setSuffix(" V")
        self._base_voltage_spin.setValue(MOTOR_RATED_VOLTAGE)
        self._base_voltage_spin.valueChanged.connect(self._on_base_voltage_changed)
        layout.addWidget(self._base_voltage_spin, 0, 1)

        # Low-speed torque boost (F402 vb) — extra voltage at low Hz
        layout.addWidget(QLabel("Boost:"), 1, 0)
        self._boost_spin = QDoubleSpinBox()
        self._boost_spin.setRange(0, MAX_VF_BOOST_V)
        self._boost_spin.setDecimals(1)
        self._boost_spin.setSingleStep(1.0)
        self._boost_spin.setSuffix(" V")
        self._boost_spin.setValue(0.0)
        self._boost_spin.setToolTip("Extra voltage at low frequencies for torque")
        self._boost_spin.valueChanged.connect(self._on_boost_changed)
        layout.addWidget(self._boost_spin, 1, 1)

        # Carrier frequency (F300)
        layout.addWidget(QLabel("Carrier:"), 2, 0)
        self._carrier_spin = QDoubleSpinBox()
        self._carrier_spin.setRange(MIN_CARRIER_FREQ_KHZ, MAX_CARRIER_FREQ_KHZ)
        self._carrier_spin.setDecimals(1)
        self._carrier_spin.setSingleStep(0.5)
        self._carrier_spin.setSuffix(" kHz")
        self._carrier_spin.setValue(DEFAULT_CARRIER_FREQ_KHZ)
        self._carrier_spin.setToolTip("PWM carrier frequency (F300)")
        self._carrier_spin.valueChanged.connect(self._on_carrier_changed)
        layout.addWidget(self._carrier_spin, 2, 1)

        parent_layout.addWidget(group)

    def _on_base_voltage_changed(self, volts):
        self._controller.set_base_voltage(volts)

    def _on_boost_changed(self, volts):
        self._controller.set_vf_boost(volts)

    def _on_carrier_changed(self, khz):
        self._controller.set_carrier_freq(khz)

    # ------------------------------------------------------------------ #
    #  Ramp Rates                                                         #
    # ------------------------------------------------------------------ #

    def _build_ramp_rates(self, parent_layout):
        group = QGroupBox("Ramp Rates")
        layout = QGridLayout(group)

        layout.addWidget(QLabel("Accel:"), 0, 0)
        self._accel_spin = QDoubleSpinBox()
        self._accel_spin.setRange(MIN_RAMP_TIME, MAX_RAMP_TIME)
        self._accel_spin.setDecimals(1)
        self._accel_spin.setSingleStep(1.0)
        self._accel_spin.setSuffix(" s")
        self._accel_spin.setValue(DEFAULT_ACCEL_TIME)
        self._accel_spin.valueChanged.connect(
            lambda v: self._controller.set_accel_time(v)
        )
        layout.addWidget(self._accel_spin, 0, 1)

        layout.addWidget(QLabel("Decel:"), 1, 0)
        self._decel_spin = QDoubleSpinBox()
        self._decel_spin.setRange(MIN_RAMP_TIME, MAX_RAMP_TIME)
        self._decel_spin.setDecimals(1)
        self._decel_spin.setSingleStep(1.0)
        self._decel_spin.setSuffix(" s")
        self._decel_spin.setValue(DEFAULT_DECEL_TIME)
        self._decel_spin.valueChanged.connect(
            lambda v: self._controller.set_decel_time(v)
        )
        layout.addWidget(self._decel_spin, 1, 1)

        parent_layout.addWidget(group)

    # ------------------------------------------------------------------ #
    #  Direction                                                          #
    # ------------------------------------------------------------------ #

    def _build_direction(self, parent_layout):
        group = QGroupBox("Direction")
        layout = QVBoxLayout(group)

        self._fwd_radio = QRadioButton("Forward")
        self._rev_radio = QRadioButton("Reverse")
        self._fwd_radio.setChecked(True)

        self._dir_group = QButtonGroup()
        self._dir_group.addButton(self._fwd_radio)
        self._dir_group.addButton(self._rev_radio)

        self._fwd_radio.toggled.connect(self._on_direction_changed)

        layout.addWidget(self._fwd_radio)
        layout.addWidget(self._rev_radio)
        parent_layout.addWidget(group)

    def _on_direction_changed(self):
        self._direction = "forward" if self._fwd_radio.isChecked() else "reverse"
        # If motor is running, send new direction command immediately
        if self._is_running:
            if self._direction == "forward":
                self._controller.run_forward()
            else:
                self._controller.run_reverse()

    # ------------------------------------------------------------------ #
    #  Motor Control (RUN / STOP)                                        #
    # ------------------------------------------------------------------ #

    def _build_motor_control(self, parent_layout):
        group = QGroupBox("Motor Control")
        layout = QVBoxLayout(group)

        btn_layout = QHBoxLayout()

        self._run_btn = QPushButton("RUN")
        self._run_btn.setFixedHeight(40)
        self._run_btn.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; "
            "font-weight: bold; font-size: 14px; border-radius: 4px; } "
            "QPushButton:hover { background-color: #388e3c; }"
        )
        self._run_btn.clicked.connect(self._on_run)
        btn_layout.addWidget(self._run_btn)

        self._stop_btn = QPushButton("STOP")
        self._stop_btn.setFixedHeight(40)
        self._stop_btn.setStyleSheet(
            "QPushButton { background-color: #c8a000; color: white; "
            "font-weight: bold; font-size: 14px; border-radius: 4px; } "
            "QPushButton:hover { background-color: #dab000; }"
        )
        self._stop_btn.clicked.connect(self._on_stop)
        btn_layout.addWidget(self._stop_btn)

        layout.addLayout(btn_layout)

        self._coast_stop_check = QCheckBox("Coast to stop (free spin)")
        layout.addWidget(self._coast_stop_check)

        parent_layout.addWidget(group)

    def _on_run(self):
        if self._hz_spin.value() <= 0:
            self.statusBar().showMessage(
                "Set a non-zero frequency before pressing RUN", 5000
            )
            return
        self._is_running = True
        if self._direction == "forward":
            self._controller.run_forward()
        else:
            self._controller.run_reverse()

    def _on_stop(self):
        self._is_running = False
        if self._coast_stop_check.isChecked():
            self._controller.coast_stop()
        else:
            self._controller.stop_motor()

    def _on_estop(self):
        self._is_running = False
        self._controller.emergency_stop()

    # ------------------------------------------------------------------ #
    #  CSV Data Logging                                                   #
    # ------------------------------------------------------------------ #

    def _on_record_toggle(self):
        if self._logging:
            self._stop_logging()
        else:
            self._start_logging()

    def _start_logging(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"vfd_log_{timestamp}.csv"
        self._log_file = open(filename, "w", newline="")
        self._csv_writer = csv.writer(self._log_file)
        self._csv_writer.writerow([
            "timestamp", "freq_hz", "voltage_v", "current_a", "power_kw",
            "torque_nm", "calc_torque_nm", "sync_rpm", "tach_rpm",
            "pressure_torr", "vibration_mms", "ke_wh",
        ])
        self._logging = True
        self._record_btn.setText("Stop Recording")
        self._record_btn.setStyleSheet(
            "QPushButton { background-color: #cc0000; color: white; "
            "font-weight: bold; font-size: 13px; border-radius: 4px; } "
            "QPushButton:hover { background-color: #dd2222; }"
        )
        self.statusBar().showMessage(f"Recording to {filename}", 3000)

    def _stop_logging(self):
        self._logging = False
        filename = ""
        if self._log_file:
            filename = self._log_file.name
            self._log_file.close()
            self._log_file = None
            self._csv_writer = None
        self._record_btn.setText("Record")
        self._record_btn.setStyleSheet(
            "QPushButton { background-color: #555555; color: white; "
            "font-weight: bold; font-size: 13px; border-radius: 4px; } "
            "QPushButton:hover { background-color: #666666; }"
        )
        if filename:
            self.statusBar().showMessage(f"Saved {filename}", 5000)

    def _write_csv_row(self, freq_hz, voltage_v, current_a, power_kw,
                       torque_nm, calc_torque_nm, sync_rpm, tach_rpm,
                       pressure_torr, vibration_mms, ke_wh):
        if not self._logging or self._csv_writer is None:
            return
        self._csv_writer.writerow([
            f"{time.time():.3f}", f"{freq_hz:.2f}", f"{voltage_v:.1f}",
            f"{current_a:.1f}", f"{power_kw:.3f}", f"{torque_nm:.1f}",
            f"{calc_torque_nm:.1f}", f"{sync_rpm:.0f}", f"{tach_rpm:.0f}",
            f"{pressure_torr:.4e}", f"{vibration_mms:.2f}", f"{ke_wh:.2f}",
        ])

    # ------------------------------------------------------------------ #
    #  DC Braking                                                         #
    # ------------------------------------------------------------------ #

    def _build_dc_brake(self, parent_layout):
        group = QGroupBox("DC Braking")
        layout = QGridLayout(group)

        layout.addWidget(QLabel("Power:"), 0, 0)
        self._dc_brake_slider = QSlider(Qt.Orientation.Horizontal)
        self._dc_brake_slider.setRange(0, int(MAX_DC_BRAKE_PCT))
        self._dc_brake_slider.setValue(50)
        self._dc_brake_slider.valueChanged.connect(self._on_dc_brake_power)
        layout.addWidget(self._dc_brake_slider, 0, 1)

        self._dc_brake_label = QLabel("50%")
        self._dc_brake_label.setFixedWidth(40)
        layout.addWidget(self._dc_brake_label, 0, 2)

        btn_layout = QHBoxLayout()

        self._dc_brake_start_btn = QPushButton("Start DC Brake")
        self._dc_brake_start_btn.setStyleSheet(
            "QPushButton { background-color: #1565c0; color: white; "
            "font-weight: bold; border-radius: 4px; } "
            "QPushButton:hover { background-color: #1976d2; }"
        )
        self._dc_brake_start_btn.clicked.connect(self._on_dc_brake_start)
        btn_layout.addWidget(self._dc_brake_start_btn)

        self._dc_brake_stop_btn = QPushButton("Release Brake")
        self._dc_brake_stop_btn.clicked.connect(self._on_dc_brake_stop)
        btn_layout.addWidget(self._dc_brake_stop_btn)

        layout.addLayout(btn_layout, 1, 0, 1, 3)

        parent_layout.addWidget(group)

    def _on_dc_brake_power(self, value):
        self._dc_brake_label.setText(f"{value}%")

    def _on_dc_brake_start(self):
        self._is_running = False
        self._controller.set_dc_brake(
            enable=True,
            voltage_pct=float(self._dc_brake_slider.value()),
        )

    def _on_dc_brake_stop(self):
        self._controller.set_dc_brake(enable=False)

    # ------------------------------------------------------------------ #
    #  Tachometer                                                         #
    # ------------------------------------------------------------------ #

    def _build_tach_group(self, parent_layout):
        group = QGroupBox("Tachometer (ACT-3X)")
        layout = QGridLayout(group)

        layout.addWidget(QLabel("IP:"), 0, 0)
        self._tach_ip_edit = QLineEdit(TACH_IP)
        self._tach_ip_edit.setFixedWidth(140)
        layout.addWidget(self._tach_ip_edit, 0, 1)

        layout.addWidget(QLabel("Port:"), 1, 0)
        self._tach_port_spin = QSpinBox()
        self._tach_port_spin.setRange(1, 65535)
        self._tach_port_spin.setValue(TACH_PORT)
        layout.addWidget(self._tach_port_spin, 1, 1)

        self._tach_connect_btn = QPushButton("Connect Tach")
        self._tach_connect_btn.clicked.connect(self._on_tach_connect)
        layout.addWidget(self._tach_connect_btn, 2, 0, 1, 2)

        self._tach_status_label = QLabel("Disconnected")
        layout.addWidget(self._tach_status_label, 3, 0, 1, 2)

        parent_layout.addWidget(group)

    def _on_tach_connect(self):
        if self._tach_connect_btn.text() == "Connect Tach":
            ip = self._tach_ip_edit.text().strip()
            port = self._tach_port_spin.value()
            self._tach_reader.connect_tach(ip, port)
            self._tach_connect_btn.setText("Disconnect Tach")
        else:
            self._tach_reader.disconnect_tach()
            self._tach_connect_btn.setText("Connect Tach")
            self._tach_connected = False
            self._tach_status_label.setText("Disconnected")

    def _on_tach_rpm(self, rpm: float):
        now = time.time()
        dt = now - self._prev_tach_time if self._prev_tach_time > 0 else 0.0
        if dt > 0.01:  # avoid noise from very small dt
            raw = calc_torque_from_acceleration(
                rpm, self._prev_tach_rpm, dt,
            )
            # EMA smoothing to reduce spikiness
            a = TORQUE_SMOOTHING_ALPHA
            self._smooth_torque_nm = a * raw + (1.0 - a) * self._smooth_torque_nm
            self._calc_torque_nm = self._smooth_torque_nm
        self._prev_tach_rpm = rpm
        self._prev_tach_time = now
        self._tach_rpm = rpm

        # Update tach readouts immediately (independent of VFD)
        self._readout_labels["tach_rpm"].setText(f"{rpm:.0f}")
        self._rpm_meter.set_value(rpm)
        calc_torque = self._calc_torque_nm
        calc_torque_fl = calc_torque_ftlb(abs(calc_torque))
        self._readout_labels["calc_torque_nm"].setText(f"{calc_torque:.1f}")
        self._readout_labels["calc_torque_ftlb"].setText(f"{calc_torque_fl:.1f}")
        ke_wh = calc_kinetic_energy_wh(rpm)
        self._readout_labels["ke"].setText(f"{ke_wh:.1f}")

        # Push tach data to plots even when VFD is disconnected
        if not self._controller.is_connected():
            self._plot_window.append_data(0.0, 0.0, calc_torque, 0.0, rpm,
                                          ke_wh=ke_wh)

    def _on_tach_connected(self, connected: bool):
        self._tach_connected = connected
        if connected:
            self._tach_status_label.setText("Connected")
            self._tach_status_label.setStyleSheet("color: green;")
        else:
            self._tach_status_label.setText("Disconnected")
            self._tach_status_label.setStyleSheet("")
            self._tach_rpm = 0.0
            self._prev_tach_rpm = 0.0
            self._prev_tach_time = 0.0
            self._calc_torque_nm = 0.0
            self._smooth_torque_nm = 0.0
            self._readout_labels["tach_rpm"].setText("---")
            self._readout_labels["calc_torque_nm"].setText("---")
            self._readout_labels["calc_torque_ftlb"].setText("---")

    # ------------------------------------------------------------------ #
    #  LabJack T7 (Pressure + Vibration)                                 #
    # ------------------------------------------------------------------ #

    def _build_labjack_group(self, parent_layout):
        group = QGroupBox("LabJack T7 (Sensors)")
        layout = QGridLayout(group)

        layout.addWidget(QLabel("IP:"), 0, 0)
        self._lj_ip_edit = QLineEdit(LABJACK_IP)
        self._lj_ip_edit.setFixedWidth(140)
        layout.addWidget(self._lj_ip_edit, 0, 1)

        self._lj_connect_btn = QPushButton("Connect LabJack")
        self._lj_connect_btn.clicked.connect(self._on_labjack_connect)
        layout.addWidget(self._lj_connect_btn, 1, 0, 1, 2)

        self._lj_status_label = QLabel("Disconnected")
        layout.addWidget(self._lj_status_label, 2, 0, 1, 2)

        parent_layout.addWidget(group)

    def _on_labjack_connect(self):
        if self._lj_connect_btn.text() == "Connect LabJack":
            ip = self._lj_ip_edit.text().strip()
            self._labjack_reader.connect_labjack(ip)
            self._lj_connect_btn.setText("Disconnect LabJack")
        else:
            self._labjack_reader.disconnect_labjack()
            self._lj_connect_btn.setText("Connect LabJack")
            self._labjack_connected = False
            self._lj_status_label.setText("Disconnected")
            self._lj_status_label.setStyleSheet("")

    def _on_sensors_updated(self, pressure_torr: float, vibration_mms: float):
        self._pressure_torr = pressure_torr
        self._vibration_mms = vibration_mms

        # Vibration warning
        if vibration_mms > VIBRATION_WARNING_MMS:
            self._vib_warning.show()
        else:
            self._vib_warning.hide()

        # Update readout labels
        if pressure_torr >= 1.0:
            self._readout_labels["pressure"].setText(f"{pressure_torr:.1f}")
        else:
            self._readout_labels["pressure"].setText(f"{pressure_torr:.2e}")
        self._readout_labels["vibration"].setText(f"{vibration_mms:.2f}")
        self._vib_meter.set_value(vibration_mms)

        # Push sensor data to plots when VFD is disconnected
        if not self._controller.is_connected():
            ke_wh = calc_kinetic_energy_wh(self._tach_rpm)
            self._plot_window.append_data(
                0.0, 0.0, self._calc_torque_nm, 0.0, self._tach_rpm,
                pressure_torr=pressure_torr, vibration_mms=vibration_mms,
                ke_wh=ke_wh,
            )

    def _on_labjack_connected(self, connected: bool):
        self._labjack_connected = connected
        if connected:
            self._lj_status_label.setText("Connected")
            self._lj_status_label.setStyleSheet("color: green;")
        else:
            self._lj_status_label.setText("Disconnected")
            self._lj_status_label.setStyleSheet("")
            self._pressure_torr = 0.0
            self._vibration_mms = 0.0
            self._readout_labels["pressure"].setText("---")
            self._readout_labels["vibration"].setText("---")

    # ------------------------------------------------------------------ #
    #  Status Group                                                       #
    # ------------------------------------------------------------------ #

    def _build_status_group(self, parent_layout):
        group = QGroupBox("Status")
        layout = QGridLayout(group)

        layout.addWidget(QLabel("Connection:"), 0, 0)
        self._conn_status_label = QLabel("Disconnected")
        layout.addWidget(self._conn_status_label, 0, 1)

        layout.addWidget(QLabel("Fault:"), 1, 0)
        self._fault_label = QLabel("None")
        layout.addWidget(self._fault_label, 1, 1)

        self._reset_fault_btn = QPushButton("Reset Fault")
        self._reset_fault_btn.clicked.connect(
            lambda: self._controller.reset_fault()
        )
        layout.addWidget(self._reset_fault_btn, 2, 0, 1, 2)

        parent_layout.addWidget(group)

    # ------------------------------------------------------------------ #
    #  Live Readouts (right panel)                                        #
    # ------------------------------------------------------------------ #

    def _build_readouts(self, parent_layout):
        # Vibration warning banner (hidden by default)
        self._vib_warning = QLabel("VIBRATION HIGH")
        self._vib_warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._vib_warning.setStyleSheet(
            "background-color: #cc0000; color: white; font-weight: bold; "
            "font-size: 16px; padding: 8px; border-radius: 4px;"
        )
        self._vib_warning.hide()
        parent_layout.addWidget(self._vib_warning)

        group = QGroupBox("Live Readouts")
        grid = QGridLayout(group)

        readout_font = QFont("Monospace", 16, QFont.Weight.Bold)
        label_font = QFont("", 10)

        self._readout_labels = {}
        items = [
            # Row 0: electrical
            ("Voltage (V)", "voltage"),
            ("Current (A)", "current"),
            ("Power (kW)", "power_kw"),
            ("Power (HP)", "power_hp"),
            # Row 1: torque and RPM
            ("VFD Torque (N·m)", "torque_nm"),
            ("Calc Torque (N·m)", "calc_torque_nm"),
            ("Sync RPM", "sync_rpm"),
            ("Tach RPM", "tach_rpm"),
            # Row 2: more
            ("VFD Torque (ft·lb)", "torque_ftlb"),
            ("Calc Torque (ft·lb)", "calc_torque_ftlb"),
            ("KE (Wh)", "ke"),
            ("Pressure (Torr)", "pressure"),
            # Row 3: sensors
            ("Vibration (mm/s)", "vibration"),
        ]

        for i, (title, key) in enumerate(items):
            row, col = divmod(i, 4)
            col *= 2  # 2 columns per item (label + value)

            lbl = QLabel(title)
            lbl.setFont(label_font)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(lbl, row * 2, col, 1, 2)

            val = QLabel("---")
            val.setFont(readout_font)
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            val.setMinimumWidth(100)
            grid.addWidget(val, row * 2 + 1, col, 1, 2)
            self._readout_labels[key] = val

        parent_layout.addWidget(group)

    # ------------------------------------------------------------------ #
    #  Analog Meters                                                      #
    # ------------------------------------------------------------------ #

    def _build_meters(self, parent_layout):
        meter_layout = QHBoxLayout()

        self._rpm_meter = AnalogMeter(
            min_val=0, max_val=MAX_RPM, label="RPM",
            num_major=6, warning_pct=0.75, danger_pct=0.92,
        )
        meter_layout.addWidget(self._rpm_meter)

        self._vib_meter = AnalogMeter(
            min_val=0, max_val=25, label="mm/s RMS",
            num_major=5, warning_pct=0.32, danger_pct=0.60,
        )
        meter_layout.addWidget(self._vib_meter)

        parent_layout.addLayout(meter_layout)

    # ------------------------------------------------------------------ #
    #  Connection                                                         #
    # ------------------------------------------------------------------ #

    def _on_connect(self):
        if self._connect_btn.text() == "Connect":
            ip = self._ip_edit.text().strip()
            port = self._port_spin.value()
            self._controller.connect_vfd(ip, port)
            # Send current GUI values to VFD on connect
            self._controller.set_frequency(self._hz_spin.value())
            self._controller.set_accel_time(self._accel_spin.value())
            self._controller.set_decel_time(self._decel_spin.value())
            self._connect_btn.setText("Disconnect")
        else:
            self._is_running = False
            self._controller.disconnect_vfd()
            self._connect_btn.setText("Connect")
            self._set_led(False)

    def _set_led(self, connected: bool):
        color = "#4caf50" if connected else "#888888"
        self._conn_led.setStyleSheet(f"color: {color};")

    # ------------------------------------------------------------------ #
    #  Status update slot (from worker thread)                            #
    # ------------------------------------------------------------------ #

    def _on_status_updated(self, status: VFDStatus):
        # Connection LED
        self._set_led(status.connected)
        self._conn_status_label.setText(
            "Connected" if status.connected else "Disconnected"
        )

        if not status.connected:
            if status.error_message:
                self.statusBar().showMessage(status.error_message, 5000)
            return

        # Convert readings — VFD reports power/torque directly
        voltage = voltage_from_percent(status.output_voltage_pct)
        current = current_from_percent(status.output_current_pct)
        sync_rpm = freq_to_rpm(status.output_freq_hz)
        power_kw = status.output_power_kw
        power_hp = calc_power_hp(power_kw)
        torque_nm = status.torque_pct / 100.0 * MOTOR_RATED_TORQUE_NM
        torque_ftlb = calc_torque_ftlb(torque_nm)

        # Use tach RPM for KE if available, else sync RPM
        tach_rpm = self._tach_rpm
        ke_rpm = tach_rpm if self._tach_connected else sync_rpm
        ke_wh = calc_kinetic_energy_wh(ke_rpm)

        # Calculated torque from tach RPM derivative
        calc_torque = self._calc_torque_nm
        calc_torque_fl = calc_torque_ftlb(abs(calc_torque))

        # Update readout labels
        self._readout_labels["voltage"].setText(f"{voltage:.1f}")
        self._readout_labels["current"].setText(f"{current:.1f}")
        self._readout_labels["power_kw"].setText(f"{power_kw:.2f}")
        self._readout_labels["power_hp"].setText(f"{power_hp:.2f}")
        self._readout_labels["torque_nm"].setText(f"{torque_nm:.1f}")
        self._readout_labels["torque_ftlb"].setText(f"{torque_ftlb:.1f}")
        self._readout_labels["calc_torque_nm"].setText(f"{calc_torque:.1f}")
        self._readout_labels["calc_torque_ftlb"].setText(f"{calc_torque_fl:.1f}")
        self._readout_labels["sync_rpm"].setText(f"{sync_rpm:.0f}")
        self._readout_labels["tach_rpm"].setText(
            f"{tach_rpm:.0f}" if self._tach_connected else "---"
        )
        self._readout_labels["ke"].setText(f"{ke_wh:.1f}")

        # Update RPM meter (use tach if available, else sync)
        if not self._tach_connected:
            self._rpm_meter.set_value(sync_rpm)

        # Fault/alarm display from FD01 status bits
        inv_status = status.inverter_status
        if inv_status & FD01_TRIPPED:
            self._fault_label.setText(f"TRIPPED (alarm: 0x{status.alarm_code:04X})")
            self._fault_label.setStyleSheet("color: red; font-weight: bold;")
        elif inv_status & FD01_ALARM:
            self._fault_label.setText(f"ALARM (0x{status.alarm_code:04X})")
            self._fault_label.setStyleSheet("color: orange; font-weight: bold;")
        else:
            self._fault_label.setText("None")
            self._fault_label.setStyleSheet("")

        # Push data to plot window
        self._plot_window.append_data(
            power_kw, torque_nm, calc_torque,
            sync_rpm, tach_rpm if self._tach_connected else 0.0,
            pressure_torr=self._pressure_torr if self._labjack_connected else None,
            vibration_mms=self._vibration_mms if self._labjack_connected else None,
            ke_wh=ke_wh,
        )

        # Write CSV row if logging
        self._write_csv_row(
            status.output_freq_hz, voltage, current, power_kw,
            torque_nm, calc_torque, sync_rpm, tach_rpm,
            self._pressure_torr, self._vibration_mms, ke_wh,
        )

        # Update status bar with running state
        if status.error_message:
            self.statusBar().showMessage(status.error_message, 3000)
        elif inv_status & FD01_RUN_STOP:
            tach_str = f" | Tach: {tach_rpm:.0f}" if self._tach_connected else ""
            self.statusBar().showMessage(
                f"Running — {status.output_freq_hz:.2f} Hz / "
                f"{sync_rpm:.0f} RPM{tach_str}"
            )

    # ------------------------------------------------------------------ #
    #  Close event - safety shutdown                                      #
    # ------------------------------------------------------------------ #

    def closeEvent(self, event):
        # Stop CSV logging if active
        if self._logging:
            self._stop_logging()
        # Stop plot window
        self._plot_window.stop()
        self._plot_window.close()
        # Stop tach reader
        self._tach_reader.stop_thread()
        self._tach_reader.wait(2000)
        # Stop LabJack reader
        self._labjack_reader.stop_thread()
        self._labjack_reader.wait(2000)
        # Send stop command before closing
        self._controller.stop_motor()
        self._controller.disconnect_vfd()
        self._controller.stop_thread()
        self._controller.wait(3000)
        event.accept()
