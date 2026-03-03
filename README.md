# VFD Control v1 -- Toshiba VF-AS3 Flywheel Drive Controller

Python GUI application for controlling a Toshiba VF-AS3 VFD over Ethernet (Modbus TCP), driving a 75 HP induction motor with a steel flywheel. Includes real-time monitoring, tachometer integration, and live plotting.

## Hardware

| Component | Details |
|-----------|---------|
| **VFD** | Toshiba TOSVERT VF-AS3 with Embedded Ethernet |
| **Motor** | 75 HP, 2-pole, 460V, 88A rated, 3600 RPM synchronous at 60 Hz |
| **Flywheel** | 50" diameter x 1.75" thick steel, 443 kg, J = 89.4 kg*m^2 |
| **Tachometer** | Monarch ACT-3X Panel Tachometer with Ethernet adapter |

## Network Configuration

The VFD and tachometer are on the same physical Ethernet switch but use different subnets. The host computer needs IP addresses on both subnets.

| Device | IP Address | Port | Subnet |
|--------|-----------|------|--------|
| VFD (Modbus TCP) | 192.168.2.100 | 502 | 192.168.2.0/24 |
| Tachometer (ACT-3X) | 192.168.50.205 | 5000 | 192.168.50.0/24 |
| Host PC (eno2) | 192.168.2.90 | -- | 192.168.2.0/24 |
| Host PC (eno2, secondary) | 192.168.50.90 | -- | 192.168.50.0/24 |

The secondary IP is added to the same interface:
```bash
sudo ip addr add 192.168.50.90/24 dev eno2
```
This is temporary and lost on reboot. To make it permanent, add it to your netplan or network configuration.

## Installation

```bash
pip install -r requirements.txt
python3 main.py
```

### Dependencies
- PyQt6
- pyqtgraph
- pyModbusTCP
- numpy

## VFD Modbus Registers

Reference: E6582125 Embedded Ethernet Function Manual

### Command Registers (Write)

| Register | Address | Description | Units |
|----------|---------|-------------|-------|
| FA36 | 0xFA36 | Communication command 1 (bit field) | -- |
| FA37 | 0xFA37 | Frequency command | 0.01 Hz |
| FA38 | 0xFA38 | Communication command 2 (bit field) | -- |
| ACC | 0x0009 | Acceleration time 1 | 0.1 s |
| dEC | 0x0010 | Deceleration time 1 | 0.1 s |

### FA36 Command Bit Field

| Bit | Function | 0 | 1 |
|-----|----------|---|---|
| 0-3 | Preset speed switching 1-4 | | |
| 4 | V/f switching 1 | V/f 1 | V/f 2 |
| 7 | DC braking | OFF | Forced DC braking |
| 9 | Forward/Reverse | Forward | Reverse |
| 10 | Run/Stop | Stop | Run |
| 11 | Coast stop | Standby | Coast stop |
| 12 | Emergency off | OFF | Emergency off |
| 13 | Fault reset | OFF | Reset |
| 14 | Frequency priority | OFF | Enabled (overrides FMOd) |
| 15 | Command priority | OFF | Enabled (overrides CMOd) |

**Key insight:** Bits 14 and 15 override the VFD's CMOd and FMOd keypad settings respectively. Setting both bits enables full Ethernet control regardless of panel configuration. Our base command is `0xC000` (bits 14+15), and RUN forward is `0xC400` (bits 14+15+10).

**Important:** When CMOd=2, bit 11 (Coast stop) is ON at startup. Must be cleared before the motor can run.

### Monitor Registers (Read)

| Register | Address | Description | Units |
|----------|---------|-------------|-------|
| FD00 | 0xFD00 | Output frequency | 0.01 Hz |
| FD01 | 0xFD01 | Inverter status 1 (bit field) | -- |
| FD03 | 0xFD03 | Output current | 0.01% of rated |
| FD05 | 0xFD05 | Output voltage | 0.01% of rated V |
| FD18 | 0xFD18 | Torque | 0.01% of rated N*m |
| FD29 | 0xFD29 | Input power | 0.01 kW |
| FD30 | 0xFD30 | Output power | 0.01 kW |
| FC91 | 0xFC91 | Alarm code (bit field) | -- |

### VFD Keypad Settings

The application uses FA36 bits 14-15 to override command/frequency source, so these keypad settings are **not strictly required** but are documented for reference:

