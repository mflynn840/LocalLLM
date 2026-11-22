---
noteId: "99acb600e62411f19533cdc465806ccc"
tags: []

---

# Local OpenHands client

This portable Compose app runs the OpenHands agent on the user's computer. It
mounts only `client/workspace`, keeps its conversations in a local Docker volume,
and calls the backend's LiteLLM-compatible model gateway with a personal API
key. It does not mount the backend computer's files or Docker socket.

1. Ask the backend owner for the registration code and open
   `http://<backend-host>:4000/register`.
2. Register an email, follow the invitation, set a password, and create a
   personal key for `local-model` in the LiteLLM UI.
3. Run `./start.sh` (Linux/macOS) or `bash start.sh` (Windows with WSL/Git Bash),
   enter the backend URL, then paste the personal key at the hidden prompt.
4. Open `http://localhost:8000/canvas`. Put projects in `workspace/`.

The backend owner can hand out this `client/` folder or a zip of it. Docker
Compose and network access to the backend are required. The backend must be
running and the user's PC must be on the trusted LAN. Backend model access uses
HTTP unless the owner adds TLS, so use it only on a trusted network.

To stop the client, run `docker compose --env-file .env -f compose.yaml down`.
To change the personal API key, edit `.env` locally or remove it and rerun
`start.sh`. Keep `.env` private.
