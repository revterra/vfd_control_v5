"""
Background thread that grabs frames from an RTSP camera using OpenCV.
A fast grab loop runs continuously to keep the buffer drained;
the display loop picks up only the latest decoded frame.
"""

import os
import cv2
import threading
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage


class CameraReader(QThread):
    """Reads RTSP frames in a background thread, emits QImage signals."""

    frame_ready = pyqtSignal(QImage)
    connected_changed = pyqtSignal(bool)

    def __init__(self, url: str = "", fps: int = 10, parent=None):
        super().__init__(parent)
        self._url = url
        self._interval_ms = int(1000 / fps) if fps > 0 else 100
        self._running = False
        self._cap = None
        self._latest_frame = None
        self._frame_lock = threading.Lock()

    def set_url(self, url: str):
        self._url = url

    def _grab_loop(self):
        """Runs in a plain thread — grabs frames as fast as possible
        so the OpenCV/ffmpeg buffer never fills up."""
        while self._running and self._cap and self._cap.isOpened():
            ret, frame = self._cap.read()
            if not ret:
                self._running = False
                self.connected_changed.emit(False)
                return
            with self._frame_lock:
                self._latest_frame = frame

    def run(self):
        self._running = True

        # Force ffmpeg low-latency demuxing
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "fflags;nobuffer|flags;low_delay|analyzeduration;0|probesize;32768|max_delay;0"
        )

        self._cap = cv2.VideoCapture(self._url, cv2.CAP_FFMPEG)
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self._cap.isOpened():
            self.connected_changed.emit(False)
            return

        self.connected_changed.emit(True)

        # Start a fast grab thread to keep the buffer drained
        grabber = threading.Thread(target=self._grab_loop, daemon=True)
        grabber.start()

        # Emit the latest frame at the desired display rate
        while self._running:
            self.msleep(self._interval_ms)

            with self._frame_lock:
                frame = self._latest_frame
                self._latest_frame = None

            if frame is None:
                continue

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            img = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
            self.frame_ready.emit(img)

        grabber.join(timeout=2)
        if self._cap:
            self._cap.release()
            self._cap = None

    def stop_thread(self):
        self._running = False
