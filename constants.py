"""
Toshiba VF-AS3 VFD register addresses, bit masks, motor/flywheel constants.
Register map from E6582125 Embedded Ethernet Function Manual.
"""

# --- Modbus TCP ---
DEFAULT_PORT = 502
DEFAULT_IP = "192.168.2.100"
MODBUS_UNIT_ID = 1
POLL_INTERVAL_MS = 200
RECONNECT_THRESHOLD = 5  # consecutive errors before auto-reconnect

# --- VFD prerequisite settings (set on keypad) ---
# CMOd (0x0003) = 2  (Embedded Ethernet) — or use FA36 bit 15 to override
# FMOd (0x0004) = 20 (Embedded Ethernet) — or use FA36 bit 14 to override

# --- Command registers (write) ---
# FA36: Communication command 1 from embedded ethernet (bit field)
REG_FA36_COMMAND = 0xFA36
# FA37: Frequency command from embedded ethernet (0.01 Hz units)
REG_FA37_FREQ = 0xFA37
# FA38: Communication command 2 from embedded ethernet (bit field)
REG_FA38_COMMAND2 = 0xFA38

# Accel/decel times (0.1 s units per F519 setting)
REG_ACCEL_TIME = 0x0009   # ACC: Acceleration time 1
REG_DECEL_TIME = 0x0010   # dEC: Deceleration time 1

# V/f parameters
REG_BASE_VOLTAGE = 0x0171  # F171: Base voltage for V/f pattern 2 (0.1V units)
REG_VF_BOOST = 0x0402      # F402: Manual torque boost vb (0.1V units)
REG_CARRIER_FREQ = 0x0300  # F300: Carrier frequency (0.1 kHz units)

# --- FA36 command word bit masks ---
# Bits 0-3: Preset speed switching 1-4
FA36_VF_SWITCH     = 1 << 4   # V/f switching 1
FA36_PID_OFF       = 1 << 5   # PID control OFF
FA36_AD_SWITCH     = 1 << 6   # Acc/Dec switching 1
FA36_DC_BRAKE      = 1 << 7   # DC braking (1=Forced DC braking)
FA36_JOG           = 1 << 8   # Jog run
FA36_REVERSE       = 1 << 9   # Forward/Reverse (0=Forward, 1=Reverse)
FA36_RUN           = 1 << 10  # Run/Stop (0=Stop, 1=Run)
FA36_COAST_STOP    = 1 << 11  # Coast stop (0=Standby, 1=Coast stop)
FA36_EMERGENCY_OFF = 1 << 12  # Emergency off
FA36_FAULT_RESET   = 1 << 13  # Fault reset
FA36_FREQ_PRIORITY = 1 << 14  # Frequency priority (enables FA37)
FA36_CMD_PRIORITY  = 1 << 15  # Command priority (enables FA36 run/stop)

# Convenience: base command with both priorities enabled, coast stop OFF
FA36_BASE = FA36_CMD_PRIORITY | FA36_FREQ_PRIORITY  # 0xC000

# --- FA38 command 2 bit masks ---
FA38_TORQUE_MODE    = 1 << 0   # 0=Speed control, 1=Torque control
FA38_POWER_RESET    = 1 << 1   # Electric power quantity reset
FA38_BRAKE_REQUEST  = 1 << 3   # Braking request (BC)
FA38_PRELIM_EXCITE  = 1 << 4   # Preliminary excitation

# --- Monitor registers (read) ---
REG_FD00_OUTPUT_FREQ    = 0xFD00  # Output frequency (0.01 Hz units)
REG_FD01_STATUS         = 0xFD01  # Inverter status 1 (bit field)
REG_FD03_OUTPUT_CURRENT = 0xFD03  # Output current (0.01% of rated)
REG_FD04_INPUT_VOLTAGE  = 0xFD04  # Input voltage DC detection (0.01% V)
REG_FD05_OUTPUT_VOLTAGE = 0xFD05  # Output voltage (0.01% V)
REG_FD18_TORQUE         = 0xFD18  # Torque (0.01% of rated N*m)
REG_FD29_INPUT_POWER    = 0xFD29  # Input power (0.01 kW)
REG_FD30_OUTPUT_POWER   = 0xFD30  # Output power (0.01 kW)
REG_FC91_ALARM          = 0xFC91  # Alarm code (bit field)

# --- FD01 inverter status bit masks ---
FD01_FAILURE_FL   = 1 << 0
FD01_TRIPPED      = 1 << 1
FD01_ALARM        = 1 << 2
FD01_UNDER_VOLT   = 1 << 3
FD01_DC_BRAKING   = 1 << 7
FD01_FWD_REV      = 1 << 9   # 0=Forward, 1=Reverse
FD01_RUN_STOP     = 1 << 10  # 0=Stop, 1=Run
FD01_COAST_STOP   = 1 << 11
FD01_EMERGENCY    = 1 << 12

