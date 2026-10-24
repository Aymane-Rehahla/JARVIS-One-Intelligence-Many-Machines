# 0008: Knowledge is approved by the owner before it is indexed

Status: accepted (owner's instruction, plan v3, point 3).

## Context
Each node answers from documents in `memory/rom/`. What JARVIS cites must be correct, traceable and the owner's
choice, and it must never contradict the simulation the nodes supervise.

## Decision
- **Each node holds:**
  - its **machine manual**, derived from the simulation and the authority on every number;
  - a **domain guide** (incubators: incubation and hatching; HVAC: poultry-house ventilation and troubleshooting; animal
    protection: heat stress, fire, flood and storm procedures);
  - the **shared** owner rules, safety list, farm layout and contacts.
- **Domain guides come from free, citable public sources:** university agricultural extension services, FAO and
  government farm-disaster guidance. The owner approves the source list before any guide is written. Guides are written
  in our own words, never copied.
- **Every document has a TOML header:** node, title, kind, version, `status = "draft" | "approved"`, and its sources
  with links.
- **Approval belongs to the owner.** `tools/knowledge.py approve` records the document's SHA-256 in `rom/approvals.json`.
  Only approved, unchanged documents are indexed. Any change sends a document back to draft until the owner approves it
  again (`knowledge.py check --fix`).
- **Numbers are checked against the simulation by tests.** When a guide disagrees, the manual wins and the guide notes
  the conflict.

## Alternatives
- **Index everything in `rom/`:** fast, but unapproved or wrong text could be cited.
- **Approval by a flag alone:** a later edit would silently keep its approval. The recorded hash prevents that.

## Consequences
- Search (6B) indexes nothing until the owner approves documents.
- Writing the domain guides waits for the owner's approval of the sources (this step's guaranteed stop).
