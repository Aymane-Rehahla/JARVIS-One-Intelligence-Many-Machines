+++
node = "all"
title = "Owner rules"
kind = "shared"
version = 1
status = "approved"
sources = [
  { title = "Reasoning memory: OWNER_RULES (quoted word for word)", link = "../../../../_dev/farm_poc_snapshot/nodes/cmm.py" },
  { title = "Safety gate: notification and call limits", link = "../../../../_dev/farm_poc_snapshot/nodes/gate.py" },
  { title = "Evidence rules", link = "../../../../_dev/farm_poc_snapshot/nodes/evidence.py" },
]
+++

# Owner rules

*Copied word for word from the reasoning's code, with the limits the safety gate enforces.* Only the owner edits this
file; no node ever writes to it.

## The rules

> Escalate a problem the machine handled when it repeats 3 times in 24 h, is not recovered after 30 minutes, spreads
> to another node, or puts life at risk. Do not interfere when the machine handled it. Fire: call the fire brigade at
> once and tell the owner. Flood or storm: tell the owner first; call civil protection only if water enters the barn
> or there is a tornado strike or extreme wind and the owner has not answered within 5 minutes.

## How the safety gate applies them

- **Owner notifications** are merged per incident, and not repeated for the same node and diagnosis within 2 hours
  unless the priority rises.
- **Fire brigade:** only in DANGER with at least 2 independent fire signs. The incident lead calls: Animal Protection
  when it has first-hand smoke or CO, otherwise the HVAC. Another node calls only if the lead has stayed silent for
  60 s after the incident opened. Every call also notifies the owner, and the nodes learn from each other's messages
  that a call is already in progress.
- **Civil protection:** only in DANGER, when water is entering the barn, the floodwater is 30 cm or more, or the wind
  measured on site is 33 m/s or more, **and** only after the owner has left the notification unacknowledged for 5 minutes.
- **Who leads:** Animal Protection leads gas, flood, storm and farm-water incidents, because it measures the barn air,
  the weather and the water main's pressure. For a farm water cut it escalates and tells the owner; the incubators join
  its incident and watch. One dry hose on one incubator is that incubator's own fault, reported by that incubator.

## What these words mean

| Assessment | Meaning |
|---|---|
| NORMAL | nothing to report |
| HANDLED_BY_MACHINE | the machine's own controller dealt with it; remember it, do not interfere |
| WATCHING | something is developing; collect evidence, ask the other nodes |
| ESCALATE_TO_USER | the owner needs to know or decide |
| DANGER | waiting would cause harm: approved protective action on its own machine, owner told, authorities called when needed |
