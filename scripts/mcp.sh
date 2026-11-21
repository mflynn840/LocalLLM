#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"
MANIFEST="$ROOT/runtime/mcp-packages.json"

usage() {
    cat <<'EOF'
Usage:
  scripts/mcp.sh install [--allow-scripts] PACKAGE [BIN] [-- ARG ...]
  scripts/mcp.sh list

Packages install into a persistent OpenHands npm prefix through a temporary,
restricted installer container. Lifecycle scripts are disabled by default.
EOF
}

ensure_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        (umask 077 && cp "$ROOT/.env.example" "$ENV_FILE")
        echo "Created .env from .env.example."
    fi
}

command="${1:-}"
[[ -n "$command" ]] || { usage >&2; exit 2; }
shift

case "$command" in
    list)
        python3 - "$MANIFEST" <<'PY'
import json
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
records = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
for record in records:
    print(f"{record['id']}: {record['package']} ({record['command']})")
if not records:
    print("No npm-installed MCP servers are registered.")
PY
        ;;
    install)
        allow_scripts=0
        if [[ "${1:-}" == "--allow-scripts" ]]; then
            allow_scripts=1
            shift
        fi
        package="${1:-}"
        [[ -n "$package" ]] || { usage >&2; exit 2; }
        shift
        if [[ ! "$package" =~ ^(@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+(@[A-Za-z0-9._*^~+-]+)?$ ]]; then
            echo "Invalid npm package spec: $package" >&2
            exit 2
        fi

        binary_name=""
        if [[ $# -gt 0 && "$1" != "--" ]]; then
            binary_name="$1"
            shift
        fi
        if [[ "${1:-}" == "--" ]]; then
            shift
        fi
        args=("$@")
        if [[ -n "$binary_name" && ! "$binary_name" =~ ^[A-Za-z0-9._-]+$ ]]; then
            echo "Invalid npm executable name: $binary_name" >&2
            exit 2
        fi

        ensure_env
        mkdir -p "$ROOT/runtime/openhands-npm-cache" "$ROOT/runtime/openhands-npm-global"
        host_gid="$(id -g)"
        chgrp "$host_gid" "$ROOT/runtime/openhands-npm-cache" "$ROOT/runtime/openhands-npm-global"
        chmod 2770 "$ROOT/runtime/openhands-npm-cache" "$ROOT/runtime/openhands-npm-global"
        if [[ ! -e "$MANIFEST" ]]; then
            printf '[]\n' > "$MANIFEST"
        fi
        chgrp "$host_gid" "$MANIFEST"
        chmod 640 "$MANIFEST"

        npm_args=(install --global)
        [[ "$allow_scripts" == "1" ]] || npm_args+=(--ignore-scripts)
        npm_args+=("$package")
        docker compose --env-file "$ENV_FILE" --profile mcp-install \
            run --rm --no-deps mcp-installer npm "${npm_args[@]}"

        if [[ "$package" == "@browsermcp/mcp" || "$package" == "@browsermcp/mcp@2.5.0" ]]; then
            docker compose --env-file "$ENV_FILE" --profile mcp-install \
                run --rm --no-deps mcp-installer node - <<'JS'
const fs = require("node:fs");
const path = require("node:path");

const packageDir = path.join(
  process.env.NPM_CONFIG_PREFIX || "/mcp-global",
  "lib/node_modules/@browsermcp/mcp",
);
const packageJson = JSON.parse(
  fs.readFileSync(path.join(packageDir, "package.json"), "utf8"),
);
if (packageJson.version !== "2.5.0") {
  throw new Error("Browser MCP 2.5.0 is required for the pinned relay patch.");
}

const daemonFile = path.join(packageDir, "dist/daemon.js");
const source = fs.readFileSync(daemonFile, "utf8");
const loopback = 'server.listen({ port, host: RELAY_HOST, exclusive: true }, () => {';
const allInterfaces = 'server.listen({ port, host: "0.0.0.0", exclusive: true }, () => {';
const occurrences = (text, value) => text.split(value).length - 1;
if (occurrences(source, loopback) === 1) {
  fs.writeFileSync(daemonFile, source.replace(loopback, allInterfaces));
} else if (occurrences(source, allInterfaces) !== 1) {
  throw new Error("Browser MCP relay source did not match the expected pinned build.");
}
console.log("Configured Browser MCP relay for the loopback-only published ports.");
JS
        fi

        python3 - "$ROOT" "$package" "$binary_name" "$host_gid" "${args[@]}" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
package_spec, selected_bin, host_gid = sys.argv[2:5]
args = sys.argv[5:]
if package_spec.startswith("@"):
    scope, package_part = package_spec.split("/", 1)
    package_name = scope + "/" + package_part.split("@", 1)[0]
else:
    package_name = package_spec.split("@", 1)[0]

prefix = root / "runtime/openhands-npm-global"
node_modules = (prefix / "lib/node_modules").resolve()
package_dir = (node_modules / package_name).resolve()
if not package_dir.is_relative_to(node_modules):
    raise SystemExit("Package path escapes the npm global directory.")
package_json = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
bin_config = package_json.get("bin")
if isinstance(bin_config, str):
    bins = {package_name.rsplit("/", 1)[-1]: bin_config}
elif isinstance(bin_config, dict):
    bins = bin_config
else:
    bins = {}
if selected_bin:
    if selected_bin not in bins:
        raise SystemExit(f"{package_name} does not provide executable {selected_bin!r}. Available: {', '.join(bins)}")
    bin_name = selected_bin
elif len(bins) == 1:
    bin_name = next(iter(bins))
elif not bins:
    raise SystemExit(f"{package_name} has no executable to register as an MCP server.")
else:
    raise SystemExit(
        f"{package_name} provides multiple executables. Choose one: {', '.join(bins)}"
    )
if not re.fullmatch(r"[A-Za-z0-9._-]+", bin_name):
    raise SystemExit("The selected npm executable has an unsupported name.")
target = (package_dir / bins[bin_name]).resolve()
if not target.is_file() or not target.is_relative_to(package_dir):
    raise SystemExit("The npm executable is missing or escapes its package directory.")
link = prefix / "bin" / bin_name
if not link.exists():
    raise SystemExit(f"npm did not create the expected executable link: {link}")

slug = re.sub(r"[^a-z0-9]+", "-", package_name.lower()).strip("-")
server_id = f"npm-{slug}"[:63].rstrip("-")
manifest = root / "runtime/mcp-packages.json"
records = json.loads(manifest.read_text(encoding="utf-8"))
if any(item.get("id") == server_id and item.get("package") != package_name for item in records):
    raise SystemExit(f"Another installed package already uses the MCP ID {server_id}.")
record = {
    "id": server_id,
    "package": package_name,
    "command": f"/home/openhands/.local/npm/bin/{bin_name}",
    "args": args,
}
records = [item for item in records if item.get("id") != server_id]
records.append(record)
manifest.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
os.chown(manifest, -1, int(host_gid))
os.chmod(manifest, 0o640)
print(f"Registered {server_id} -> {record['command']}")
PY

        running="$(docker compose --env-file "$ENV_FILE" --profile openhands ps --status running --services)"
        if printf '%s\n' "$running" | grep -qx openhands; then
            bash "$ROOT/scripts/configure-openhands-defaults.sh"
        else
            echo "This MCP will be tested and registered automatically the next time OpenHands starts."
        fi
        ;;
    -h|--help)
        usage
        ;;
    *)
        echo "Unknown command: $command" >&2
        usage >&2
        exit 2
        ;;
esac
