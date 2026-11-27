---
name: "jetson-connect"
description: "Use the saved SSH host key when the user requests Jetson access."
---

# Jetson SSH

When the user asks to connect to or work on the Jetson, use the `ssh-hosts` MCP tools. The saved host key is `jetson`; inspect it with `ssh_hosts`, refresh the remote file map with `ssh_map_files`, and locate code with `ssh_search_files`. Use `ssh_exec`, `ssh_read_file`, and `ssh_write_file` for all remote commands and edits. The host bridge prepares the existing WSL USB network and runs SSH as the configured user. SSH keys remain on the host.

Keep Jetson work on the remote host, including project inspection, file edits, and command execution; do not switch into the local workspace midway through that task. Use absolute remote paths and verify writes by reading the file back. For ordinary tasks without a remote-host request, use OpenHands' default My PC (WSL) workspace.
