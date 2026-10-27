# tests/llm/integration/

The 6B H1 check runs the rule-brain FarmRuntime and three in-process nodes, then hot-joins animal-protection with a
fixture-approved manual. It verifies the speaker learns the new node's hazard topic and document metadata, asks it a
question through the audited tool registry, and checks the matching question/answer audit IDs. The LLM-worded answer
comes in 6C.

- **In:** private NATS, public farm messages, node configs and copied manuals; approvals and node data live only under
  pytest `tmp_path`.
- **Out:** measured hot-join and peer-answer durations, farm-time-bearing tool data, and paired audit records.
- **Test:** `.venv\Scripts\python -m pytest -q -s -p no:cacheprovider -o faulthandler_timeout=120 tests\llm\integration\test_hot_join_tools.py`
  (the live D5 supervisor scenario is in `tests/test_system.py`).
