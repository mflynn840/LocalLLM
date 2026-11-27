---
noteId: "e61764f0e56011f198e0e9ecd09d8dfe"
tags: []

---

# Local AI stack

This project serves local GGUF models through llama.cpp and LiteLLM, with Open WebUI for chat and OpenHands for coding. OpenCode is an optional terminal coding agent. Qdrant provides persistent vector storage, and a local BGE embedding model powers document retrieval and explicit long-term memory.

Local web search is enabled by default and reaches public search providers through SearXNG. Model inference and embeddings remain local. Use `--no-web-search` for disconnected sessions.

The project is intended to live at `/home/michael/AI/LocalLLM`. Start commands below assume you have changed into that directory.

## Requirements

The one-command installer targets **Linux with NVIDIA CUDA**, including Ubuntu in WSL2:

- Docker Engine with Docker Compose v2 and NVIDIA GPU support. For WSL2, install Docker Desktop, enable Ubuntu WSL integration, and ensure the NVIDIA GPU is visible to Docker.
- Python 3 and internet access for initial container, model, and MCP package downloads.
- Enough disk space for the GGUF and container images. Gemma 4 12B Q4_K_M is about 6.6 GiB; the local embedding model is about 65 MiB.

`install.sh` does not install Docker Desktop, GPU drivers, or the NVIDIA container runtime. If the Hugging Face `hf` CLI is missing, it installs `huggingface_hub` into this project’s `.venv`.

The default 100k context needs substantial system memory in addition to VRAM. It was tested on this machine with a 52 GB WSL memory limit and an RTX 5080 with 16 GB VRAM. On another machine, lower `CONTEXT_SIZE` in `.env` or pass `--context` to `start.sh` if the model fails to load or memory is insufficient.

## Install and start

Run installation while online:

```sh
./install.sh
```

The installer creates `.env` if needed, generates local LiteLLM, SearXNG, and web-loader secrets, pulls service images, builds local services, and downloads Gemma 4 12B Q4_K_M plus the BGE embedding model. It installs Browser MCP into a persistent OpenHands npm prefix through a temporary installer container. On WSL, it also installs Windows logon tasks for backend startup and LAN forwarding (with an administrator prompt). Existing non-placeholder secrets, models, OpenHands data, and Docker volumes are preserved. `.env` is kept private with mode `600`.

Choose a different startup model during installation; repeat `--model` to download several models sequentially. The first model becomes the persisted default:

```sh
./install.sh --model qwen3-coder-30b-a3b:Q3_K_M \
  --model qwen3.5-9b:Q4_K_M
```

Omit the quantization suffix to use the model catalog default. You can also set `DEFAULT_MODEL` and `DEFAULT_QUANT` in `.env`; `start.sh` uses those values when no model is passed.

Start backend mode (the default):

```sh
./start.sh
```

Backend mode starts CUDA + OpenHands + the model configured in `.env`, with local web search enabled. It runs the model server, PostgreSQL-backed LiteLLM gateway, Open WebUI, your owner OpenHands instance, and the user registration portal.

- **Open WebUI:** `http://localhost:3000` — individual chat accounts.
- **OpenHands:** `http://localhost:8000/canvas` — owner agent starts in the project and sees it at `/projects` and its original WSL path. The UI is ready to use with the configured local model and does not require a separate login; port 8000 is bound to localhost only.
- **Registration portal:** `${BACKEND_PUBLIC_URL}/register` — requires the shared registration code and creates personal model accounts.

Open WebUI requires login and allows signup. On a new install, Open WebUI creates the first admin from `WEBUI_ADMIN_EMAIL` and `WEBUI_ADMIN_PASSWORD`; existing no-auth installs keep their account and chat history, with its password set to the generated value. Keep `.env` private.

### Other users' PCs

The backend shares its model gateway and Open WebUI. Each person runs a separate OpenHands agent on their own PC, with their own files and conversations. Run `./start.sh --mode client` to start the portable deployment from `client/`, or distribute that folder. See [client/README.md](client/README.md).

