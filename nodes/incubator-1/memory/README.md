# memory/: the CMM and the node's knowledge

The reasoning's memory: working memory (RAM), short-term episodes, long-term facts, hypotheses kept apart from facts,
the normal profile by hour, and a nightly "sleep" that consolidates. SQLite, one database per node (`data/cmm.sqlite`).
`working.py` and `cmm.py` arrive in step 6D from the training run's code snapshot. **The LLM never opens the CMM**
(ground rule 2): neither `cmm.sqlite` nor working memory.

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

`rom/` differs between nodes (with `node.toml`, the node's own `README.md` and `data/`), so `tools/sync_nodes.py`
never touches it.

- **In:** every tick's readings, assessment and events from the reasoning (from 6D); the owner's approved documents.
- **Out:** memory context for the reasoning; the approved documents for the LLM's search, nothing else.
- **Tests:** `tests/test_knowledge.py` (headers, sources, approval), `tests/test_rom_quotes.py` (quotes word for word),
  `tests/test_rom_numbers.py` (every number matches the simulation), `tests/test_tree.py` (no code here before 6D).
