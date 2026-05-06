# skill: system/battery

description: Returns the current battery level and charging state of the host device.

params: none

returns:
  level:    integer (0–100) — battery percentage
  charging: boolean — true if plugged in and charging or full
  status:   string — raw sysfs status string ("Charging", "Discharging", "Full", "stub")

example response:
  { "level": 72, "charging": false, "status": "Discharging" }
