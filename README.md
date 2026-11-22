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
- **OpenHands:** `http://localhost:8000/canvas` — owner agent with the project mounted at `/projects`.
- **Registration portal:** `${BACKEND_PUBLIC_URL}/register` — requires the shared registration code and creates personal model accounts.

Open WebUI requires login and allows signup. On a new install, Open WebUI creates the first admin from `WEBUI_ADMIN_EMAIL` and `WEBUI_ADMIN_PASSWORD`; existing no-auth installs keep their account and chat history, with its password set to the generated value. Keep `.env` private.

### Other users' PCs

The backend shares its model gateway and Open WebUI. Each person runs a separate OpenHands agent on their own PC, with their own files and conversations. Run `./start.sh --mode client` to start the portable deployment from `client/`, or distribute that folder. See [client/README.md](client/README.md).

1. Set a private `MODEL_SIGNUP_CODE` in backend `.env` and share it with the user.
2. After WSL starts, refresh the Windows port forwarding and LocalSubnet firewall rules:

   ```sh
   powershell.exe -ExecutionPolicy Bypass -File scripts/backend-lan.ps1
   ```

   This forwards TCP 3000 and 4000. It removes the old OpenHands TCP 8000 forwarding. Rerun after WSL restarts because its NAT IP can change.
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

## Use the coding agent

Open WebUI is a chat interface; use OpenHands when you want an agent to inspect or edit files. The backend owner OpenHands mounts the project root at `/projects` and the WSL filesystem at `/host`. This is the owner-facing full-control agent. Client mode mounts only `client/workspace`; it has no host filesystem mount or Docker socket.

OpenHands runs as container root with `HOME=/home/openhands` so its settings, session key, and profiles stay in the mounted persistent state volume. The entire WSL filesystem is mounted read/write at `/host`; DAC override capabilities let it access files regardless of their host ownership or mode. Commands run inside the container, and the host Docker socket is not mounted. Process count is limited to 512, and memory/CPU default to 16 GiB / 8 CPUs (`OPENHANDS_MEMORY_LIMIT` and `OPENHANDS_CPU_LIMIT` in `.env`). The owner UI binds to localhost. OpenHands has read/write access to files and credentials in WSL through `/host`, and full-control mode can run commands without per-command approval by default. Start with a bounded task and review changes.

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
agent broad outbound access and shell command execution. These modes are container boundaries,
not a substitute for reviewing agent changes or isolating sensitive host data. OpenCode runs as a
host CLI and is **not** restricted by these OpenHands sandbox settings.

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

OpenHands durable memory is **explicit**, not an automatic transcript recorder. Startup automatically registers its memory tools (search, save, and delete) against Qdrant's `openhands_memories` collection. The same memory server and the local web-search server are registered in Open WebUI. Choose enabled local tools in the chat's tool picker when you want a conversation to use them. Open WebUI tool-server access is admin-only by default; an admin can share a connection with other users in its access settings. Memory tools can save and delete shared memories, so share only with trusted users.

OpenHands also has a `local-file-edit` MCP with line-numbered reads, exact or whitespace-tolerant replacements, helpful mismatch excerpts, and hash-guarded multi-file edits. `search_files` finds candidate paths and hashes; `bulk_edit` validates every target before applying the changes. Creating a file refuses to overwrite an existing one. The helper runs as a separate service using a pinned MCP SDK and can reach the WSL filesystem through `/host`.

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

Complete model downloads, container image pulls/builds, and package installations while online. With the default `full-control` sandbox, OpenHands has internet access and shell command execution without per-command approval. Optional search services and the temporary npm installer have separate outbound access. Hugging Face model downloads happen on the host, not inside the model-serving stack. For disconnected use, run `./start.sh --no-web-search --sandbox-level read-only`, keep the host offline, and do not add remote MCP servers. UI/API ports bind only to `127.0.0.1` through an ingress proxy.

## Management

```sh
./scripts/stack.sh status
./scripts/stack.sh down
```

`down` stops the services but preserves downloaded models and named Docker volumes. Local environment settings are in `.env`; model metadata is in `config/models.tsv`. Generated credentials, downloaded models, the workspace, Python environments, and runtime files are excluded by [.gitignore](./.gitignore).
