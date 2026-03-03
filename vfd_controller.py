"""
QThread worker for Modbus TCP communication with the Toshiba VF-AS3 VFD.

Uses FA36 (command word), FA37 (frequency), FA38 (command 2) registers
per the E6582125 Embedded Ethernet Function Manual.

Commands are received via a thread-safe queue.
Status is emitted via pyqtSignal.
"""

import queue
import time
from dataclasses import dataclass, field

from PyQt6.QtCore import QThread, pyqtSignal
from pyModbusTCP.client import ModbusClient

from constants import (
    DEFAULT_PORT, MODBUS_UNIT_ID, POLL_INTERVAL_MS, RECONNECT_THRESHOLD,
    REG_FA36_COMMAND, REG_FA37_FREQ, REG_FA38_COMMAND2,
    REG_ACCEL_TIME, REG_DECEL_TIME, REG_BASE_VOLTAGE, REG_VF_BOOST, REG_CARRIER_FREQ,
    REG_FD00_OUTPUT_FREQ, REG_FD01_STATUS,
    REG_FD03_OUTPUT_CURRENT, REG_FD04_INPUT_VOLTAGE, REG_FD05_OUTPUT_VOLTAGE,
    REG_FD18_TORQUE, REG_FD29_INPUT_POWER, REG_FD30_OUTPUT_POWER,
    REG_FC91_ALARM,
    FA36_BASE, FA36_RUN, FA36_REVERSE, FA36_COAST_STOP,
    FA36_DC_BRAKE, FA36_FAULT_RESET, FA36_EMERGENCY_OFF,
    FD01_TRIPPED, FD01_ALARM, FD01_RUN_STOP, FD01_FWD_REV,
)


@dataclass
class VFDStatus:
    """Data emitted from the worker thread to the GUI."""
    connected: bool = False
    output_freq_hz: float = 0.0
    output_current_pct: float = 0.0
    output_voltage_pct: float = 0.0
    input_voltage_pct: float = 0.0
    output_power_kw: float = 0.0
    input_power_kw: float = 0.0
    torque_pct: float = 0.0
    inverter_status: int = 0
    alarm_code: int = 0
    error_message: str = ""
    timestamp: float = field(default_factory=time.time)


