# PROGRESS: the one builder's running log (Phase 6, 30 Oct - 1 Nov 2026)

Re-read this file and `docs/handoff/README.md` section 4 after any context summary, before continuing.
Never write the three outside paths (the release folder, the training's Python, the upstream CTM folder) here.

## Done

| Step | Commit / tag | Evidence |
|---|---|---|
| Step 0: redesigned website swapped in | (this commit) | WORKLOG.md, "Step 0" |

## In progress

- P1 (6W part 1): demo faults in the farm runtime, gateway run-following, `live.ts` + `useFarm()`.

## Next

P1 → P2 core → P4 chat/ack/alerts → P3 → rest of P2 → P4 voice → run_all → 6F (README section 3).

## Open questions / owner actions

- The owner alone: approve the 20 ROM documents; rate the persona; record the backup video.
- The hand-in must carry `interface/web/public/assets/farm/` (3D models, about 277 MB, out of git; see
  `interface/web/assets-manifest.json`).
- On 30 Oct 14:57 another session's vite dev server (on the X: copy) held port 5173; it was not started by this
  builder and was left alone. It must be stopped before the cold-start rehearsals (the launcher needs 5173).
