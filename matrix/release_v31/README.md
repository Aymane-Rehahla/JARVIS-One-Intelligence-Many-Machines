# matrix/release_v31: the trained brains of release v3.1, installed inside JARVIS

Written by `tools/install_release.py` (P3 step 3, decision 0015). **Never edit anything here by hand**: run the
installer again on the release folder instead; it gives byte-identical files.

- `ctm_training/`, `farm_poc/`: the exact code that trained the brains (the release's `code_snapshot/`), only the
  modules the brains need (found by `tools/release_probe.py`).
- `base ctm/continuous-thought-machines-main/`: the upstream Continuous Thought Machine library modules the brains
  import, with its LICENSE, at the path `ctm_training/model.py` expects.
- `brains/<machine>/`: each reasoning engine re-saved as plain weights (`config` and `state_dict` only, every tensor
  proven equal), with its `decide.json` and `stats.npz` copied byte for byte.
- The project's old name is replaced by the word `testbed` (same case) everywhere in this code; the reverse map gives
  the release's files byte for byte. `PROVENANCE.json` lists every file with both SHA-256s and how it was installed.

The farm runtime's trained feed (`matrix/farm_runtime.py --feed trained`, decision 0021) runs these brains exactly as
the release's exam did. This folder is excluded from pyright and coverage (frozen code) but not from the old-name scan.
