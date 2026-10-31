# memory/: the CMM and the node's knowledge

The reasoning's memory (CMM): working memory (RAM), short-term episodes, long-term facts, hypotheses kept apart from
facts, the normal profile by hour, and a nightly "sleep" that consolidates. **It runs beside the brain in the farm
runtime** (decisions 0012, 0015, 0021): `farm_poc/nodes/cmm.py` of the installed release (`matrix/release_v31/`), one
SQLite file per node in a fresh folder for every farm run (`matrix/data/cmm/<feed_id>/<node>.sqlite`, deleted at the
next reset). It records what the brain concluded and what the machine reported; the trained brain does not read it
(its notes are hidden, as in training). No CMM code is copied here. **The LLM never opens the CMM** (ground rule 2).

## rom/: the node's knowledge, approved by the owner

`rom/` is read-only knowledge: the reasoning reads the owner rules and the safety list; the LLM's document search
(`llm/documents.py`, step 6B) indexes the documents to cite them. No node ever writes to `rom/`.

| Document | Kind | Content |
|---|---|---|
| the machine manual (`incubator_manual.md`, `hvac_manual.md` or `animal_protection_manual.md`) | machine manual | derived from the farm simulation model; **the authority on every number** |
| a domain guide (written after the owner approves its public sources) | domain guide | general knowledge in our own words, citing university extension, FAO and government guidance |
| `owner_rules.md`, `safety_actions.md`, `farm_layout.md`, `contacts.md` | shared | identical in all four nodes |
| `approvals.json` | record | written by `tools/knowledge.py approve`: the SHA-256 of each document the owner approved |

Every document starts with a TOML header (`+++`): node, title, kind, version, `status = "draft" | "approved"` and its
sources with links. **Only owner-approved, unchanged documents are indexed**; any change sends a document back to draft
(`tools/knowledge.py check --fix`). When a guide disagrees with the machine manual, the manual wins and the conflict is
noted in the guide.

The LLM document index is separate from the CMM. `llm/documents.py` reads only `memory/rom/*.md` and
`memory/rom/approvals.json`; it writes the keyword and vector index to `data/docs_index.sqlite`. It never reads or opens
`data/cmm.sqlite`, working memory, or the reasoning's private state. `data/llm.sqlite` stores the node's events, audit
records, and (only on incubator-1) owner conversations and turns.

`rom/` differs between nodes (with `node.toml`, the node's own `README.md` and `data/`), so `tools/sync_nodes.py`
never touches it.

- **In:** the owner's approved ROM documents for search; public farm events and audit entries for the separate LLM
  store. **Out:** cited ROM passages and node-local LLM records; no CMM data crosses into the LLM.
- **Tests:** `tests/test_knowledge.py` (headers, sources, approval), `tests/test_rom_quotes.py` (quotes word for word),
  `tests/test_rom_numbers.py` (every number matches the simulation), `tests/llm/test_documents.py` and
  `tests/llm/test_conversation.py` (temporary indexes and stores), `tests/test_tree.py` (no CMM code in the node folders), `tests/test_farm_trained.py` (fresh CMM files per run).