1. Set a private `MODEL_SIGNUP_CODE` in backend `.env` and share it with the user.
2. After WSL starts, refresh the Windows port forwarding and LocalSubnet firewall rules:

   ```sh
   powershell.exe -ExecutionPolicy Bypass -File scripts/backend-lan.ps1
   ```

   This forwards Windows TCP 3000 and 4000 to WSL TCP 3001 and 4001, avoiding conflicts with Docker Desktop's published ports. It removes the old OpenHands TCP 8000 forwarding. Rerun after WSL restarts because its NAT IP can change.
   To start backend mode and refresh forwarding automatically at Windows sign-in, install the scheduled task once:

   ```powershell
   powershell.exe -ExecutionPolicy Bypass -File scripts/install-backend-autostart.ps1
   ```

   Windows will request administrator approval. One task starts `./start.sh` as your normal WSL user; a separate elevated task refreshes NAT forwarding. The start task waits for Docker Desktop and retries if it is still starting; its output is logged at `runtime/backend-boot.log`.
3. The user opens `http://<backend-windows-ip>:4000/register`, registers, follows the invitation, and creates a personal `local-model` key. Each key is limited to the local model and a $5 maximum budget per 30 days.
4. Users can create separate Open WebUI accounts at `http://<backend-windows-ip>:3000`. Client agents use the backend's SearXNG `search_web` and guarded `fetch_url` tools through the model gateway port.

Do not forward these ports from your router or expose them to the public internet. LAN traffic currently uses HTTP and does not encrypt credentials or model keys in transit; use a trusted local network only. Add TLS before using an untrusted network.

Backend owner OpenHands uses `LITELLM_MASTER_KEY` internally. Client instances use each user's personal virtual key; the master key is never distributed.

OpenCode has automatic compaction enabled in [config/opencode.json](./config/opencode.json). It starts compacting with a 15% context buffer (at least 16,000 tokens), so with a 100,000-token context it begins at about 84,000 tokens. The compacted conversation keeps roughly the latest 15,000 tokens alongside its summary. Compaction uses the selected local model through LiteLLM.

OpenHands provisions a selectable **Finish-and-Verify** agent profile in Settings > Agent. Use it for coding tasks when you want the agent to continue through implementation, a relevant smoke or dry run, unit tests, and a final review of every changed file. It does not replace or activate over your current profile. Startup refreshes its instructions while preserving the default profile's model and tool configuration.

### Supervised agent runs

The optional `agent-orchestrator` service runs a small planner and read-only reviewer around the selected OpenHands worker. It stores task plans and review decisions in `runtime/orchestration/`. The planner and reviewer use `ORCHESTRATOR_MODEL` through the local LiteLLM gateway (default `local-model`); the worker uses your selected OpenHands agent profile. Reviews run at step boundaries, and a revision is limited to a short follow-up instruction. The default cap is three review calls for the task.

Start the backend, then run a task from the project root:

```sh
docker compose --profile openhands run --rm agent-orchestrator \
  "Inspect and fix the retry behavior in the model queue" --mode docker
```

For a worker that needs your WSL user, shell, or files outside the mounted project, use `--mode wsl`. This needs the stack's `full-control` sandbox and the active OpenHands profile must include a terminal. Planner and reviewer stay inside the container network; only the worker gets WSL terminal access. Task JSON records are local runtime state and can be removed when no longer needed.

## Use the coding agent

Open WebUI is a chat interface; use OpenHands when you want an agent to inspect or edit files. In Docker mode, the owner agent sees only the project checkout at `/projects` and its original WSL path. The WSL home and filesystem root are not mounted, and the project's `.env` is masked inside the container. OpenHands runs as an unprivileged container user; its persistent settings remain in the state volume. Process count is limited to 512, and memory/CPU default to 16 GiB / 8 CPUs (`OPENHANDS_MEMORY_LIMIT` and `OPENHANDS_CPU_LIMIT` in `.env`).

The Canvas includes a LocalLLM picker for new conversations. It lists downloaded catalog models, shows unavailable models in gray with a download action and progress, and offers context and reasoning-effort settings when the selected model advertises them. Each conversation keeps its own model and context choice. The llama.cpp router keeps one model loaded and queues requests while another model is serving, so a model change in one conversation cannot interrupt another conversation's active generation.

In full-control mode, the Canvas execution selector can choose **Docker** or **My PC (WSL)** for each new conversation. My PC mode uses a private Unix-socket PTY bridge to run commands as your normal WSL user, from the same checkout path and with your shell startup files and installed tools. It can access any file your WSL user can access. The host bridge is available only when OpenHands full-control/networked mode is selected. Client mode mounts only `client/workspace`; it has no host filesystem mount or Docker socket. The owner UI binds to localhost.

### Sandbox levels

Select the OpenHands access mode at startup:

