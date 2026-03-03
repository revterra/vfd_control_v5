"""
Entry point for the Toshiba AS3 VFD Control System.
"""

import subprocess
import sys
from constants import ETHERNET_INTERFACE, REQUIRED_SECONDARY_IPS
from PyQt6.QtWidgets import QApplication
from dashboard import MainDashboard


def ensure_network():
    """Check that all required secondary IPs are present on the ethernet
    interface and add any that are missing (prompts for password via pkexec)."""
    try:
        result = subprocess.run(
            ["ip", "addr", "show", "dev", ETHERNET_INTERFACE],
            capture_output=True, text=True, timeout=5,
        )
        current = result.stdout
    except Exception as e:
        print(f"[network] Could not query {ETHERNET_INTERFACE}: {e}")
        return

    missing = []
    for ip_cidr, subnet_prefix in REQUIRED_SECONDARY_IPS:
        if subnet_prefix not in current:
            missing.append(ip_cidr)

    if not missing:
        return

    # Build a single script that adds all missing IPs
    cmds = " && ".join(
        f"ip addr add {ip} dev {ETHERNET_INTERFACE}" for ip in missing
    )
    print(f"[network] Adding missing IPs on {ETHERNET_INTERFACE}: "
          + ", ".join(missing))
    try:
        subprocess.run(
            ["pkexec", "bash", "-c", cmds],
            timeout=60,
        )
    except Exception as e:
        print(f"[network] Failed to add IPs: {e}")


def main():
    ensure_network()

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("Toshiba AS3 VFD Control")

    window = MainDashboard()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
