# matrix/: the simulated farm, JARVIS's test bench (copied in step 6D)

The Matrix: four machines (two incubators, the HVAC, animal protection), their built-in controllers, the building's
physics, weather, fire, flood, water and power. JARVIS sees only public telemetry (ground rule 4).

- **Status: empty until 6D.** Then the farm is copied from the training run's code snapshot, so it matches the code the
  brains were trained with; the old project name is removed from it (random-seed labels included, decision 0010) and
  the four demo-fault routes are added without changing any physics, encoders or labels.
- **Until then** the test feed is `_dev/farm_poc_snapshot/`, a read-only copy of today's farm.
- `farm_runtime.py` (6A, test feed; final in 6D) ticks the farm and the four reasoning brains in one loop and publishes
  `farm.<node>.status | events | assessment | incident`, `farm.links` and `farm.sim` on NATS.
- **Tests:** in 6D (`tests/DEFERRED.md`); E9 checks that nothing hidden leaks to a node.
