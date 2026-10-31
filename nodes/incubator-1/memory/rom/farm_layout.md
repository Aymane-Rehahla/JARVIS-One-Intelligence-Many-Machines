+++
node = "all"
title = "Farm layout"
kind = "shared"
version = 1
status = "approved"
sources = [
  { title = "Farm simulation model: physical world (zones, air paths)", link = "../../../../_dev/farm_poc_snapshot/matrix/physical_world.py" },
  { title = "Farm simulation model: equipment faults (water main, power)", link = "../../../../_dev/farm_poc_snapshot/matrix/equipment_faults.py" },
  { title = "Farm simulation model: devices", link = "../../../../_dev/farm_poc_snapshot/devices/" },
]
+++

# Farm layout

*Derived from the farm simulation model, reviewed by the owner.* The farm is JARVIS's test bench: four machines, each
with its own node, in one building.

## The building

| Zone | Floor | Height | Openings | What is there |
|---|---|---|---|---|
| Barn | 12.5 m × 10 m | 4 m | windows (1 m²), vents (0.5 m²), gates | the animals, their feeders and drinkers; Animal Protection's sensors and protective equipment |
| Storage | 7.5 m × 8 m | 3 m | no windows | feed and supplies |
| Workspace | 6 m × 5.5 m | 3 m | a window (0.5 m²) | the two incubators |

The three zones are joined by internal doors, so heat and smoke can move between them. The central HVAC serves all three
zones; outdoor air enters through its economizer and through the barn's windows and vents.

## The machines and their nodes

| Node | Machine | Where | What it watches |
|---|---|---|---|
| incubator-1 | incubator (24 eggs, 0.5 m³ cabinet) | workspace | its cabinet, its water hose, the workspace air outside it; **talks to the owner for all of JARVIS** |
| incubator-2 | incubator, same model | workspace | the same, for its own cabinet |
| hvac | central air handler | serves barn, storage, workspace | the three zones' temperature and humidity, supply and return air, cooling, fans, filter |
| animal-protection | animal protection unit | barn | barn air (CO₂, O₂, ammonia, CO, smoke), feed, drinking water, weather and warnings, windows, vents, shutters, flood barrier |

## Shared supplies

- **Water:** one farm water main (about 300 kPa) feeds both incubators' reservoirs, each through its own hose and float
  valve, and the barn's 1,000 L drinking-water tank. Animal Protection measures the main's pressure.
- **Power:** one 230 V, 50 Hz mains supply. The incubators and Animal Protection have backup batteries.
- **Outside:** a weather station and an authorised warning feed, both read by Animal Protection.

Because the machines share air, water and power, one problem often shows up on several nodes at once; JARVIS links them
to find the cause.
