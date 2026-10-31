# 0023: The calm farm, real-time chat and the fault menu

Status: accepted (owner's demand, 31 Oct 2026).

## Context
On a normal library day the trained brains escalate by themselves (measured: 20 owner alerts in about 25 farm
minutes at 10×, after a reset with nothing injected). Each became a spoken alert, which kept the voice busy and buried
the chat. The owner asked for a farm that stays calm until a user breaks something, chat only in real time, and a
fault menu: incubator, HVAC, animal protection and disaster faults, five each, plus a missing peer for each machine.

## Decision
- **Calm farm.** `SimState.alerts_for` names the machines whose escalations may reach the owner. It is empty on a
  normal day. An injected fault adds the machines it touches. A missing peer adds the other three, which notice the
  silence. A reset empties it. The brains still assess every tick, and their conclusions still go to the feed, the
  events page and the device pages; only the owner alert (the template and the voice) is held. The node learns *that*
  a machine is in a user's test, never which fault: the fault's name stays off the wire (the 120-tick wall test still
  passes for all 20 faults).
- **Fault menu.** There are 20 `ScenarioId`s, five per kind. Each is the first ordinary library variant matching its
  description, as decision 0020 sets out, with the library's starting state applied (a low hopper, or a tank near its
  refill level; without it, `feeder_blocked` and `tank_fill_valve_failure` never act). The Matrix has no hurricane.
  "Hurricane" is its strongest windstorm, a warned tornado strike. A missing peer is the Matrix's radio cut for a farm
  day (`POST /links/{node}/cut` on the gateway), so it lasts until the next reset.
- **Real-time chat.** When the speed is above 1×, the website closes the chat box and the voice panel. The farm and
  its brains keep running and detecting.

## Consequences
- A brain naming what it detected (for example "fire" as its incident kind) is detection, not a leak; the wall test
  excuses only the kind that the brain itself concluded, and only in its own assessment or incident.
- False escalations from the brains on a calm day are still measured on the feed; they are no longer spoken.
