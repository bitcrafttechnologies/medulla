# skill: system/battery
# Returns battery level and charging state.
# On non-Jetson systems, reads from /sys/class/power_supply (Linux)
# or falls back to a stub for development.

import os
import sys
from pathlib import Path

def run(params: dict) -> dict:
    platform = sys.platform

    result = None
    if platform == "linux":
        result = _linux()
    elif platform == "darwin":
        result = _macos()
    elif platform == "win32":
        result = _windows()

    if result is not None:
        return result

    # Dev stub — no battery hardware or unrecognised platform
    return {"level": 100, "charging": True, "status": "stub"}

def _linux() -> dict | None:
    ps_root = Path("/sys/class/power_supply")
    if not ps_root.exists():
        return None
    for entry in ps_root.iterdir():
        cap_file    = entry / "capacity"
        status_file = entry / "status"
        if cap_file.exists():
            level    = int(cap_file.read_text().strip())
            status   = status_file.read_text().strip() if status_file.exists() else "Unknown"
            charging = status in ("Charging", "Full")
            return {"level": level, "charging": charging, "status": status}
    return None


def _macos() -> dict | None:
    import subprocess, json, re
    try:
        raw = subprocess.check_output(
            ["ioreg", "-r", "-c", "AppleSmartBattery", "-a"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    # ioreg -a emits Apple plist XML; parse with plistlib
    import plistlib
    try:
        data = plistlib.loads(raw.encode() if isinstance(raw, str) else raw)
        batt = data[0] if isinstance(data, list) else data
    except Exception:
        return None

    max_cap  = batt.get("MaxCapacity", 0)
    cur_cap  = batt.get("CurrentCapacity", 0)
    level    = round(cur_cap / max_cap * 100) if max_cap else 0
    charging = bool(batt.get("IsCharging", False))
    full     = bool(batt.get("FullyCharged", False))

    if full:
        status = "Full"
    elif charging:
        status = "Charging"
    else:
        status = "Discharging"

    return {"level": level, "charging": charging or full, "status": status}


def _windows() -> dict | None:
    try:
        import ctypes, ctypes.wintypes

        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus",        ctypes.c_byte),
                ("BatteryFlag",         ctypes.c_byte),
                ("BatteryLifePercent",  ctypes.c_byte),
                ("SystemStatusFlag",    ctypes.c_byte),
                ("BatteryLifeTime",     ctypes.wintypes.DWORD),
                ("BatteryFullLifeTime", ctypes.wintypes.DWORD),
            ]

        sps = SYSTEM_POWER_STATUS()
        ok  = ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps))
        if not ok:
            return None

        raw_pct  = sps.BatteryLifePercent          # 255 = unknown
        level    = raw_pct if raw_pct != 255 else 0
        ac       = sps.ACLineStatus == 1            # 1 = plugged in
        flag     = sps.BatteryFlag

        # Flag 8 = no battery, 128 = unknown
        if flag in (8, 128):
            return None

        charging = ac and not (flag & 4)            # flag bit 4 = "no charge"
        full     = bool(flag & 8) or level == 100   # flag bit 3 = fully charged... reuse 8? 
        # Windows uses bit positions: 1=high,2=low,4=critical,8=charging,128=no battery
        charging = bool(flag & 8)                   # bit 8 = actively charging
        full     = ac and level == 100

        if full:
            status = "Full"
        elif charging:
            status = "Charging"
        elif ac:
            status = "Plugged In"
        else:
            status = "Discharging"

        return {"level": level, "charging": charging or full, "status": status}

    except Exception:
        return None
