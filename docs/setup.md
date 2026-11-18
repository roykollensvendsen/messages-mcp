# Setting up messages-mcp

This guide is for an agent setting up messages-mcp on the owner's laptop. Follow it top to
bottom. Each step has its commands and a `Check:` line, the command whose output proves that
the step worked. Do not go on past a step whose check fails.

messages-mcp reads the owner's SMS and chat messages from a Matrix inbox on the owner's own
laptop. The voice reaches it through claude-voice, as a plugin. Five services run beside it:

| Service | What it does | Its own docs |
|---|---|---|
| matrix-door | The only way in from the tailnet. It lets in only the devices on the owner's list. | this repository |
| Synapse | The Matrix server that holds the inbox, on 127.0.0.1:8008 only | [element-hq/synapse](https://element-hq.github.io/synapse/latest/) |
| Element | The Matrix app on the laptop and the phone | [element.io](https://element.io/download) |
| ntfy | Pushes "something new" to the phone, with no message text | [docs.ntfy.sh](https://docs.ntfy.sh/) |
| mautrix-meta | Bridges Messenger into Matrix | [docs.mau.fi](https://docs.mau.fi/bridges/go/meta/) |

Some steps wait for one only the owner can do. Those are at the end, under their own heading,
and each agent step names the one it waits for. Ask the owner, then go on when its check passes.

The plugin is built in slices. A step marked **Not built yet** names the part of the build that
brings it. Stop there until that part has merged.

## What you need first

- Linux with systemd user units, and Tailscale running and logged in.
- [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer.
- claude-voice, with this plugin's folders in its secret table. Its own folders are
  `~/.config/messages-mcp/` and `~/.local/state/messages-mcp/`.
- Never Tailscale Funnel for any port here: the inbox is for the owner's own devices only.

## Steps

### 1. Install the project

<!-- not run: it installs packages from the network -->
```
uv sync --all-extras
git config core.hooksPath .githooks
```

Check: `uv run messages-mcp --help` lists the `status` command.

### 2. Hear the plugin answer from its made-up device

For now this is the template's walking skeleton. The reading tools replace it.

<!-- safe-to-run -->
```
uv run messages-mcp status --simulate
```

Check: it prints one sentence for the voice and exits with 0.

### 3. Put matrix-door in front of the inbox

Wait for owner steps A, B and C. Step C gives the door its certificate.

matrix-door listens on the laptop's tailnet addresses, on port 8448 for Synapse and 2586 for
ntfy, with TLS. For each new connection it asks Tailscale which device is calling, before it
reads a byte. A device not on the owner's list, a tagged device and a device shared in from
another tailnet are all refused. So is every call when Tailscale is slow or fails to answer.
A missing or empty list lets nobody in. Each refusal is one line in
`~/.local/state/messages-mcp/door.log`, kept for 30 days. It will not start while Tailscale
Funnel is on for either port.

<!-- not run: it installs the command and starts a service -->
```
uv tool install --from . messages-mcp
mkdir -p ~/.config/systemd/user
cp systemd/matrix-door.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now matrix-door.service
```

Check: `systemctl --user is-active matrix-door.service` prints `active`.

### 4. Set up Synapse behind the door

**Not built yet:** Synapse's checked settings and its user unit come with the build's
bridge-settings slice. Wait for owner step C.

Synapse listens on 127.0.0.1:8008 only, so matrix-door is the one way to it. Its settings keep
message text out of pushes (`push.include_content: false`) and drop old messages after a while.

<!-- not run: it writes Synapse's config and starts a service on the host -->
```
python -m synapse.app.homeserver --server-name <laptop>.<tailnet>.ts.net \
  --config-path ~/.local/share/synapse/homeserver.yaml --generate-config --report-stats=no
systemctl --user enable --now synapse.service
```

Check: `curl -s http://127.0.0.1:8008/_matrix/client/versions` prints JSON with `"versions"`.

### 5. Start ntfy behind the door

**Not built yet:** ntfy's place behind the door comes with the build's door slice, and its
user unit with the bridge-settings slice. Wait for owner step C.

ntfy tells the phone that something is new. It never carries the message text.

<!-- not run: it starts a service on the host -->
```
systemctl --user enable --now ntfy.service
```

Check: `curl -s http://127.0.0.1:2586/v1/health` prints `{"healthy":true}`.

### 6. Bridge Messenger with mautrix-meta

**Not built yet:** the bridge's checked settings and its user unit come with the build's
bridge-settings slice. Wait for owner step H.

The bridge sends no read receipts, no presence and no typing, fetches no old messages, and logs
at warning level only.

<!-- not run: it starts a service on the host -->
```
systemctl --user enable --now mautrix-meta.service
```

Check: `systemctl --user is-active mautrix-meta.service` prints `active`.

### 7. Add the plugin to claude-voice

**Not built yet:** the reading tools come with the build's read slice. Wait for owner step I.

<!-- not run: it changes the owner's own bridge config -->
```
claude-voice plugins install messages --from .
```

Check: `claude-voice plugins list` shows `messages`.

## Steps only the owner can do

### A. Keep the laptop awake with its lid closed

<!-- owner -->
Why: these are system files, which need `sudo` on the owner's machine.

Put these lines in `/etc/systemd/logind.conf.d/lid.conf`. They take effect at the next boot.
Do not restart logind for them, since that can end the desktop session.

```
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
```

Check: `systemd-analyze cat-config systemd/logind.conf` shows both lines.

### B. List the devices that may reach the inbox

<!-- owner -->
Why: who gets in is the owner's choice, and no tool or agent ever writes this list.

Take the phone's and this laptop's IDs from `tailscale status --json`. They are the `ID` of
`Self` and of the phone under `Peer`. Write them to `~/.config/messages-mcp/door.toml`, one
table each, with only the owner able to read it. matrix-door refuses a list others can read.

```
[[device]]
name = "phone"
id = "<the phone's ID>"

[[device]]
name = "laptop"
id = "<this laptop's ID>"
```

Then make it the owner's alone:

```
chmod 600 ~/.config/messages-mcp/door.toml
```

Check: `stat -c %a ~/.config/messages-mcp/door.toml` prints `600`.

### C. Install Synapse and ntfy, and get the door's certificate

<!-- owner -->
Why: it installs packages and changes Tailscale on the owner's machine, with `sudo`. The
operator setting also lets the owner's user drive Tailscale without `sudo`, which widens who
can change it.

Install both with the system's package manager, as each project's install docs say. If
`tailscale cert` needs root for the owner's user, let it run without:

```
sudo tailscale set --operator=$USER
```

To undo it, run `sudo tailscale set --operator=`. Then get the certificate matrix-door serves,
for the laptop's own tailnet name:

```
mkdir -p ~/.config/messages-mcp
tailscale cert --cert-file ~/.config/messages-mcp/door.crt \
  --key-file ~/.config/messages-mcp/door.key <laptop>.<tailnet>.ts.net
chmod 600 ~/.config/messages-mcp/door.key
```

Check: `command -v ntfy` prints a path, and `stat -c %a ~/.config/messages-mcp/door.key` prints
`600`.

### D. Create the Matrix user

<!-- owner -->
Why: the password is the owner's, and only the owner types it.

```
register_new_matrix_user -c ~/.local/share/synapse/homeserver.yaml http://127.0.0.1:8008
```

Check: the owner can log in with it in step E.

### E. Log in to Element on the laptop and the phone

<!-- owner -->
Why: it is a login, and the recovery key is a secret only the owner keeps.

Log in on both, verify each against the other, and keep the recovery key somewhere safe.

Check: in Element's sessions list, both sessions show as verified.

### F. Try the door from a device that is not on the list

<!-- owner -->
Why: only the owner can reach the other devices on the tailnet.

From another laptop on the tailnet, try the inbox:

```
curl -sS https://<laptop>.<tailnet>.ts.net:8448/_matrix/client/versions
```

Check: the connection is refused, and `~/.local/state/messages-mcp/door.log` has a new line.

### G. Paste the tailnet rule, and give the phone its push token

<!-- owner -->
Why: the rule is a policy in the owner's own Tailscale account, and the token is a secret.

Paste the prepared rule into the Tailscale admin page. It gives only the phone and the laptop
port 8448 and ntfy's port. Install the ntfy app on the phone, and give it its token.

Check: the ntfy app shows its topic as connected.

### H. Log in to Messenger through the bridge

<!-- owner -->
Why: it is a login to the owner's own account, with Messenger's encryption PIN.

Install mautrix-meta from its release page, then log in to account 1 in a plain terminal.
Account 2 follows a week later, if account 1 had no lock.

Check: the bridge's management room in Element says the account is logged in.

### I. Approve the plugin

<!-- owner -->
Why: claude-voice runs a new plugin only once its owner has typed its name.

Do this only once the voice's private mark and its denies are in claude-voice.

```
claude-voice plugins approve messages
```

Check: `claude-voice plugins list` shows `messages` as approved.
