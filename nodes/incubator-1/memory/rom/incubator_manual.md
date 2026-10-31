+++
node = "incubator-1, incubator-2"
title = "Incubator manual"
kind = "machine manual"
version = 1
status = "approved"
sources = [
  { title = "Farm simulation model: incubator device", link = "../../../../_dev/farm_poc_snapshot/devices/incubator.py" },
  { title = "Farm simulation model: incubator controller", link = "../../../../_dev/farm_poc_snapshot/controllers/incubator_controller.py" },
  { title = "Farm simulation model: physical world", link = "../../../../_dev/farm_poc_snapshot/matrix/physical_world.py" },
]
+++

# Incubator manual

*Derived from the farm simulation model, reviewed by the owner.* This manual is the authority on every incubator number:
when a guide disagrees, this manual wins. Both incubators are the same model.

## What it is

A cabinet incubator for 24 chicken eggs. Its own controller keeps the temperature and humidity, turns the eggs and
protects itself; it works without JARVIS. JARVIS watches it, explains what it sees and asks the other machines.

## Normal operation

| Quantity | Normal | Notes |
|---|---|---|
| Cabinet temperature | 37.5 °C setpoint | three room sensors inside the cabinet; the controller uses their median, so one bad probe cannot drive the heater |
| Heater outlet temperature | about 39 °C | air just after the heating element |
| Cabinet humidity | 55 % RH setpoint; 70 % RH during lockdown | three sensors; the humidifier switches on 2 % RH below the setpoint and off 1 % RH above it |
| Limits counted as "time outside limits" | 35 to 39 °C, 40 to 70 % RH | any sensor outside the band counts |
| Heater | 230 W at 230 V, time-proportioned output from 0 to 100 % | PI control |
| Fans | two fans, about 2,000 rpm at full speed; normally commanded to 60 % | fans and damper boost when the cabinet runs warm |
| Egg turning | every 3 hours (8 turns a day); the tray tilts between 0° and 45° | turning stops at lockdown |
| Water reservoir | 5 L, kept at 95 % | refilled through a hose from the farm water main by a float valve, at up to 1 L/min; inlet flow is reported |
| Supply | 230 V, 50 Hz | backup battery for outages |
| Air outside the cabinet | the workspace air, the same air the HVAC measures in the workspace | two ambient sensors, reported as `environment.ambient_temperature_c` and `ambient_humidity_pct` |

## Incubation phases (chicken eggs, 21 days)

| Phase | When | Temperature | Humidity | Turning |
|---|---|---|---|---|
| Incubation | until 3 days before the hatch | 37.5 °C | 55 % RH | every 3 hours |
| Lockdown | the last 3 days | 37.5 °C | 70 % RH | stopped |
| Hatching | the last day | 37.5 °C | 70 % RH | stopped |

The incubator reports the incubation day, the days remaining and the expected hatch date.

## Protection built into the incubator

- **Overvoltage relay:** above 260 V the heater is cut off. Codes `OVERVOLTAGE` (fault) and `OVERVOLTAGE_PROTECTION`
  (alarm). It resets by itself once the supply has stayed normal (180 to 255 V) for 120 s.
- **High-limit thermostat:** trips when any room sensor exceeds 39.5 °C or the heater outlet exceeds 65 °C. Codes
  `OVERTEMPERATURE` and `OVERTEMPERATURE_PROTECTION`. It resets by itself once the room is below 38.5 °C and the outlet
  below 50 °C.
- **Backup battery:** when the mains falls below 180 V the incubator runs on its backup battery and raises the
  `POWER_OUTAGE` alarm. Its controller labels the supply "undervoltage" below 180 V and "outage" below 50 V.

When one of these protections acts and the incubator recovers, the machine has handled it (HANDLED_BY_MACHINE).

## Fault codes the incubator reports

| Code | What the incubator measured | Likely cause |
|---|---|---|
| `FAN1_FAILURE`, `FAN2_FAILURE` | a fan below 10 % of its expected speed for 10 s while commanded above 20 % | stalled or failed fan |
| `HEATER_OPEN_CIRCUIT` | heater commanded above 5 % on mains power but drawing under 0.05 A for 5 s | broken element or wiring |
| `HEATER_RELAY_STUCK` | heater commanded to 0 % but still drawing over 0.2 A for 5 s; clears after 30 s without current | welded relay contacts |
| `PUMP_FAULT` | humidifier on, water in the reservoir, mains power, but the pump not running for 30 s | failed humidifier pump |
| `HUMIDIFIER_FAULT` | pump running but the water system drawing under 0.15 A for 30 s | failed humidifier element |
| `TURNING_FAULT` | a commanded turn did not move the tray | failed turning motor |
| `DOOR_SWITCH_FAULT` | the door switch gives no reading | failed door switch |
| `INTERNAL_POWER_FAULT` | a DC rail (5 V, 12 V or 24 V) more than 10 % off for 2 s | internal power supply fault |
| `LOW_WATER` (alarm) | reservoir at 15 % or less | water not refilling |
| `WATER_LEAK` (alarm) | the leak sensor detects water | leak |
| `POWER_OUTAGE` (alarm) | running on the backup battery (mains below 180 V) | mains failure or deep undervoltage |
| `BACKUP_BATTERY_LOW` (alarm) | on battery with under 20 % charge | long outage |

## Water: one hose or the whole farm?

- **One incubator's reservoir stops refilling** while the other incubator and Animal Protection have water: that
  incubator's own hose or float valve (WATER_INLET_FAULT). The incubator reports it.
- **Both incubators and Animal Protection lose water** together: the farm water main (WATER_SUPPLY_LOSS). Animal
  Protection measures the main's pressure and leads the incident; the incubators join it and watch.

## Seeing beyond the cabinet

The three room sensors measure the air inside the cabinet; the two ambient sensors measure the workspace air outside it.
If the workspace warms while the incubator itself is healthy, the heater output drops to compensate and the cause lies
elsewhere, for example the HVAC. If the HVAC's workspace sensor disagrees with both incubators' ambient sensors, the
HVAC sensor is the one to doubt.

## Approved protective action

`emergency_stop_heater`, in DANGER only: see `safety_actions.md`.