class VFDController(QThread):
    """
    Worker thread that polls VFD registers and processes commands.

    Uses FA36 bit field for run/stop/direction/DC brake commands.
    Bit 11 (coast stop) is ON by default when CMOd=2; we always clear it.
    """

    status_updated = pyqtSignal(object)  # VFDStatus dataclass

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client: ModbusClient | None = None
        self._command_queue: queue.Queue = queue.Queue()
        self._running = False
        self._connected = False
        self._consecutive_errors = 0
        # Track current FA36 state so we can set/clear individual bits
        self._fa36_state = FA36_BASE  # priorities on, coast stop off

    # --- Public API (called from GUI thread) ---

    def is_connected(self) -> bool:
        return self._connected

    def send_command(self, cmd: dict):
        """Enqueue a command dict for the worker thread to process."""
        self._command_queue.put(cmd)

    def connect_vfd(self, ip: str, port: int = DEFAULT_PORT):
        self.send_command({"type": "connect", "ip": ip, "port": port})

    def disconnect_vfd(self):
        self.send_command({"type": "disconnect"})

    def set_frequency(self, freq_hz: float):
        self.send_command({"type": "set_freq", "value": freq_hz})

    def set_accel_time(self, seconds: float):
        self.send_command({"type": "set_accel", "value": seconds})

    def set_decel_time(self, seconds: float):
        self.send_command({"type": "set_decel", "value": seconds})

    def set_base_voltage(self, volts: float):
        self.send_command({"type": "set_base_voltage", "value": volts})

    def set_vf_boost(self, volts: float):
        self.send_command({"type": "set_vf_boost", "value": volts})

    def set_carrier_freq(self, khz: float):
        self.send_command({"type": "set_carrier_freq", "value": khz})

    def run_forward(self):
        self.send_command({"type": "run", "direction": "forward"})

    def run_reverse(self):
        self.send_command({"type": "run", "direction": "reverse"})

    def stop_motor(self):
        self.send_command({"type": "stop"})

    def coast_stop(self):
        """Coast to stop — cut power and let the load spin down freely."""
        self.send_command({"type": "coast_stop"})

    def emergency_stop(self):
        self.send_command({"type": "estop"})

    def set_dc_brake(self, enable: bool, voltage_pct: float = 0.0):
        self.send_command({
            "type": "dc_brake", "enable": enable,
            "voltage_pct": voltage_pct,
        })

    def reset_fault(self):
        self.send_command({"type": "fault_reset"})

    def stop_thread(self):
        self._running = False

    # --- Thread run loop ---

    def run(self):
        self._running = True
        poll_interval = POLL_INTERVAL_MS / 1000.0

        while self._running:
            loop_start = time.time()

            # Process all pending commands
            while not self._command_queue.empty():
                try:
                    cmd = self._command_queue.get_nowait()
                    self._process_command(cmd)
                except queue.Empty:
                    break

            # Poll status if connected
            if self._connected and self._client:
                status = self._poll_status()
                self.status_updated.emit(status)
            else:
                self.status_updated.emit(VFDStatus(connected=False))

            # Maintain poll interval
            elapsed = time.time() - loop_start
            sleep_time = poll_interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        # Cleanup on exit
        self._do_disconnect()

    # --- Command processing ---

    def _process_command(self, cmd: dict):
        cmd_type = cmd.get("type")

        if cmd_type == "connect":
            self._do_connect(cmd["ip"], cmd["port"])

        elif cmd_type == "disconnect":
            self._do_disconnect()

        elif cmd_type == "set_freq":
            # FA37: frequency in 0.01 Hz units
            self._write_register(
                REG_FA37_FREQ, int(cmd["value"] * 100)
            )

        elif cmd_type == "set_accel":
            self._write_register(
                REG_ACCEL_TIME, int(cmd["value"] * 10)
            )

        elif cmd_type == "set_decel":
            self._write_register(
                REG_DECEL_TIME, int(cmd["value"] * 10)
            )

        elif cmd_type == "set_base_voltage":
            # F171: 0.1V units
            self._write_register(
                REG_BASE_VOLTAGE, int(cmd["value"] * 10)
            )

        elif cmd_type == "set_vf_boost":
            # F402 vb: 0.1V units
            self._write_register(
                REG_VF_BOOST, int(cmd["value"] * 10)
            )

        elif cmd_type == "set_carrier_freq":
            # F300: 0.1 kHz units
            self._write_register(
                REG_CARRIER_FREQ, int(cmd["value"] * 10)
            )

        elif cmd_type == "run":
            # Set run bit, set direction, clear coast stop
            self._fa36_state = FA36_BASE | FA36_RUN
            if cmd["direction"] == "reverse":
                self._fa36_state |= FA36_REVERSE
            else:
                self._fa36_state &= ~FA36_REVERSE
            self._fa36_state &= ~FA36_COAST_STOP
            self._write_register(REG_FA36_COMMAND, self._fa36_state)

        elif cmd_type == "stop":
            # Clear run bit, keep priorities, coast stop off (decel to stop)
            self._fa36_state = FA36_BASE
            self._write_register(REG_FA36_COMMAND, self._fa36_state)

        elif cmd_type == "coast_stop":
            # Set coast stop bit — cuts power, load spins down freely
            self._fa36_state = FA36_BASE | FA36_COAST_STOP
            self._write_register(REG_FA36_COMMAND, self._fa36_state)
            # Clear coast stop bit after a moment so we can run again later
            time.sleep(0.2)
            self._fa36_state = FA36_BASE
            self._write_register(REG_FA36_COMMAND, self._fa36_state)

        elif cmd_type == "estop":
            # Emergency off: bit 12
            self._fa36_state = FA36_BASE | FA36_EMERGENCY_OFF
            self._write_register(REG_FA36_COMMAND, self._fa36_state)

        elif cmd_type == "dc_brake":
            if cmd["enable"]:
                # Set DC brake bit in FA36
                self._fa36_state |= FA36_DC_BRAKE
                # Clear run bit during braking
                self._fa36_state &= ~FA36_RUN
            else:
                self._fa36_state &= ~FA36_DC_BRAKE
            self._write_register(REG_FA36_COMMAND, self._fa36_state)

        elif cmd_type == "fault_reset":
            # Pulse fault reset bit
            self._write_register(
                REG_FA36_COMMAND, self._fa36_state | FA36_FAULT_RESET
            )
            time.sleep(0.1)
            self._write_register(REG_FA36_COMMAND, self._fa36_state)

    # --- Modbus helpers ---

    def _do_connect(self, ip: str, port: int):
        self._do_disconnect()
        try:
            self._client = ModbusClient(
                host=ip, port=port, auto_open=False,
                timeout=2.0, unit_id=MODBUS_UNIT_ID,
            )
            if self._client.open():
                self._connected = True
                self._consecutive_errors = 0
                # Clear coast stop bit on connect (ON by default with CMOd=2)
                self._fa36_state = FA36_BASE
                self._write_register(REG_FA36_COMMAND, self._fa36_state)
            else:
                self._connected = False
                self.status_updated.emit(VFDStatus(
                    connected=False,
                    error_message=f"Failed to connect to {ip}:{port}",
                ))
        except Exception as e:
            self._connected = False
            self.status_updated.emit(VFDStatus(
                connected=False, error_message=str(e),
            ))

    def _do_disconnect(self):
        if self._client:
            try:
                if self._connected:
                    # Stop motor and clear command on disconnect
                    self._client.write_single_register(
                        REG_FA36_COMMAND, FA36_BASE
                    )
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None
        self._connected = False

    def _write_register(self, address: int, value: int):
        if not self._connected or not self._client:
            return
        try:
            result = self._client.write_single_register(address, value)
            if not result:
                self._handle_error(f"Write 0x{address:04X} failed")
            else:
                self._consecutive_errors = 0
        except Exception as e:
            self._handle_error(str(e))

    def _read_registers(self, address: int, count: int) -> list[int] | None:
        if not self._connected or not self._client:
            return None
        try:
            result = self._client.read_holding_registers(address, count)
            if result is None:
                self._handle_error("Read failed")
                return None
            self._consecutive_errors = 0
            return result
        except Exception as e:
            self._handle_error(str(e))
            return None

    def _poll_status(self) -> VFDStatus:
        status = VFDStatus(connected=True)

        # FD00: Output frequency (0.01 Hz)
        regs = self._read_registers(REG_FD00_OUTPUT_FREQ, 1)
        if regs:
            status.output_freq_hz = regs[0] / 100.0

        # FD01: Inverter status
        regs = self._read_registers(REG_FD01_STATUS, 1)
        if regs:
            status.inverter_status = regs[0]

        # FD03: Output current (0.01%)
        regs = self._read_registers(REG_FD03_OUTPUT_CURRENT, 1)
        if regs:
            status.output_current_pct = regs[0] / 100.0

        # FD05: Output voltage (0.01%)
        regs = self._read_registers(REG_FD05_OUTPUT_VOLTAGE, 1)
        if regs:
            status.output_voltage_pct = regs[0] / 100.0

        # FD18: Torque (0.01% of rated)
        regs = self._read_registers(REG_FD18_TORQUE, 1)
        if regs:
            status.torque_pct = regs[0] / 100.0

        # FD30: Output power (0.01 kW)
        regs = self._read_registers(REG_FD30_OUTPUT_POWER, 1)
        if regs:
            status.output_power_kw = regs[0] / 100.0

        # FC91: Alarm code
        regs = self._read_registers(REG_FC91_ALARM, 1)
        if regs:
            status.alarm_code = regs[0]

        status.connected = self._connected
        return status

    def _handle_error(self, msg: str):
        self._consecutive_errors += 1
        if self._consecutive_errors >= RECONNECT_THRESHOLD:
            if self._client:
                try:
                    self._client.close()
                except Exception:
                    pass
                try:
                    if self._client.open():
                        self._connected = True
                        self._consecutive_errors = 0
                        return
                except Exception:
                    pass
                self._connected = False
                self.status_updated.emit(VFDStatus(
                    connected=False,
                    error_message=f"Lost connection ({msg}). Reconnecting...",
                ))