```sh
./start.sh --sandbox-level restricted
./start.sh --sandbox-level read-only
./start.sh --sandbox-level read-write
./start.sh --sandbox-level full-control
```

`project` and `networked` are kept as aliases for `read-write` and `full-control`
respectively for backward compatibility.

| Level | Project mount | OpenHands internet egress | Shell commands | Use |
|---|---|---|---|---|
| `restricted` | Read-only | Blocked | Disabled | Inspect and plan only; most locked-down container (read-only root filesystem) |
| `read-only` | Read-only | Blocked | Disabled | Read files, explain, and plan without modifying project files |
| `read-write` | Read/write | Blocked | Disabled | Normal coding: read and edit project files, no internet |
| `full-control` (default) | Read/write | Allowed | Enabled | Read, write, and run shell commands; internet access for package downloads |

The setting is applied by recreating the OpenHands service as needed. `full-control` gives the
Docker agent project write access, outbound access, and shell execution. Selecting My PC (WSL)
also runs commands with the WSL user's full filesystem permissions. Review changes and use this
mode only for conversations that need host tools or files. OpenCode runs as a host CLI and is
**not** restricted by these OpenHands sandbox settings.

To change the setting permanently, edit `OPENHANDS_SANDBOX_LEVEL` in `.env` (or pass
`--sandbox-level` on each `./start.sh` run), then restart OpenHands:

```sh
./start.sh --agent openhands --sandbox-level full-control
```

### Command approval

When using a mode that allows shell commands (`full-control`), you can require
OpenHands to ask for approval before executing each command. Edit `.env`:

```sh
OPENHANDS_REQUIRE_APPROVAL=true
```

Then restart OpenHands. With approval required, OpenHands will present each proposed
shell command for your review before running it. This works independently of the
sandbox level — combine it with any mode that permits commands. The default is
`OPENHANDS_REQUIRE_APPROVAL=false`.

## Persistent memory and document RAG

Qdrant stores data in a persistent Docker volume. The local BGE-small model creates 384-dimensional embeddings without sending content to a remote service. Open WebUI document retrieval and explicit memory tools use this Qdrant server, with separate collections.

OpenHands durable memory uses the `openhands_memories` Qdrant collection. The memory service binds reads, writes, and deletes to the configured `WORKSPACE_DIR`. Scope names group items inside that workspace. Save durable decisions and preferences explicitly. Repo facts require a workspace-relative source path and SHA-256, and search skips them if the source changes. Give task checkpoints a named scope, then query that scope when resuming. Saving a new checkpoint replaces the previous one for that task, and checkpoints expire from search after seven days. Reuse a stable `memory_key` in the same kind and scope to update a durable record.

Older records stay in Qdrant until reviewed. Use the following commands to review them and adopt only IDs that belong to the active workspace:

```sh
docker compose exec memory-mcp python -m app.review_legacy
docker compose exec memory-mcp python -m app.review_legacy --adopt MEMORY_UUID
```

Open WebUI can use the same memory server through its tool picker. Share that connection only with users who should have access to this workspace's memories.

OpenHands also has a `local-file-edit` MCP with line-numbered reads, exact or whitespace-tolerant replacements, helpful mismatch excerpts, and hash-guarded multi-file edits. `search_files` finds candidate paths and hashes; `bulk_edit` validates every target before applying the changes. `create_directory` makes nested project directories, while `create_file` refuses to overwrite existing files. The helper runs as a separate service and is restricted to the mounted project workspace.

Ask an agent to save useful decisions deliberately; never save credentials, tokens, or private personal information. Search results are reference data, not trusted instructions. Memory and search MCPs are private to the Docker network; only the Browser MCP relay is published, and only on host loopback.

For uploaded documents, use Open WebUI's **Workspace > Knowledge** feature to create a knowledge base and add documents. The stack configures Qdrant and the local embedding endpoint by default. Existing knowledge bases and explicit memories are separate: uploaded documents are indexed into Open WebUI's own collection, while MCP memories are saved only when a tool is called.

### Repository evidence for OpenHands

Backend startup registers `local-repo-context` with a read-only mount of the configured Git workspace. The owner backend default LLM profile is created and activated by [the OpenHands startup configuration](scripts/configure-openhands-defaults.sh). The portable client has its own startup script and Compose configuration under `client/`.

