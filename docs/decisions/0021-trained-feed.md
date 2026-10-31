# 0021: The trained feed: an ordinary library day, one torch thread, a fresh CMM per run

Status: accepted (builder, 31 Oct 2026, step 6D; P3 section 5). One question is open for the owner (below).

## Context
Decision 0015 installs release v3.1 inside JARVIS (`matrix/release_v31/`, `tools/install_release.py`). The farm
runtime must run those brains as the release's exam did, publish them as "live: trained brains", and keep every
Matrix API route working.

## Decision
- **`--feed trained|simulated`** on `matrix/farm_runtime.py` (`start_all.ps1 -Feed`, the supervisor's `--feed`). The
  launcher's default is `trained`; the code's default stays `simulated` so the fast tests keep the 6A snapshot.
- **The farm:** the release's own Matrix code with the config of an ordinary library day: family `normal`, **variant
  1** (training split, weather profile "mild", weather start hour 6.87, so the farm starts at 06:52), the runtime's own
  seed (default 7). The brains get that start hour; measured: it equals the engine's hour of day at tick 0.
- **The brains:** as `ctm_training.practice.CheckpointBrains` built them for the exam: each engine loaded once
  (`load_brain`, CPU), a deep copy per node (each incubator its own), the tuned decision layer (`decide.json`),
  `hide_notes=True`, `hold_calls=True`; `NodeRuntime(engine, modes all "real", brains, memory_factory)` as in the
  release README.
- **The CMM:** `CognitiveMemory` per node in `matrix/data/cmm/<feed_id>/<node>.sqlite` (gitignored), closed and deleted
  at the next reset or when the program stops.
- **Threads: 1.** Measured on this PC (300 ticks after 20 warm-up ticks): 28.2 ticks/s with 1 thread, 30.1 with 2,
  30.8 with 4. More threads gain 7-9 %, the equivalence was proven with 1, and the node and language-model programs
  share the CPU, so 1 stays.
- **Speed:** about 35 ms a farm tick, so at most about 28x real time. 1x, 10x and 30x are reached; 60x and 120x
  asked run at about 28x (the website shows asked and reached).

## The equivalence, and the one open question
`tools/release_equivalence.py` ran the exam's harness on one ordinary episode per demo-fault family (heater relay
stuck v9, supply fan failure v1, sensor fault v228, overvoltage v1; all 8,923 ticks, every node) with the training's
Python on the release and with `.venv` on the installed copy. **They differ from tick 0** (incubator-1's confidence
0.40775 vs 0.410387, same assessment). Isolated (31 Oct): the interpreter makes no difference (the release's code gives
identical results on Python 3.12 and 3.14); the re-saved weights make no difference (the release's code with the
installed weights gives the release's numbers). The cause is the word map: the Matrix code seeds five random streams
with strings that begin with the project's old name (the phone service, the sensors, the weather, the peer bus, the
episode id), so the renamed copy draws different random numbers: a different but equally ordinary farm, as a new seed
would give. **With those five seed strings mapped back at run time only** (`--restore-salts`, from `name_guard.OLD`,
no file changed), **the copy matches the release exactly: 0 differences.** The copy is the same brain and the same
logic.

**Question for the owner:** should the trained feed keep the renamed seed strings (the default now: the old name is
in no file and nowhere at run time; the farm's random draws differ from the training's worlds, as any fresh seed's
do), or restore the original seed strings at run time (the training's exact random worlds, with the old name assembled
in memory from `name_guard.OLD`, as the equivalence diagnosis does)?

## Alternatives
- Keeping the release's code unrenamed: rejected by decision 0010.
- More torch threads: see above.

## Consequences
- The trained feed runs about 28x at most on this PC.
- Known open issues of the release (its README), seen again here: animal protection raises false alerts on a calm farm
  within the first 15 farm seconds; the incubator's own response to its stuck heater (the incubator escalates, but in
  300 ticks only animal protection notified the owner, about incubator-1's incident).
