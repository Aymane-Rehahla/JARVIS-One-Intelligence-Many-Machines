+++
node = "all"
title = "Safety list: approved protective actions"
kind = "shared"
version = 1
status = "approved"
sources = [
  { title = "Reasoning memory: safety_actions text (quoted word for word)", link = "../../../../_dev/farm_poc_snapshot/nodes/cmm.py" },
  { title = "Approved actions per machine: DANGER_ACTIONS", link = "../../../../_dev/farm_poc_snapshot/nodes/contracts.py" },
  { title = "Safety gate: evidence required per action", link = "../../../../_dev/farm_poc_snapshot/nodes/gate.py" },
  { title = "Evidence rules: fire signs, gas, flood, wind", link = "../../../../_dev/farm_poc_snapshot/nodes/evidence.py" },
]
+++

# Safety list: approved protective actions

*Copied word for word from the reasoning's code, with the evidence the safety gate requires.* Only the owner edits this
file. Each machine's node may act only on its own machine.

## The approved actions, as the reasoning holds them

> Incubator: Approved protective actions (DANGER only, own machine only): emergency_stop_heater
>
> HVAC: Approved protective actions (DANGER only, own machine only): fire_shutdown, emergency_ventilation
>
> Animal Protection: Approved protective actions (DANGER only, own machine only): deploy_shutters, deploy_flood_barrier, close_gates, activate_alarm

## The evidence the safety gate requires

| Machine | Action | Command sent | Evidence required |
|---|---|---|---|
| Incubator | emergency_stop_heater | `emergency_stop`: the heater contactor opens; fans and controller stay on | cabinet at 40 °C or more, or heater outlet at 70 °C or more, **and** the heater still drawing current with the relay stuck or the high-limit thermostat tripped |
| HVAC | fire_shutdown | `set_mode fire_shutdown`: fans, outdoor air, dampers, compressor and heating off until reset | at least 2 independent fire signs |
| HVAC | emergency_ventilation | `emergency_ventilation` | dangerous barn gas **and** no fire sign at all |
| Animal Protection | deploy_shutters | `deploy_shutters` | a tornado warning from the warning feed, or wind of 33 m/s or more (hurricane force) measured on site |
| Animal Protection | deploy_flood_barrier | `deploy_flood_barrier` | a flood warning, floodwater of 5 cm or more, or water entering the barn |
| Animal Protection | activate_alarm | `activate_alarm` | at least 2 independent fire signs, or dangerous gas |
| Animal Protection | close_gates | none | listed as an action, but the safety gate has no command for it, so it is never sent |

**Fire signs** come in four families:
- the smoke detector;
- CO at 35 ppm or more;
- room heat: a zone at 45 °C or more, or a sensor that died after reading hot;
- duct heat: return air at 40 °C or more, or supply air at 35 °C or more with airflow above 100 m³/h while the heater is off.

**Dangerous gas** in the barn means CO₂ at 3,000 ppm or more, ammonia at 25 ppm or more, O₂ below 19.5 %, or CO at
100 ppm or more.

## Rules for every action

- Only the reasoning proposes an action, and only the safety gate (`safety/gate.py`) sends it. The language model never
  acts on a machine and never calls emergency services.
- Own machine only, DANGER only, and only with the evidence in the table. The gate checks that evidence in code.
  Fire is never acted on from a single sign: it needs at least 2 independent fire signs.
- A 10-minute cooldown per action, and no retry of an action the machine refused.
- Every command is checked through the machine's command lifecycle (accepted, executed, failed or timed out).
