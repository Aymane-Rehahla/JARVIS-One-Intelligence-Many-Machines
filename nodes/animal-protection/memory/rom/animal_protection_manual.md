+++
node = "animal-protection"
title = "Animal Protection manual"
kind = "machine manual"
version = 1
status = "approved"
sources = [
  { title = "Farm simulation model: animal-protection device", link = "../../../../_dev/farm_poc_snapshot/devices/animal_protection.py" },
  { title = "Farm simulation model: animal-protection controller", link = "../../../../_dev/farm_poc_snapshot/controllers/animal_protection_controller.py" },
]
+++

# Animal Protection manual

*Derived from the farm simulation model, reviewed by the owner.* This manual is the authority on every Animal Protection
number: when a guide disagrees, this manual wins.

## What it is

The unit that protects the barn animals: feed and water, barn air quality, weather and disaster warnings, and the
building's protective equipment (windows, vents, gates, storm shutters, flood barrier, alarm). Its own controller runs
without JARVIS. It also measures the pressure of the farm water main, so it leads any farm-wide water incident.

## Normal operation

| Quantity | Normal | Alarm |
|---|---|---|
| CO₂ in the barn | about 600 ppm | `CO2_HIGH` at 3,000 ppm or more (clears below 2,500 ppm) |
| O₂ in the barn | 20.9 % | `O2_LOW` below 19.5 % (clears above 19.8 %) |
| Ammonia in the barn | about 5 ppm | `AMMONIA_HIGH` at 25 ppm or more (clears below 20 ppm) |
| CO in the barn | 0 ppm | `CO_HIGH` at 35 ppm or more (clears below 25 ppm) |
| Smoke detector | clear | `SMOKE_DETECTED` at 8 %/m obscuration or more (clears below 4 %/m) |
| Barn temperature (from the HVAC) | about 24 °C | `HEAT_STRESS` at 30 °C or more (clears below 28 °C) |
| Drinking-water tank | 1,000 L | |
| Drinking-water pressure | about 250 kPa | |
| Farm water main pressure | about 300 kPa | |
| Feed storage | 50 kg capacity | |

## What the controller does by itself

Using only its own sensors, the HVAC's barn readings and the authorised weather-warning feed:
- **Tornado warning** (from the feed only, never from local wind alone): deploy the storm shutters, close windows and
  gates, vents to a minimum. A manual deploy of the shutters or the flood barrier holds until it is retracted by hand.
- **Severe weather warning:** close windows and gates, vents to a minimum.
- **Flood warning or rising floodwater:** deploy the flood barrier and close the low vents. Flood mode ends only after
  the water has stayed below 2 cm (no warning, no water entering) for 30 minutes.
- **Smoke:** close windows and vents to limit the air feeding a fire.
- **Poor air** (CO₂, ammonia, CO): open vents and windows to purge.
- **Barn too warm:** open the windows when outside is cooler; otherwise close them and ask the HVAC for cooling.
- When a protective closure conflicts with air quality or heat, it records the conflict and asks the HVAC for emergency
  ventilation. Requests to the HVAC are limited to one every 10 minutes.

## Disaster risk levels

| Level | When |
|---|---|
| emergency | tornado warning, water entering the barn, or floodwater of 30 cm or more |
| warning | severe weather or flood warning, or floodwater of 10 cm or more |
| watch | a weather watch, pressure falling 3 hPa or more in 3 hours, wind of 17 m/s or more, or rain of 30 mm/h or more |
| normal | none of the above |

Local wind alone never establishes a tornado: it can only raise a watch.

## Fault codes and alarms the unit reports

| Code | What the unit measured | Likely cause |
|---|---|---|
| `FEEDER_BLOCKED` | feeder on but dispensing nothing for 5 minutes | blocked feeder |
| `FEED_EMPTY` (alarm) | feed storage empty | feed not refilled |
| `WATER_PUMP_FAULT` | mains power and water in the tank, but drinking-water pressure under 50 kPa for 30 s | failed water pump |
| `WATER_SUPPLY_LOST` (alarm) | farm water main under 50 kPa for 30 s | farm-wide water cut |
| `TANK_FILL_FAULT` | refill on and the main has pressure, but no inflow for 60 s | failed tank fill valve |
| `WATER_LEAK` (alarm) | the leak sensor detects water | leak |
| `MAINS_POWER_FAILURE` (alarm) | mains power not normal | power outage |
| `BACKUP_BATTERY_LOW` (alarm) | on battery with under 20 % charge | long outage |
| `WATER_INTRUSION` (alarm) | water entering the barn | flood |
| `SMOKE_DETECTOR_FAILED` | the smoke detector gives no reading | failed detector |

## Water: who leads?

When the farm water main fails, the incubators' reservoirs stop refilling and the drinking-water tank stops filling at
the same time. Animal Protection measures the main's pressure, so it leads the incident (WATER_SUPPLY_LOSS); the
incubators join it. A fault on only one incubator's hose is that incubator's own problem.

## Approved protective actions

`deploy_shutters`, `deploy_flood_barrier` and `activate_alarm`, in DANGER only: see `safety_actions.md`.
