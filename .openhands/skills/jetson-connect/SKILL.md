---
noteId: "3c95f210ea0211f185048d61977aa200"
tags: []
name: "jetson-connect"
description: "Connect to the user's Jetson attached to Windows over WSL USB networking or USB serial, and run commands on it when requested."

---

# Jetson connection

Use `/home/michael/AI/jetson/connect_jetson.sh` for the attached Jetson. Windows has exposed it as NVIDIA USB device `0955:7020`; its USB network gadget uses `cdc_ncm` and its serial console appears as `/dev/ttyACM0` after `usbipd` attaches it to WSL. The bus ID can change. If WSL no longer sees it, ask the user to attach it from Administrator PowerShell with `usbipd attach --wsl --busid <current-busid>` after binding it if needed.

The script finds the network interface by walking its sysfs parent devices for NVIDIA vendor ID `0955`, brings it up, and assigns the usual Jetson host-side address `192.168.55.100/24`. The Jetson gadget address is normally `192.168.55.1`; verify reachability before assuming it is correct. Set `JETSON_HOST_IP` and `JETSON_WSL_IP` to override these values. The WSL SSH key is `~/.ssh/jetson_devkit`, and `~/.ssh/config` maps both `jetson` and `192.168.55.1` to account `michael` with that key. Use `ssh jetson` or `ssh michael@192.168.55.1` for key-based login; do not ask for or store the password. The helper accepts `JETSON_TARGET` or prompts for an SSH target. Use `--configure-only` to prepare networking without opening SSH. Use `--serial` for the USB console.

Network interface setup needs `sudo` once per script run; the script calls `sudo -v` once and then uses that cached authorization for the two network changes. SSH commands run as the Jetson account and do not need WSL sudo. USB serial access is governed by `/dev/ttyACM0` permissions; if the current WSL user is not in `dialout`, add it once with `sudo usermod -aG dialout "$USER"` in a normal Ubuntu WSL terminal and restart that WSL session. Never assume Jetson SSH credentials; use the user's configured SSH alias/key or ask for the target/account when needed.

After connecting, use ordinary SSH commands for read-only inspection first. Run state-changing commands on the Jetson only when the user requests them.

## OpenHands use

Run the connector from a **My PC (WSL)** conversation, where the terminal has the host user's SSH config and key. In Docker mode the host script, USB device, and SSH key are unavailable. Use `/home/michael/AI/jetson/connect_jetson.sh`; do not copy credentials into the workspace. For requested inspection, run a read-only SSH command such as `uname -a`, `hostname`, or a targeted file read. The connector's networking setup may ask for WSL sudo once. Do not modify Jetson state unless the user requests it.

## OpenHands tool

Prefer the `jetson-host_jetson_exec` MCP tool for Jetson commands. It runs SSH on the WSL host through the configured `jetson` alias, so the SSH key stays on the host and the tool works in Docker mode. Default to read-only inspection; only make changes when the user asks. Use the host terminal and `connect_jetson.sh` only for USB-network setup or if the MCP tool is unavailable.

The WSL host's configured `ssh jetson` alias was verified to return the Jetson hostname and Linux kernel information.

## Development access

Use the Jetson only when the user explicitly asks for Jetson work. The `jetson` SSH alias logs in as `michael`; the connected SSD is mounted at `/mnt/jetson-ssd`. Use `jetson-host_jetson_exec` for development, installs, and system administration on that device. `michael` has sudo access, so use `sudo` when file permissions require it. Preserve SSD and user data; do not format or delete user data unless specifically requested.
