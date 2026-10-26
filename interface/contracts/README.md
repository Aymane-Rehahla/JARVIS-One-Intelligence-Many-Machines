# interface/contracts/: generated boundary schemas

These JSON Schema files are generated from the pydantic wire contracts in `nodes/incubator-1/contracts.py` and the
gateway's `FarmSnapshot`. They are inputs to the website's zod schema definitions; the generated files are outputs and
must not be edited by hand. `NodeConfig` remains a local node configuration, not a message exported to the website.

Regenerate after changing an exported pydantic contract:

```powershell
.venv\Scripts\python tools\export_contracts.py
.venv\Scripts\python tools\export_contracts.py --check
```

The first command writes every schema; `--check` reports a missing, stale or retired schema without changing files.
`tests/test_contracts.py` runs the check. `tests/test_contracts_zod.py` verifies that the website's zod schemas accept
and reject the same samples as pydantic.
