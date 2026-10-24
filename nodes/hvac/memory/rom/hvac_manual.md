+++
node = "hvac"
title = "HVAC manual (central air handler)"
kind = "machine manual"
version = 1
status = "draft"
sources = [
  { title = "Farm simulation model: HVAC device", link = "../../../../_dev/farm_poc_snapshot/devices/hvac.py" },
  { title = "Farm simulation model: HVAC controller", link = "../../../../_dev/farm_poc_snapshot/controllers/hvac_controller.py" },
]
+++

# HVAC manual (central air handler)

*Derived from the farm simulation model, reviewed by the owner.* This manual is the authority on every HVAC number: when
a guide disagrees, this manual wins.

## What it is

One air handler serving three zones: the barn, the storage area and the workspace (where the two incubators stand). Its
own controller runs without JARVIS. JARVIS watches it, explains what it sees and compares it with the other machines.

## Normal operation

| Zone | Temperature setpoint | Humidity setpoint | Counted as "outside limits" |
|---|---|---|---|
| Barn | 24 °C | 60 % RH | more than 2 °C or 15 % RH from the setpoint |
| Storage | 22 °C | 50 % RH | same |
| Workspace | 23 °C | 45 % RH | same |

| Quantity | Normal | Notes |
|---|---|---|
| Cooling | 10 kW rated, compressor with refrigerant | the compressor stays off at least 300 s and on at least 120 s between switches |
| Refrigerant pressure | about 850 kPa | below 550 kPa with the compressor on is a leak sign |
| Filter pressure drop | about 35 Pa when clean | rises as the filter loads |
| Duct static pressure | about 120 Pa | |
| Supply fan | speed follows the larger of the cooling and heating demands | |
| Return fan | runs at a fixed command (50 % by default) | |

## What the controller does by itself

- PI cooling on the warmest zone, with compressor staging.
- Economizer: more outdoor air when outside is cooler than the return air; the compressor is locked out when outdoor air
  alone can cool.
- Electric heating when a zone falls well below its setpoint.
- Zone dampers open toward the zones that need it most.
- Airflow-proving interlock: no compressor or heating without a proven fan.
- **Fire shutdown mode** stops all air movement: fans, outdoor air, dampers, compressor and heating, until reset.

## Fault codes the HVAC reports

| Code | What the HVAC measured | Likely cause |
|---|---|---|
| `SUPPLY_FAN_FAILURE` | supply fan below 10 % of its command for 10 s while commanded above 20 % | failed supply fan |
| `RETURN_FAN_FAILURE` | return fan below 10 % of its command for 10 s while commanded above 20 % | failed return fan |
| `COMPRESSOR_FAILURE` | compressor requested and allowed but not running for 330 s | failed compressor |
| `LOW_REFRIGERANT_PRESSURE` | under 550 kPa for 30 s with the compressor on; the compressor locks out | refrigerant leak |
| `CONDENSATE_OVERFLOW` | condensate pan at 0.4 L or more (float switch); blocks the compressor | blocked condensate drain |
| `CONDENSATE_LEAK` (alarm) | the leak sensor detects water | leak |
| `FILTER_MAINTENANCE` (alarm) | filter resistance at twice a clean filter's (maintenance due) or three times (clogged) | dirty filter |

The HVAC reports its mode as `fault` when the supply fan, the compressor, the refrigerant pressure or the condensate
overflow has a fault.

## Seeing beyond the air handler

- Each incubator has two ambient sensors outside its cabinet that read the workspace air. If the HVAC's workspace
  sensor disagrees with both incubators' ambient sensors, the HVAC sensor is the one to doubt.
- When the HVAC loses cooling, the zones warm: the incubators see their workspace warm and their heaters back off, and
  Animal Protection sees the barn warm. They can name the HVAC as the cause.

## Approved protective actions

`fire_shutdown` and `emergency_ventilation`, in DANGER only: see `safety_actions.md`.