| Tool | Evidence returned |
|---|---|
| `repo_search` | Live tracked paths and excerpts with hashes, line ranges, and HEAD revision. Hybrid mode combines lexical matches with local BGE embeddings. |
| `symbol_lookup` | Python definitions, JS/TS declaration matches, and candidate textual references. |
| `docs_lookup` | Local dependency declarations and official release links for covered packages. Supply a confirmed installed version to fetch matching release notes through the existing guarded web loader. |
| `check_changes` | A receipt for Git status, Git whitespace checks, or tracked Python syntax checks. Run project test suites through the terminal. |

On this deployment repository, `repo_search` defaults to the owner backend scope. Use `scope=client` for the portable client and `scope=all` to search both. Hybrid search warms its semantic cache on demand and reports whether it is ready. It embeds up to 400 source chunks per workspace snapshot. Exact search remains available while embeddings load and checks the current contents of tracked files, including unstaged edits. The repo tools skip untracked files, symlinks, and common secret or generated paths. Use direct file reads for a newly created file before it is tracked.

The owner terminal includes Bash, Git, and ripgrep and starts in the mounted checkout. It runs inside the Canvas container, so the installed software and shell startup configuration can differ from the WSL terminal. Use the short `revision` from `read_file_lines` with `replace_text` to guard edits to host files. The edit preserves the file's ownership and mode.

For owner OpenHands requests using `gpt-oss-20b`, the LiteLLM compatibility hook keeps native action calls and lets final responses arrive as normal messages. It also pauses a search tool for the rest of a turn after the agent repeats the same search, leaving the other tools available.

Run the MCP integration checks and verify the running Canvas agent from this checkout:

```sh
docker compose --profile openhands exec -T openhands python - < scripts/verify-openhands-tools.py
./scripts/verify-openhands-canvas.sh
```

The Canvas check runs ten repository tasks, then verifies search and fetch, reading and editing a host-owned file, and running commands in the mounted checkout. The SDK comparison is also available in `scripts/eval-openhands-repo.py`.

## Web search

Search is enabled by default. Open WebUI uses local SearXNG and the authenticated external page loader; OpenHands is provisioned with local `search_web` and guarded `fetch_url` tools. Search confirmation is enabled. To turn search off:

```sh
./start.sh --no-web-search
```

Turning it off stops the optional search services, disables the saved Open WebUI search toggle, and disables OpenHands' search MCP. Search queries go to public search providers through SearXNG. The fetcher rejects private/local IPs, non-HTTP(S) URLs, nonstandard ports, redirects to private hosts, and oversized/non-text responses. Search snippets and page contents are untrusted; do not follow instructions embedded in them. Search/fetch content is not automatically saved to memory—use Open WebUI Knowledge or the explicit memory tools when you want persistence.

The initial `.env` default is `WEB_SEARCH_ENABLED=true`; set it to `false` to change the default for future `./start.sh` runs. `--web-search` explicitly enables it for one run.

## Install MCP packages

MCP packages install into a persistent npm prefix shared with OpenHands. The wrapper uses a one-shot installer attached to an egress-only network; it does not leave OpenHands with internet access. Npm lifecycle scripts are disabled by default:

```sh
./scripts/mcp.sh install @browsermcp/mcp@2.5.0
./scripts/mcp.sh list
```

The default installer already installs and registers Browser MCP. Other packages with one executable are tested and registered automatically. For a package with multiple executables, choose one explicitly:

```sh
./scripts/mcp.sh install @example/my-mcp@1.2.3 my-mcp-server
```

If a package truly requires npm lifecycle scripts, opt in explicitly with `--allow-scripts`. Only install packages you trust: MCP server code runs inside the OpenHands container with access to the mounted project.

