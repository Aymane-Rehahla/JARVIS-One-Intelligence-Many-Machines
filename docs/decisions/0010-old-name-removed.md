# 0010: The old project name is removed everywhere, copied code included

Status: accepted (owner's instruction, plan v3, point 2).

## Context
The project's old name appeared in file contents, paths, the website, the plan and the code copied from the earlier
project folders. The owner wants it gone from everything inside `JARVIS/`.

## Decision
- `tests/test_no_old_name.py` fails if the old name appears, in any capitalisation, in any file or folder name, any
  file's content (logs, `.venv` and `node_modules` included), or the git history.
  - The only files whose content it does not read are model weights of 50 MB or more. They are byte-identical to their
    upstream releases (`models/MANIFEST.json`), and a second test proves every such file is a listed model.
  - Copying from the old folders is allowed; naming them is not.
- **In the read-only farm snapshot (`_dev/farm_poc_snapshot/`)**, the name was replaced in docstrings, the API title and
  default output paths, and in the **random-seed labels** (for example `"farm-comms-{seed}"`).
- The same replacement is applied when the trained brains' code is copied in step 6D.

## Consequences
- Changing a seed label changes which random numbers a given seed produces. Distributions, physics, encoders and labels
  are unchanged; only the particular noise draws differ.
  - So the same seed now gives a different, equally valid farm run than the training-era code.
  - The brains were examined on unseen seeds, so this doesn't affect them.
  - Exact episode-for-episode reproduction of the training-era runs now needs the training folder itself, outside JARVIS.
- The website's saved demo state moves to new storage keys (`jarvis.demo.v3`). A browser's old saved state is simply
  not read.
- The content scan reads about 1 GB in about 50,000 files. To keep `pytest` fast, it remembers which files it has
  already checked (by path, size and modification time) in a cache that git ignores.
