---
noteId: "3c95f210ea0211f185048d61977aa200"
tags: []
name: "jetson-connect"
description: "Connect to the user's Jetson attached to Windows over WSL USB networking or USB serial, and run commands on it when requested."

---

# Jetson connection

Use `/home/michael/AI/jetson/connect_jetson.sh` for the Jetson. The helper refreshes the existing elevated Windows task to bind/attach NVIDIA USB device `0955:7020` to WSL when needed, then configures its USB network interface through WSL root and connects by SSH. The USB bus ID can change. If the elevated task is unavailable or Windows does not report the device, ask the user to connect the Jetson USB cable and restore the Windows task; do not ask them to repeat routine WSL sudo or USB attach commands.

The script finds the network interface by walking its sysfs parent devices for NVIDIA vendor ID `0955`, brings it up, and assigns the usual Jetson host-side address `192.168.55.100/24`. The Jetson gadget address is normally `192.168.55.1`; verify reachability before assuming it is correct. Set `JETSON_HOST_IP` and `JETSON_WSL_IP` to override these values. The WSL SSH key is `~/.ssh/jetson_devkit`, and `~/.ssh/config` maps both `jetson` and `192.168.55.1` to account `michael` with that key. Use `ssh jetson` or `ssh michael@192.168.55.1` for key-based login; do not ask for or store the password. Set `JETSON_TARGET` to an SSH alias or user@host; the OpenHands tool sets it to `jetson`. Use `--configure-only` to prepare networking without opening SSH. Use `--serial` for the USB console.

Network interface setup is non-interactive: the existing elevated Windows scheduled task attaches the shared USB device, and the helper invokes WSL as root to configure only the Jetson network interface. It does not store either the WSL or Jetson password. SSH commands run as the Jetson account and do not need WSL sudo. USB serial access is governed by `/dev/ttyACM0` permissions; if the current WSL user is not in `dialout`, add it once with `sudo usermod -aG dialout "$USER"` in a normal Ubuntu WSL terminal and restart that WSL session. Never assume Jetson SSH credentials; use the user's configured SSH alias/key or ask for the target/account when needed.

After connecting, use ordinary SSH commands for read-only inspection first. Run state-changing commands on the Jetson only when the user requests them.

## OpenHands use

Run the connector from a **My PC (WSL)** conversation, where the host bridge has the user's SSH config and key. In Docker mode use `jetson-host_jetson_exec`; its host bridge runs `/home/michael/AI/jetson/connect_jetson.sh` before each SSH command, so USB attach and networking recover automatically when needed. Do not copy credentials into the workspace. For requested inspection, run a read-only SSH command such as `uname -a`, `hostname`, or a targeted file read. Do not modify Jetson state unless the user requests it.

## OpenHands tool

Prefer the `jetson-host_jetson_exec` MCP tool for Jetson commands. It runs SSH on the WSL host through the configured `jetson` alias, so the SSH key stays on the host and the tool works in Docker mode. Default to read-only inspection; only make changes when the user asks. Use the host terminal and `connect_jetson.sh` only for USB-network setup or if the MCP tool is unavailable.

The WSL host's configured `ssh jetson` alias was verified to return the Jetson hostname and Linux kernel information.

## Development access

Use the Jetson only when the user explicitly asks for Jetson work. The `jetson` SSH alias logs in as `michael`; the connected SSD is mounted at `/mnt/jetson-ssd`. Use `jetson-host_jetson_exec` for development, installs, and system administration on that device. `michael` has sudo access, so use `sudo` when file permissions require it. Preserve SSD and user data; do not format or delete user data unless specifically requested.
