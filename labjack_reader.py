"""
QThread worker for reading analog inputs from a LabJack T7 over Ethernet.

Reads:
  AIN0 — Pressure sensor (1-8V, log-linear Torr, P = 10^(V-5))
  AIN1 — Vibration sensor (1-5V, linear 0-25 mm/s RMS)

Uses the labjack-ljm library for Ethernet communication.
"""

import time
import logging

from PyQt6.QtCore import QThread, pyqtSignal

from labjack import ljm

from constants import (
    LABJACK_IP, LABJACK_SAMPLE_RATE_HZ,
    LABJACK_PRESSURE_CHANNEL, LABJACK_VIBRATION_CHANNEL,
)
from physics import voltage_to_pressure_torr, voltage_to_vibration_mms

log = logging.getLogger(__name__)


class LabJackReader(QThread):
    """
    Worker thread that polls AIN0 (pressure) and AIN1 (vibration) from
    a LabJack T7 at LABJACK_SAMPLE_RATE_HZ.

    Signals:
        sensors_updated(float, float): Emitted with (pressure_torr, vibration_mms).
        connected_changed(bool): Emitted on connect/disconnect.
    """

    sensors_updated = pyqtSignal(float, float)
    connected_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ip: str = LABJACK_IP
        self._running = False
        self._should_connect = False
        self._handle: int | None = None
        self._poll_interval = 1.0 / LABJACK_SAMPLE_RATE_HZ

    def connect_labjack(self, ip: str = LABJACK_IP):
        """Request connection to LabJack T7."""
        self._ip = ip
        self._should_connect = True

    def disconnect_labjack(self):
        """Request disconnection."""
        self._should_connect = False
        self._close_handle()

    def stop_thread(self):
        self._running = False
        self._should_connect = False

    def run(self):
        self._running = True

        while self._running:
            if self._should_connect and self._ip:
                try:
                    self._poll_loop()
                except Exception as e:
                    log.error("LabJack error: %s", e)
                    self._close_handle()
                    self.connected_changed.emit(False)

                if self._running and self._should_connect:
                    time.sleep(3.0)
            else:
                time.sleep(0.1)

        self._close_handle()

    def _poll_loop(self):
        """Open connection and poll AIN channels."""
        try:
            self._handle = ljm.openS("T7", "ETHERNET", self._ip)
        except ljm.LJMError as e:
            log.error("Failed to open LabJack at %s: %s", self._ip, e)
            self.connected_changed.emit(False)
            return

        info = ljm.getHandleInfo(self._handle)
        log.info("LabJack T7 connected — serial %d", info[2])
        self.connected_changed.emit(True)

        try:
            while self._running and self._should_connect:
                t0 = time.monotonic()

                try:
                    # Read both channels in one call
                    values = ljm.eReadNames(
                        self._handle, 2,
                        [LABJACK_PRESSURE_CHANNEL, LABJACK_VIBRATION_CHANNEL],
                    )
                    pressure_v = values[0]
                    vibration_v = values[1]
                except ljm.LJMError:
                    break

                pressure_torr = voltage_to_pressure_torr(pressure_v)
                vibration_mms = voltage_to_vibration_mms(vibration_v)

                self.sensors_updated.emit(pressure_torr, vibration_mms)

                elapsed = time.monotonic() - t0
                remaining = self._poll_interval - elapsed
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            self._close_handle()
            self.connected_changed.emit(False)

    def _close_handle(self):
        if self._handle is not None:
            try:
                ljm.close(self._handle)
            except Exception:
                pass
            self._handle = None