| Parameter | Address | Value | Description |
|-----------|---------|-------|-------------|
| CMOd | 0x0003 | 2 | Embedded Ethernet (command source) |
| FMOd | 0x0004 | 20 | Embedded Ethernet (frequency source) |
| F897 | 0x0897 | 0 or 1 | Parameter writing (0=EEPROM, 1=RAM for comms) |

## Monarch ACT-3X Tachometer Protocol

The ACT-3X communicates over TCP (port 5000) via its Ethernet adapter. The adapter **echoes commands** and sends an `OK\r` acknowledgment before data.

### Commands

| Command | Description |
|---------|-------------|
| `@D0\r` | Send current displayed value once (limited to display rate ~2 Hz) |
| `@D1\r` | Stream display data continuously (limited to display rate ~2 Hz) |
| `@D2\r` | Stop continuous data output |
| `@D3\r` | Send last calculated reading (full internal throughput, up to 100/s) |

### Protocol Sequence (over Ethernet adapter)

Sending `@D3\r` produces:
```
@D3\r          <- echo of command
OK\r           <- acknowledgment
         5.0\r <- RPM value (space-padded ASCII)
```

**Key insight for high-rate acquisition:** `@D1` streaming is limited to the display update rate (~2 Hz max). For 10 Hz or faster acquisition, poll with `@D3` which returns the last internally calculated reading at up to 100 readings/sec (Standard Gate) or 1000/sec (Fast Gate).

The stop command is `@D2\r`, **not** `@D0\r` (which is a read command).

### ACT-3X Configuration (via front panel)

- **GATE**: Standard (1/100s) or Fast (1/1000s) -- controls internal measurement rate
- **Display Rate**: HALF (0.5s), 1_SEC, 1.5_S -- only affects @D0/@D1 commands

## Application Architecture

### File Structure

| File | Description |
|------|-------------|
| `main.py` | Entry point (QApplication + Fusion style) |
| `constants.py` | Register addresses, bit masks, motor/flywheel constants |
| `physics.py` | Power, torque, RPM, kinetic energy calculations |
| `vfd_controller.py` | QThread worker for Modbus TCP communication |
| `tach_reader.py` | QThread worker for ACT-3X tachometer polling |
| `dashboard.py` | PyQt6 main window (controls + readouts) and plot window |

### Thread Model

- **GUI thread**: PyQt6 event loop, widget updates (QTimer at 200 ms)
- **VFD worker thread**: `VFDController(QThread)` polls VFD registers every 200 ms
- **Tach worker thread**: `TachReader(QThread)` polls ACT-3X at 10 Hz via `@D3`
- **GUI -> VFD**: Commands sent via `queue.Queue` (thread-safe)
- **VFD -> GUI**: Status emitted via `pyqtSignal(VFDStatus dataclass)`
- **Tach -> GUI**: RPM emitted via `pyqtSignal(float)`

### Dual Window Layout

The application launches two windows for dual-monitor setups:

1. **Main Window** (controls + readouts): Speed control, V/f boost, ramp rates, direction, motor control (RUN/STOP), DC braking, tachometer connection, fault status
2. **Plot Window** (live graphs): Power (kW), Torque (VFD + calculated from tach dw/dt), RPM (sync + tach) -- 60-second rolling window, light theme

### Calculated Values

- **Sync RPM**: `120 * freq_hz / poles` (from VFD output frequency)
- **Tach RPM**: Direct measurement from ACT-3X
- **Calc Torque**: `T = J * dw/dt` (from consecutive tach RPM readings, J = 89.4 kg*m^2)
- **Kinetic Energy**: `KE = 0.5 * J * w^2` (uses tach RPM when available, else sync RPM)
- **Power**: Directly from VFD register FD30 (0.01 kW units)

## Usage Notes

1. **Set a non-zero frequency before pressing RUN.** The Hz spinbox defaults to 0. The motor will not start at 0 Hz.
2. **Connect tach independently.** The tachometer works regardless of VFD connection state.
3. **E-STOP** sends an emergency off command (FA36 bit 12). Always visible in toolbar.
4. **Close event** sends a STOP command to the VFD before exiting.
5. **EEPROM wear**: The VFD EEPROM has ~100,000 write cycles. Avoid writing inverter parameters (F-series registers like F171) frequently. The FA36/FA37 command registers are designed for real-time control.
