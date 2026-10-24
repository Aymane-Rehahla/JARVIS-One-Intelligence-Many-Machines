# _dev/: development-only material

`farm_poc_snapshot/` is a **read-only** copy of the farm simulation's code, taken on 23 Oct 2026 while the Phase 5
training runs. It is the test feed for steps 6A–6C. Nobody edits it, with one exception made on the owner's instruction:
the project's old name was removed from it (docstrings, an API title, default paths and the random-seed labels; see
decision 0010). `__pycache__` folders were left out. Its file hashes are in `WORKLOG.md`.

It is **not** the code that will run the trained brains: in 6D, `matrix/` and each node's `reasoning/` and `memory/` are
copied from the training run's code snapshot instead.
