---
noteId: "58613260e9ea11f185048d61977aa200"
tags: []

---

# OpenHands repository tools evaluation

November 26, 2026. The OpenHands SDK called the running LiteLLM and llama.cpp stack with gpt-oss-20b Q4_K_M at the configured 100k context. Each variant used the same model and a six-step budget. The baseline had terminal, file editing, and memory. The upgraded variant added repository tools and their instructions. Each task ran once per variant.

| Read-only task | Baseline | Upgraded |
|---|---|---|
| Locate the script that configures the default LLM profile | Did not meet criteria | Parser error |
| Find a Python definition | Pass | Parser error |
| Locate a specific import | Did not meet criteria | Did not meet criteria |
| Identify the declared HTTPX constraint | Parser error | Parser error |
| Identify the memory collection | Did not meet criteria | Pass |
| Run a Git whitespace check | Pass | Pass |
| Run a tracked Python syntax check | Pass | Parser error |
| Recognize an absent setting | Did not meet criteria | Did not meet criteria |
| Identify the embedding API service | Did not meet criteria | Parser error |
| Locate the owner container configuration | Pass | Did not meet criteria |

The baseline met the answer criteria on 4 of 10 tasks. The upgraded variant met them on 2 of 10. llama.cpp rejected malformed native tool output in one baseline task and five upgraded tasks. The upgraded agent completed the Git check in one tool call and returned its receipt. In a separate exploratory run with temperature 0.2 and medium reasoning, the baseline passed 2 of 10 and the upgraded setup passed 4 of 10. The runs used different sampling and tool revisions, and neither establishes a reduction in unsupported claims.

The live MCP checks passed for exact and hybrid search, source hashes, symbol lookup, dependency declarations, check receipts, stale edit rejection, and a successful edit in a temporary file. Additional live checks covered workspace memory isolation, legacy record adoption, expiry, changed code visibility, and versioned HTTPX release notes. The seven existing web URL tests passed.

Run the paired evaluation with `docker compose --profile openhands exec -T openhands python - < scripts/eval-openhands-repo.py`.

## Canvas repair verification

November 26, 2026. The repaired owner Canvas finished all ten repository tasks with its saved OpenHands settings and the running gpt-oss-20b Q4_K_M model at 100k context. The Compose answer was checked for invented mount entries. The edit and web checks passed in a separate run using the same configuration.

| Task | Result | Seconds |
|---|---|---:|
| Locate the owner default LLM profile script | Pass | 6.09 |
| Locate the Python definition | Pass | 1.25 |
| Locate the import | Pass | 5.29 |
| Identify the HTTPX constraint | Pass | 2.47 |
| Identify the memory collection | Pass | 1.67 |
| Run the Git whitespace check | Pass | 1.25 |
| Run the Python syntax check | Pass | 0.87 |
| Recognize the absent setting | Pass | 0.88 |
| Identify the embedding API service | Pass | 1.66 |
| Locate the owner container configuration and check quoted mounts | Pass | 2.88 |
| Read and edit a host-owned file, preserve ownership, and verify it with pwd and rg | Pass | 29.46 |
| Search and fetch the official ripgrep repository | Pass | 4.88 |

The owner Canvas and host terminal reported the same checkout and Git HEAD. Canvas executes Bash tools inside Docker. The edit and web runs recovered from malformed tool arguments before completing.

Reproduce the Canvas verification with `./scripts/verify-openhands-canvas.sh`.