# --- Motor nameplate (75 HP, 2-pole, 460V) ---
MOTOR_RATED_HP = 75
MOTOR_RATED_VOLTAGE = 460.0   # V line-to-line
MOTOR_RATED_CURRENT = 88.0    # A (typical for 75 HP @ 460V)
MOTOR_RATED_TORQUE_NM = 148.0 # N*m (approx rated torque at full load)
MOTOR_POLES = 2
MOTOR_RATED_FREQ = 60.0       # Hz
MOTOR_SYNC_RPM = 3600.0       # 120 * 60 / 2
MOTOR_POWER_FACTOR = 0.88     # Assumed PF

# --- Flywheel ---
FLYWHEEL_DIAMETER_IN = 50.0   # inches
FLYWHEEL_THICKNESS_IN = 1.75  # inches
FLYWHEEL_MASS_KG = 443.0      # kg
FLYWHEEL_INERTIA = 89.4       # kg*m² (J)

# --- Frequency/speed limits ---
MAX_FREQ_HZ = 60.0
MIN_FREQ_HZ = 0.0
MAX_RPM = 3600.0
MIN_RPM = 0.0

# --- Ramp time limits (seconds) ---
MIN_RAMP_TIME = 0.1
MAX_RAMP_TIME = 600.0
DEFAULT_ACCEL_TIME = 60.0  # conservative for high-inertia flywheel
DEFAULT_DECEL_TIME = 60.0

# --- V/f limits ---
MAX_BASE_VOLTAGE = 460.0   # V
MIN_BASE_VOLTAGE = 0.0
MAX_VF_BOOST_V = 50.0      # V (low-speed torque boost)
MIN_VF_BOOST_V = 0.0

# --- Carrier frequency limits (kHz) ---
MIN_CARRIER_FREQ_KHZ = 0.5
MAX_CARRIER_FREQ_KHZ = 16.0
DEFAULT_CARRIER_FREQ_KHZ = 8.0

# --- DC brake limits ---
MAX_DC_BRAKE_PCT = 100.0
MIN_DC_BRAKE_PCT = 0.0
DEFAULT_DC_BRAKE_TIME = 5.0  # seconds

# --- LabJack T7 DAQ ---
LABJACK_IP = "192.168.50.4"
LABJACK_SAMPLE_RATE_HZ = 10  # poll rate for AIN reads

# AIN0: Pressure sensor (1-8V, log-linear Torr, P = 10^(V-5))
LABJACK_PRESSURE_CHANNEL = "AIN0"
PRESSURE_V_MIN = 1.0
PRESSURE_V_MAX = 8.0

# AIN1: Vibration sensor (1-5V, linear 0-25 mm/s RMS)
LABJACK_VIBRATION_CHANNEL = "AIN1"
VIBRATION_V_MIN = 1.0
VIBRATION_V_MAX = 5.0
VIBRATION_RANGE_MMS = 25.0  # mm/s RMS at max voltage
VIBRATION_WARNING_MMS = 8.0  # mm/s threshold for vibration warning

# --- Camera (Reolink RLC-510A) ---
CAMERA_IP = "192.168.50.10"
CAMERA_RTSP_URL = "rtsp://admin:@192.168.50.10/h264Preview_01_sub"
CAMERA_FPS = 10

# --- Torque smoothing ---
TORQUE_SMOOTHING_ALPHA = 0.15  # EMA alpha (lower = smoother, 0.15 ≈ 0.7s tau @ 10 Hz)

# --- Tachometer (Monarch ACT-3X) ---
TACH_IP = "192.168.50.205"
TACH_PORT = 5000
TACH_RECONNECT_DELAY = 3.0  # seconds between reconnect attempts
TACH_SAMPLE_RATE_HZ = 10    # emit RPM signal at this rate (throttle)

# --- Plot ---
PLOT_WINDOW_SECONDS = 60
PLOT_HISTORY_SIZE = 300  # 60s / 0.2s = 300 samples

# --- Network setup ---
# Secondary IPs to add to the ethernet interface if not already present.
# Each entry is (ip/prefix, subnet_to_check). The subnet_to_check is used
# to see if any address on that subnet already exists on the interface.
ETHERNET_INTERFACE = "eno2"
REQUIRED_SECONDARY_IPS = [
    ("192.168.50.1/24", "192.168.50."),
]

# --- Conversions ---
HP_TO_KW = 0.7457
KW_TO_HP = 1.0 / HP_TO_KW
NM_TO_FTLB = 0.7376