Browser control requires one manual browser-side approval: [install the Browser MCP extension](https://chromewebstore.google.com/detail/bjfgambnhccakkhmkepdoekmckoijdlc) in Chrome, Edge, or Brave, then click **Allow** when it prompts to pair with the local server. The container cannot install or approve browser extensions. The stack exposes the pinned Browser MCP relay only on host loopback ports `9010`–`9012`; it is not published to your LAN. After pairing, OpenHands can use the registered Browser MCP tools. The pairing identity persists across container restarts.

## Choose or switch the coding agent

Only one coding-agent mode is selected at a time. OpenHands runs in Docker. OpenCode runs as a CLI process on the host; install its CLI separately while online if needed:

```sh
npm install -g @opencode/cli
```

Switch to OpenCode, then run it from the project directory you want it to work on:

```sh
./start.sh --agent opencode
cd /path/to/your/project
/home/michael/AI/LocalLLM/scripts/code.sh
```

Switch back to OpenHands with `./start.sh --agent openhands`. Selecting OpenCode stops the OpenHands container; switching modes does not delete its saved data.

The scripts also accept `--backend cuda|metal`, `--model ALIAS`, `--quant QUANT`, and `--context TOKENS`. The default `start.sh` backend is CUDA. Apple Silicon users need a Metal-enabled host `llama-server`; see [Apple Silicon setup](#apple-silicon--metal).

## Download and switch models

List the catalog and download one or more GGUFs. Downloads run sequentially; use the alias and quantization from the catalog:

```sh
./scripts/models.sh list
./scripts/models.sh download \
  --model qwen3-coder-30b-a3b:Q3_K_M \
  --model qwen3.5-9b:Q4_K_M
```

For a model not in the catalog, select an exact GGUF pattern from its Hugging Face repository:

```sh
./scripts/models.sh download \
  --repo organization/model-GGUF \
  --name my-model \
  --include '*Q4_K_M*.gguf'
```

For gated repositories, authenticate with `hf auth login` first. Once downloaded, restart with the requested model and quant:

```sh
./start.sh --model qwen3-coder-30b-a3b --quant Q3_K_M
```

Omit `--quant` to use that model's catalog default. A `--model` passed to `start.sh` changes the model for that run; to change the saved startup default, use `./install.sh --model ALIAS[:QUANT]` or edit `DEFAULT_MODEL` and `DEFAULT_QUANT` in `.env`. LiteLLM continues to expose the selected model under the alias `local-model`; OpenHands and OpenCode keep using that alias when the loaded weights change.

## Benchmark installed models

Use the host `llama.cpp` build to measure prefill and generation throughput for downloaded GGUFs. The command uses each catalog model's default quantization, skips models without a matching downloaded GGUF, and resumes completed model/context/mode measurements in `benchmarks/results/`:

```sh
# Benchmark all installed catalog models
./scripts/benchmark.sh

# Benchmark selected models and contexts
./scripts/benchmark.sh --model qwen3.5-9b --model gemma-4-12b \
  --contexts 1000 10000 --mode both

# Pick a quantization, or run only one measurement type
./scripts/benchmark.sh --model qwen3-coder-30b-a3b --quant Q3_K_M --mode generation
```

The defaults use 8 threads, GPU offload, Flash Attention, llama.cpp's automatic fit with a 1 GiB margin, three repetitions through 10k context, and a 10 minute timeout per measurement. Contexts of 100k and above use one repetition. Override these with `--threads`, `--gpu-layers`, `--flash-attention`, `--fit-margin-mib`, `--repetitions`, and `--timeout`. Use `--no-resume` to rerun existing measurements. Set `LLAMA_BENCH_PATH` if `llama-bench` is outside `llama.cpp/build/bin/llama-bench`. Raw CSV and JSON results are stored under `benchmarks/results/`.

## Apple Silicon / Metal

The one-command installer above targets CUDA. On Apple Silicon, run the model server on the host because Docker Desktop does not expose Metal to Linux containers. Build llama.cpp with Metal support:

```sh
git clone https://github.com/ggml-org/llama.cpp.git
cmake -S llama.cpp -B llama.cpp/build -DGGML_METAL=ON
cmake --build llama.cpp/build --config Release --target llama-server -j
```

Then start the stack with the Metal backend, for example:

```sh
./start.sh --backend metal --agent openhands --model qwen3-coder-30b-a3b --quant Q3_K_M
```

If the binary is not at `llama.cpp/build/bin/llama-server`, set `LLAMA_SERVER_PATH`. The host model server listens for Docker on port 8080; restrict it with the host firewall and do not forward it from your router.

## Offline use and network

Complete model downloads, container image pulls/builds, and package installations while online. With the default `full-control` sandbox, OpenHands has internet access and shell command execution without per-command approval. Optional search services and the temporary npm installer have separate outbound access. Hugging Face model downloads happen on the host, not inside the model-serving stack. For disconnected use, run `./start.sh --no-web-search --sandbox-level read-only`, keep the host offline, and do not add remote MCP servers. UI/API ports bind only to `127.0.0.1` through an ingress proxy.

## Management

```sh
./scripts/stack.sh status
./scripts/stack.sh down
```

`down` stops the services but preserves downloaded models and named Docker volumes. Local environment settings are in `.env`; model metadata is in `config/models.tsv`. Generated credentials, downloaded models, the workspace, Python environments, and runtime files are excluded by [.gitignore](./.gitignore).
