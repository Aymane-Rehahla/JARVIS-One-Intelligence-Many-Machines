# tests/llm/retrieval/

The retrieval checks prove which approved passage keyword search, vector search and hybrid reciprocal-rank fusion return.
`test_documents_bge.py` uses the real bge-small CPU embedder against a temporary approved copy of the HVAC manual.

- **In:** a question and a fixture-approved ROM copy; only its temporary SQLite index is written.
- **Out:** passage ranks and measured index/query durations. Recorded run: “the HVAC is struggling” returned a passage
  containing “compressor” and not “struggling”; keyword rank 3, vector rank 1, hybrid rank 2; build 0.505 s, query
  0.00594 s (`logs/retrieval_hvac.json`).
- **Test:** `.venv\Scripts\python -m pytest -q -p no:cacheprovider -o faulthandler_timeout=120 tests\llm\retrieval\test_documents_bge.py`; it skips only when model files are missing.
