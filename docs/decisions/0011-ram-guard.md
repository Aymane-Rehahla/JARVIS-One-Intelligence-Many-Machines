# 0011: The RAM guard while the training runs

Status: accepted (owner's instruction, plan v3, point 6); revised 24 Oct after the second and third reviews. The owner
paused the training on 24 Oct from 01:15:49 to 04:55:20 and the limits below were lifted for that time; they apply
again (plan v3.1). The monitor itself was not running from 02:01:53 to 04:33:39 on 24 Oct: the PC restarted at 02:02
and was off from 02:54 to 03:42, and the monitor does not start at boot (WORKLOG).

## Context
The Phase 5 training uses most of the 16 GB of RAM. The resource monitor samples it every 10 s. Between 23 Oct 04:35:52
and 24 Oct 01:15:43 (local), when the training paused, it took 5,636 samples, covering about 15.6 of those 20.7 hours:
it was not running on 23 Oct from 04:57:12 to 09:57:52, while the project moved to `JARVIS/`, and was restarted with
the new code at 09:57. In the samples, free RAM swung by gigabytes within seconds, from the training alone:
- the lowest free RAM was 0.03 GiB (23 Oct 16:50:07);
- 881 samples (16 %) were below 2 GiB, but never two in a row: every dip lasted less than one sampling interval;
- the lowest commit headroom (what Windows can still promise before allocations fail) was 1.98 GiB (23 Oct 19:34:49).
**When the training restarts or changes phase it is much harsher.** Right after its restart on 24 Oct at 04:55:20 the
training alone took the commit headroom to 0.00 GiB (04:56:24 and 04:58:06) and kept free RAM below 2 GiB for up to
53 s; the guard's conditions were met 16 times between 04:56 and 05:13, and it stopped nothing because nothing of ours
was registered. Since 06:00 the headroom has stayed at 5.0 GiB or more. Our own programs must not start during such
phase changes.
The owner's rule (v3, point 6): the resource monitor stops anything we started when free RAM drops below 2 GB.

## Decision
- **Never more than one LLM server while the training runs:** Qwen3-0.6B only, for plumbing tests. The 2B server runs
  only in short tests, when free RAM is above 3 GB.
- **The guard stops only programs we started that registered themselves** in `logs/guarded.json`:
  - the launcher, the LLM test helpers and the voice tests register; a Python program registers itself with its own
    process id, because the venv's `python.exe` is only a launcher for the real interpreter;
  - only programs whose image lies under `JARVIS/` or the venv's base Python may register (`PermissionError`
    otherwise), so nothing can ask the guard to stop another program;
  - each entry records the process's creation time and image path. Before stopping, the guard opens **one** handle to
    the process and checks both through that same handle, so a reused process id, or an entry forged to point at
    another program, is never stopped;
  - a lock file (`guarded.json.lock`) serialises registrations and stops.
- **When it acts:** free RAM has stayed below 2 GiB for 30 s (time-based, not a sample count), or the commit headroom
  falls below 1 GiB. While anything is registered the monitor samples every 2 s instead of every 10 s, so a squeeze is
  seen within seconds.
- The training's processes are never registered, so the guard can never touch them.
- **One test or build at a time** while the training runs. Parallel helpers may write code, docs and reviews.

## Alternatives
- **Stop on any single sample below 2 GB** (the owner's wording read literally): the measurements show 881 such samples
  in the 15.6 hours sampled, all from the training's own brief dips; the guard would stop our programs many times an
  hour without any lasting memory squeeze. Kept as an owner decision (plan section 12, point 0): the guard acts when free RAM
  *stays* below 2 GiB for 30 s, and the commit-headroom limit catches a real squeeze at once.
- **Stop by process name (`python.exe`):** could hit the training. Rejected outright.

## Consequences
- A sustained memory squeeze ends our test programs, never the training. A stop is logged as an "action" record.
- Free RAM *staying* below 2 GB is also a stop-and-report condition for the build itself (plan v3, run rules).
- Child processes: the 6A supervisor registers itself and runs every program in its own Windows job object
  (`launcher/winjob.py`), so the guard stopping the supervisor ends every program it started, and everything those
  started.
- The monitor does not start at boot: after a restart of the PC it must be started again (it was not, from 02:01 to
  04:33 on 24 Oct). `launcher/start_all.ps1` should start it in step 6F.
