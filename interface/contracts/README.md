# interface/contracts/: message schemas (step 6A)

JSON Schemas of every NATS, gateway and tool message, exported from the pydantic models (defined once, in Python). Every
message carries `schema_version`; unknown versions are logged and dropped (Q2). Generated files, never edited by hand.

- **In:** the pydantic models in Python. **Out:** JSON Schema files the website's zod checks are built from.
- **Tests:** a contract test runs the same sample messages through pydantic and zod (6A).
