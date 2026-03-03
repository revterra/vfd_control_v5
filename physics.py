"""
Power, torque, RPM, and kinetic energy calculations for the VFD/motor/flywheel system.
"""

import math
from constants import (
    MOTOR_POLES, MOTOR_RATED_VOLTAGE, MOTOR_RATED_CURRENT,
    MOTOR_POWER_FACTOR, FLYWHEEL_INERTIA, HP_TO_KW, KW_TO_HP, NM_TO_FTLB,
    VIBRATION_V_MIN, VIBRATION_V_MAX, VIBRATION_RANGE_MMS,
)


def freq_to_rpm(freq_hz: float) -> float:
    """Convert electrical frequency to synchronous RPM for the motor."""
    return 120.0 * freq_hz / MOTOR_POLES


def rpm_to_freq(rpm: float) -> float:
    """Convert RPM to electrical frequency."""
    return rpm * MOTOR_POLES / 120.0


def rpm_to_omega(rpm: float) -> float:
    """Convert RPM to angular velocity in rad/s."""
    return rpm * 2.0 * math.pi / 60.0


def voltage_from_percent(pct: float) -> float:
    """Convert VFD voltage % reading to actual volts."""
    return pct / 100.0 * MOTOR_RATED_VOLTAGE


def current_from_percent(pct: float) -> float:
    """Convert VFD current % reading to actual amps."""
    return pct / 100.0 * MOTOR_RATED_CURRENT


def calc_power_kw(voltage_v: float, current_a: float,
                  power_factor: float = MOTOR_POWER_FACTOR) -> float:
    """Calculate 3-phase power in kW: P = sqrt(3) * V * I * PF / 1000."""
    return math.sqrt(3) * voltage_v * current_a * power_factor / 1000.0


def calc_power_hp(power_kw: float) -> float:
    """Convert kW to HP."""
    return power_kw * KW_TO_HP


def calc_torque_nm(power_kw: float, rpm: float) -> float:
    """Calculate torque in N*m: T = P / omega. Returns 0 if RPM is ~0."""
    if rpm < 0.1:
        return 0.0
    omega = rpm_to_omega(rpm)
    return (power_kw * 1000.0) / omega


def calc_torque_ftlb(torque_nm: float) -> float:
    """Convert N*m to ft*lb."""
    return torque_nm * NM_TO_FTLB


def calc_kinetic_energy_kj(rpm: float, inertia: float = FLYWHEEL_INERTIA) -> float:
    """Calculate flywheel kinetic energy in kJ: KE = 0.5 * J * omega^2 / 1000."""
    omega = rpm_to_omega(rpm)
    return 0.5 * inertia * omega ** 2 / 1000.0


def calc_kinetic_energy_wh(rpm: float, inertia: float = FLYWHEEL_INERTIA) -> float:
    """Calculate flywheel kinetic energy in Wh: KE = 0.5 * J * omega^2 / 3600."""
    omega = rpm_to_omega(rpm)
    return 0.5 * inertia * omega ** 2 / 3600.0


def calc_torque_from_acceleration(
    rpm_now: float, rpm_prev: float, dt: float,
    inertia: float = FLYWHEEL_INERTIA,
) -> float:
    """Calculate torque from angular acceleration: T = J * dω/dt (N·m).

    Positive = accelerating, negative = decelerating.
    """
    if dt <= 0:
        return 0.0
    omega_now = rpm_to_omega(rpm_now)
    omega_prev = rpm_to_omega(rpm_prev)
    return inertia * (omega_now - omega_prev) / dt


def voltage_to_pressure_torr(voltage: float) -> float:
    """Convert pressure sensor voltage to Torr: P = 10^(V - 5)."""
    return 10.0 ** (voltage - 5.0)


def voltage_to_vibration_mms(voltage: float) -> float:
    """Convert vibration sensor voltage to mm/s RMS (linear 1-5V = 0-25 mm/s)."""
    return max(0.0, (voltage - VIBRATION_V_MIN)
               / (VIBRATION_V_MAX - VIBRATION_V_MIN) * VIBRATION_RANGE_MMS)
