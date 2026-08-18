# Working on SEPL-PC from a laptop, without AnyDesk

AnyDesk streams a *desktop*. Two people connected at once means two desktop sessions, two
browsers and two IDEs all resident on a box whose real constraint is 31 GB of RAM and 16 GB of
shared VRAM. That is a large part of why it falls over. None of it is necessary: the work is
terminal-and-files work, so the laptop only needs a shell.

Everything below keeps compute on the server. The laptop is a keyboard.

---

## Pick one of two shapes

**A — VS Code Remote-SSH (recommended if you already use VS Code).** VS Code runs on the laptop;
a small server component runs on SEPL-PC. Files, terminal, tests, Docker and Claude Code all
execute server-side. Claude Code's extension talks to the server filesystem, so it sees the real
repo and the real containers. Costs ~200 MB of server RAM, versus a whole desktop session.

**B — Plain SSH + tmux + the `claude` CLI.** Lightest possible. `claude` (2.1.223) is already
installed on the server. tmux keeps the session alive across a dropped connection, which matters
on a flaky office link — a long ingestion or calibration run survives your laptop closing.

They are not exclusive; B is a good fallback when VS Code misbehaves.

---

## 1. Reach the server

`sshd` is already running on port 22, with public-key and password auth both enabled (defaults).

Two routes:

- **Office LAN:** `sepl@192.168.200.21`. Works today, nothing to set up. Useless from home.
- **Tailscale:** works from anywhere. The server is already enrolled, but in *someone else's*
  tailnet (`sbokhari@`, where it appears as `sparkline` / `100.67.60.119`).

### Moving the server to your own tailnet

You chose to own the tailnet rather than ask for an invite. Read this first:

> **This removes SEPL-PC from `sbokhari@`'s tailnet.** A device can only be in one tailnet at a
> time. If the tech head reaches this box over Tailscale, that path breaks until you share the
> node back to them. Do it at a moment when that is acceptable, and keep AnyDesk installed until
> you have confirmed SSH works.

On the server:

    sudo tailscale logout
    sudo tailscale login          # prints a URL — open it and sign in with your own account

The first sign-in creates your tailnet. Then on the laptop, install Tailscale and sign in with the
same account. `tailscale status` on either side should list the other.

Optionally, on the server:

    sudo tailscale set --ssh

This lets Tailscale broker SSH using your tailnet identity, so you never manage keys at all —
`ssh sepl@sparkline` just works for devices signed into your tailnet. If you use this, skip step 2.

## 2. Key-based login (skip if using Tailscale SSH)

On the **laptop**:

    ssh-keygen -t ed25519 -C "sidharth-laptop"
    ssh-copy-id sepl@192.168.200.21        # or sepl@sparkline over Tailscale

`ssh-copy-id` uses password auth once to install the key. After that, `ssh sepl@sparkline` is
passwordless. Add to the laptop's `~/.ssh/config` so tools pick it up:

    Host sparkline
        HostName 100.67.60.119
        User sepl
        ServerAliveInterval 30
        ServerAliveCountMax 6

`ServerAliveInterval` matters — without it a flaky link drops long-running commands silently.

Do not touch `~/.ssh/isv_deploy` on the server. That key belongs to the colleague's project.

## 3. Then either

**A:** In VS Code, install *Remote - SSH*, run **Remote-SSH: Connect to Host** → `sparkline`, and
open `/home/sepl/proj1/sparkline-assistant`. Install the Claude Code extension *in the remote
window* (VS Code keeps laptop and remote extensions separate — this is the usual first mistake).

**B:**

    ssh sparkline
    tmux new -s work        # or: tmux attach -t work
    cd ~/proj1/sparkline-assistant && claude

Detach with `Ctrl-b d`; the session and anything running in it survive disconnection.

---

## What the laptop needs, and what it must not have

The laptop is only an editor for shape A/B — the code runs on the server, so it needs **no
secrets at all**. Only clone-and-run-locally requires them.

If you do want a runnable laptop checkout:

    scp sparkline:~/proj1/sparkline-assistant/.env ./.env

Use `scp`. Do not paste `.env` into a chat window, a commit, or a ticket — it holds
`APP_SECRET_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `MINIO_ROOT_PASSWORD` and `SERVICE_TOKEN`.
`.env.example` documents every key by name if you only need the shape.

A laptop checkout cannot reach `localhost:5432`, `:6333` or `:11434` — those are server-side. Point
it at the server's addresses over Tailscale, or accept that only the unit tests run locally (they
pass without any service: 75 of them, no network needed).

### Credentials, and which are recoverable

| Credential | Status |
|---|---|
| `.env` values | On the server. Copy with `scp`, never by hand. |
| `file.admin` / `FileAdmin@2025` | Documented default in `admin_tools/ingest_cli.py:34`; verified working 2026-08-18. Needed to ingest the tester documents. Weak — rotate before the pilot widens. |
| Six tester Open WebUI passwords | **Not recoverable.** Stored as bcrypt hashes. Reset them and reissue; do not try to read them back. |
| Sparkline API pilot passwords | Seeded value is documented in `README.md`. Independent of the Open WebUI set since the 2026-08-13 auth rework. |
| `WEBUI_SECRET_KEY` | Only in PID 1's environment inside `sparkline_webui`. Not in any file. |

Since the auth rework the pipe authenticates with `SERVICE_TOKEN` and never sees a user password,
so **Open WebUI passwords and API passwords are two separate sets**. Be explicit about which you
are handing to testers — it is the Open WebUI one they log in with.

---

## Stop committing from the server

Development is moving to the laptop. Commits from both ends produce divergence that will not
fast-forward. Pick the laptop as the only committing machine, and treat the server checkout as a
deployment that you `git pull` into.

The `.bak` files in the server working tree (`.env.bak-*`, `docker-compose.server.yml.bak-*`) are
deliberately untracked rollback copies. Leave them alone; do not commit them.
