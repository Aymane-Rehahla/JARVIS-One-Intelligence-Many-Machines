# 0005: Step 6.0 storage: hard links for the pre-downloaded models, caches on F:

Status: accepted (step 6.0, 23 Oct 2026; the plan's "copy, don't move" kept in spirit).

## Context
C: had 9.7 GB free at the first check (14.0 GB later). The training uses about 8 GB of the 16 GB of RAM, and the
pagefile lives on C:. Copying the pre-downloaded models (2.85 GB), plus download archives and the pip and npm caches,
would have left C: dangerously low.

## Decision
- **Hard links:** the two GGUF files and the six bge-small files in `models/` are hard links to
  `Desktop\jarvis 0.3.4\predownloaded assets`. That is the same data under two names: the originals stay untouched,
  and no extra space is used.
- **Caches on F::** download archives, the pip cache and the npm cache live in `F:\JARVIS-cache`.
- **FunASR, torch, torchaudio** (about 2 GB installed) wait until emotion2vec+ (1.12 GB) is approved. A dry run checked
  they install on Python 3.12.

## Alternatives
- **Plain copies:** 2.85 GB more on C:.
- **Junctions to F::** a slower disk and more moving parts.
- **Moving the originals:** the plan says copy, don't move.

## Consequences
- Editing one name of a hard-linked file would change both. Model files are never edited, and `MANIFEST.json`
  (`tools/hash_models.py check`) catches any change.
- Deleting the pre-downloaded folder later keeps the project's files intact.
