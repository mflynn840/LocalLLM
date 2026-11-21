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

The installer creates `.env` if needed, generates local LiteLLM, SearXNG, and web-loader secrets, pulls service images, builds local services, and downloads Gemma 4 12B Q4_K_M plus the BGE embedding model. It also installs Browser MCP into a persistent OpenHands npm prefix through a temporary installer container. Existing non-placeholder secrets, models, OpenHands data, and Docker volumes are preserved. `.env` is kept private with mode `600`.

Choose a different startup model during installation; repeat `--model` to download several models sequentially. The first model becomes the persisted default:

```sh
./install.sh --model qwen3-coder-30b-a3b:Q3_K_M \
  --model qwen3.5-9b:Q4_K_M
```

Omit the quantization suffix to use the model catalog default. You can also set `DEFAULT_MODEL` and `DEFAULT_QUANT` in `.env`; `start.sh` uses those values when no model is passed.

Start the services in the background:

```sh
./start.sh
```

The default is CUDA + OpenHands + the model configured in `.env`, web search enabled, and the `project` sandbox. Startup configures the local LiteLLM profile and MCP servers in OpenHands, then applies search and MCP defaults in Open WebUI. No LLM-provider or MCP-server setup in either UI is required.

- **Open WebUI:** `http://localhost:3000` — general chat, ready without a login prompt.
- **OpenHands:** `http://localhost:8000/canvas` — coding agent with the project mounted at `/projects`.

Open WebUI authentication is disabled for this single-user, localhost-only setup. Anyone who can reach the WebUI is effectively an administrator; do not bind its ports to a LAN address or expose them through a router. When an existing database has exactly one administrator, startup changes that account's email and password to Open WebUI's local no-auth identity in place, preserving its user ID and chat history. If the existing account layout is ambiguous, startup stops rather than changing accounts automatically. Open WebUI's no-auth flow uses the built-in `admin@localhost` / `admin` identity; if you re-enable password authentication, change that password immediately.

OpenHands uses the local LiteLLM profile `litellm_proxy/local-model` at `http://litellm:4000`. Startup creates and activates the default profile if it is missing or still an untouched first-run placeholder; it preserves an existing customized profile. The API key comes from `LITELLM_MASTER_KEY`.

OpenCode has automatic compaction enabled in [config/opencode.json](./config/opencode.json). It starts compacting with a 15% context buffer (at least 16,000 tokens), so with a 100,000-token context it begins at about 84,000 tokens. The compacted conversation keeps roughly the latest 15,000 tokens alongside its summary. Compaction uses the selected local model through LiteLLM.

## Use the coding agent

Open WebUI is a chat interface; use OpenHands when you want an agent to inspect or edit files. The installer configures the project root as `WORKSPACE_DIR` and mounts it into OpenHands as `/projects`. The startup helper grants the OpenHands container group access to project files while preserving `.env` as mode `600`. It does not change permissions inside `.git`, `.venv`, `models`, `runtime`, or build directories. OpenHands workspaces are restricted to this project directory; keep unrelated projects and secrets out of it.

OpenHands runs in a restricted container: no Docker socket, privileged mode, or host-shell access; Linux capabilities are dropped, `no-new-privileges` is enabled, process count is limited to 512, and memory/CPU default to 16 GiB / 8 CPUs (`OPENHANDS_MEMORY_LIMIT` and `OPENHANDS_CPU_LIMIT` in `.env`). UI ports bind to localhost. Start with a bounded task, review changes, and ask the agent to run relevant tests.

### Sandbox levels

Select the OpenHands access mode at startup:

```sh
./start.sh --sandbox-level read-only
./start.sh --sandbox-level project
./start.sh --sandbox-level networked
```

| Level | Project mount | OpenHands internet egress | Use |
|---|---|---|---|
| `read-only` | Read-only | Blocked | Inspect, explain, and plan without modifying project files |
| `project` (default) | Read/write | Blocked | Normal coding against this project |
| `networked` | Read/write | Allowed | Tasks that need package downloads or arbitrary internet access |

The setting is applied by recreating the OpenHands service as needed. `networked` gives the agent broad outbound access, not just package-registry access. These modes are container boundaries, not a substitute for reviewing agent changes or isolating sensitive host data. OpenCode runs as a host CLI and is **not** restricted by these OpenHands sandbox settings.

## Persistent memory and document RAG

Qdrant stores data in a persistent Docker volume. The local BGE-small model creates 384-dimensional embeddings without sending content to a remote service. Open WebUI document retrieval and explicit memory tools use this Qdrant server, with separate collections.

OpenHands durable memory is **explicit**, not an automatic transcript recorder. Startup automatically registers its memory tools (search, save, and delete) against Qdrant's `openhands_memories` collection. The same memory server and the local web-search server are registered in Open WebUI. Choose enabled local tools in the chat's tool picker when you want a conversation to use them. Open WebUI tool-server access is admin-only by default; an admin can share a connection with other users in its access settings. Memory tools can save and delete shared memories, so share only with trusted users.

Ask an agent to save useful decisions deliberately; never save credentials, tokens, or private personal information. Search results are reference data, not trusted instructions. Memory and search MCPs are private to the Docker network; only the Browser MCP relay is published, and only on host loopback.

For uploaded documents, use Open WebUI's **Workspace > Knowledge** feature to create a knowledge base and add documents. The stack configures Qdrant and the local embedding endpoint by default. Existing knowledge bases and explicit memories are separate: uploaded documents are indexed into Open WebUI's own collection, while MCP memories are saved only when a tool is called.

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

Complete model downloads, container image pulls/builds, and package installations while online. With the default `project` sandbox, OpenHands has no general internet egress; optional search services and the temporary npm installer have separate outbound access. `networked` deliberately grants OpenHands internet access. Hugging Face model downloads happen on the host, not inside the model-serving stack. For disconnected use, run `./start.sh --no-web-search --sandbox-level project`, keep the host offline, and do not add remote MCP servers. UI/API ports bind only to `127.0.0.1` through an ingress proxy.

## Management

```sh
./scripts/stack.sh status
./scripts/stack.sh down
```

`down` stops the services but preserves downloaded models and named Docker volumes. Local environment settings are in `.env`; model metadata is in `config/models.tsv`. Generated credentials, downloaded models, the workspace, Python environments, and runtime files are excluded by [.gitignore](./.gitignore).
