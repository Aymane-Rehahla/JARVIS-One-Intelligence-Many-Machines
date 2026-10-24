// Checks sample gateway messages against the website's zod schemas (plan Q2; used by tests/test_contracts_zod.py).
//
// Usage: node scripts/check-contracts.ts SAMPLES.json
// SAMPLES.json is a list of {"schema": "<name in SCHEMAS>", "message": <the JSON message>}. Prints one JSON line per
// sample, {"index": i, "schema": ..., "ok": true|false, "issues": [...]}, and exits 1 if any sample fails.
// Node 24 runs this TypeScript directly (type stripping), so it needs no build step.

import { readFileSync } from "node:fs"
import { SCHEMAS } from "../src/services/contracts.ts"

type Sample = { schema: string; message: unknown }

const path = process.argv[2]
if (!path) {
  console.error("usage: node scripts/check-contracts.ts SAMPLES.json")
  process.exit(2)
}

const samples = JSON.parse(readFileSync(path, "utf8")) as Sample[]
let failed = 0
samples.forEach((sample, index) => {
  const schema = (SCHEMAS as Record<string, (typeof SCHEMAS)[keyof typeof SCHEMAS]>)[sample.schema]
  if (!schema) {
    failed += 1
    console.log(JSON.stringify({ index, schema: sample.schema, ok: false, issues: ["unknown schema"] }))
    return
  }
  const result = schema.safeParse(sample.message)
  if (!result.success) failed += 1
  const issues = result.success ? [] : result.error.issues.map((issue) => `${issue.path.join(".")}: ${issue.message}`)
  console.log(JSON.stringify({ index, schema: sample.schema, ok: result.success, issues }))
})
process.exit(failed ? 1 : 0)
