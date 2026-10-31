+++
node = "all"
title = "Contacts"
kind = "shared"
version = 1
status = "approved"
sources = [
  { title = "Farm simulation model: simulated phone service", link = "../../../../_dev/farm_poc_snapshot/matrix/communications.py" },
  { title = "Safety gate: who may call whom", link = "../../../../_dev/farm_poc_snapshot/nodes/gate.py" },
]
+++

# Contacts

*Derived from the farm simulation model, reviewed by the owner.* **Every contact here is simulated:** no real person or
service is ever called. Calls and notifications run on the farm's simulated time.

| Contact | Reached by | How quickly | Notes |
|---|---|---|---|
| The owner | notification from JARVIS | answers in 1 to 5 minutes by day, 5 to 30 minutes at night | in about 10 % of simulated runs the owner can't be reached; notifications about the same incident within 30 minutes are merged |
| Fire brigade | a call from the incident lead (Animal Protection or the HVAC) | about 1 minute of call handling, then 12 to 20 minutes to arrive | on arrival it puts out an active fire; a call without a fire is logged as a false alarm |
| Civil protection | a call from Animal Protection | 30 to 60 minutes to arrive | pumps a flooded barn; a call without a flood or storm emergency is a false alarm |

- Duplicate calls about the same incident within 2 hours are merged, the way a dispatcher would, and the caller is told
  who reported it first.
- Only the reasoning, through the safety gate, can place a call (see `owner_rules.md` and `safety_actions.md`). The
  language model can't call anyone.
- A caller sees the status of its own calls (received, dispatched, on scene, cleared or false alarm). The nodes share
  these statuses with each other, so they know when help is already on the way.
