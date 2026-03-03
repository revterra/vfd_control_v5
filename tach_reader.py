"""
QThread TCP client for the Monarch ACT-3X panel tachometer.

Protocol (per ACT-3X manual Section 6, observed over Ethernet adapter):
  The Ethernet adapter echoes commands and sends an OK acknowledgment
  before data.  For example, sending "@D3\r" produces:
      @D3\r          (echo)
      OK\r           (acknowledgment)
           5.0\r     (RPM value, space-padded)

  Commands:
    @D3\r  — request last calculated reading (full internal throughput).
    @D0\r  — request current displayed value (limited to display rate).
    @D1\r  — stream display data continuously (limited to ~2 Hz).
    @D2\r  — stop continuous data output.

For >=10 Hz acquisition we poll with @D3 at TACH_SAMPLE_RATE_HZ intervals,
since @D1 is capped at the display update rate (~2 Hz).
"""

import socket
import time
import logging

from PyQt6.QtCore import QThread, pyqtSignal

from constants import TACH_PORT, TACH_RECONNECT_DELAY, TACH_SAMPLE_RATE_HZ

log = logging.getLogger(__name__)


class TachReader(QThread):
    """
    Worker thread that polls RPM from a Monarch ACT-3X tachometer.

    Uses the @D3 command to request the last calculated reading at up to
    TACH_SAMPLE_RATE_HZ.  The Ethernet adapter echoes commands and sends
    an OK acknowledgment before the data value — these are skipped
    automatically.

    Signals:
        rpm_updated(float): Emitted with each new RPM reading.
        connected_changed(bool): Emitted on connect/disconnect.
    """

    rpm_updated = pyqtSignal(float)
    connected_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._host: str = ""
        self._port: int = TACH_PORT
        self._running = False
        self._sock: socket.socket | None = None
        self._should_connect = False
        self._poll_interval = 1.0 / TACH_SAMPLE_RATE_HZ
        self._buf = b""

    def connect_tach(self, host: str, port: int = TACH_PORT):
        """Request connection to tachometer."""
        self._host = host
        self._port = port
        self._should_connect = True

    def disconnect_tach(self):
        """Request disconnection."""
        self._should_connect = False
        self._close_socket()

    def stop_thread(self):
        self._running = False
        self._should_connect = False
        self._close_socket()

    def run(self):
        self._running = True

        while self._running:
            if self._should_connect and self._host:
                try:
                    self._poll_loop()
                except Exception as e:
                    log.error("Tach connection error: %s", e)
                    self.connected_changed.emit(False)

                if self._running and self._should_connect:
                    time.sleep(TACH_RECONNECT_DELAY)
            else:
                time.sleep(0.1)

    def _poll_loop(self):
        """Connect and poll @D3 at TACH_SAMPLE_RATE_HZ."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(5.0)

        try:
            self._sock.connect((self._host, self._port))
        except (OSError, socket.error) as e:
            log.error("Failed to connect to %s:%d: %s",
                      self._host, self._port, e)
            self.connected_changed.emit(False)
            self._close_socket()
            return

        try:
            # Stop any prior continuous output
            self._sock.sendall(b"@D2\r")
            time.sleep(0.2)
            # Drain anything buffered (echo, OK, stale data)
            self._sock.setblocking(False)
            try:
                while self._sock.recv(4096):
                    pass
            except BlockingIOError:
                pass
            self._sock.setblocking(True)
            self._sock.settimeout(3.0)
            self._buf = b""

            self.connected_changed.emit(True)
            log.info("Tach connected to %s:%d", self._host, self._port)

            while self._running and self._should_connect:
                t0 = time.monotonic()

                # Request last calculated reading
                try:
                    self._sock.sendall(b"@D3\r")
                except (OSError, socket.error):
                    break

                # Read lines until we get a numeric RPM value
                # (skip command echo "@D3" and "OK" acknowledgment)
                rpm = self._read_rpm_value()
                if rpm is None:
                    break

                self.rpm_updated.emit(rpm)

                # Sleep remainder of interval to maintain target rate
                elapsed = time.monotonic() - t0
                remaining = self._poll_interval - elapsed
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            # Stop any output before closing
            try:
                if self._sock:
                    self._sock.sendall(b"@D2\r")
            except Exception:
                pass
            self._close_socket()
            self.connected_changed.emit(False)

    def _read_rpm_value(self) -> float | None:
        """
        Read lines from the socket until a numeric RPM value is found.
        Skips echo lines (starting with '@') and 'OK' acknowledgments.
        Returns None on connection loss or timeout.
        """
        for _ in range(5):  # max 5 lines per response (echo + OK + data)
            line = self._read_line()
            if line is None:
                return None

            text = line.decode("ascii", errors="ignore").strip()
            if not text:
                continue

            # Skip command echo (e.g. "@D3")
            if text.startswith("@"):
                continue

            # Skip acknowledgment
            if text.upper() == "OK":
                continue

            # Try to parse as RPM
            try:
                return float(text)
            except ValueError:
                log.debug("Ignoring non-numeric tach response: %r", text)
                continue

        log.debug("No numeric value found in tach response")
        return None

    def _read_line(self) -> bytes | None:
        """Read bytes until \\r delimiter. Returns None on connection loss."""
        while self._running and self._should_connect:
            # Check if we already have a complete line in the buffer
            if b"\r" in self._buf:
                line, self._buf = self._buf.split(b"\r", 1)
                return line

            try:
                data = self._sock.recv(1024)
            except socket.timeout:
                log.debug("Tach read timeout")
                return None
            except (OSError, socket.error):
                return None

            if not data:
                return None

            self._buf += data

        return None

    def _close_socket(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
