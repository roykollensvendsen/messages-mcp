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

Check: `uv run messages-mcp --help` lists the `unread` command.

### 2. Hear the plugin read made-up chats

`--simulate` reads the made-up chats in `src/messages_mcp/fake.py`, never the real inbox.

<!-- safe-to-run -->
```
uv run messages-mcp unread --simulate
```

Check: it prints what the voice would say, starting with `Simulated:`, and exits with 0.

### 3. Put matrix-door in front of the inbox

Wait for owner steps A, B and C. Step C gives the door its certificate.

matrix-door listens on the laptop's tailnet addresses, on port 8448 for Synapse and 2586 for
ntfy, with TLS. For each new connection it asks Tailscale which device is calling, before it
reads a byte. A device not on the owner's list, a tagged device and a device shared in from
another tailnet are all refused. So is every call when Tailscale is slow or fails to answer.
A missing or empty list lets nobody in. Each refusal is one line in
`~/.local/state/messages-mcp/door.log`, kept for 30 days, and a caller that keeps trying gets
one line a minute. The door will not run while Tailscale Funnel is on for either port, or while
Tailscale serve passes Synapse or ntfy on past it. It asks again every minute, and stops if
either turns on.

Tailscale never renews the certificate it wrote in step C, and it lasts 90 days. So a timer
renews it each week, a month ahead, and restarts the door to serve the new one.

<!-- not run: it installs the command and starts a service -->
```
uv tool install --from . messages-mcp
mkdir -p ~/.config/systemd/user
cp systemd/matrix-door.service systemd/matrix-door-cert.service systemd/matrix-door-cert.timer \
  ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now matrix-door.service matrix-door-cert.timer
```

Check: `systemctl --user is-active matrix-door.service matrix-door-cert.timer` prints `active`
twice. The services here and in steps 4 to 6 run with nobody logged in only when lingering is on:
`loginctl show-user "$USER" -p Linger` must print `Linger=yes`. If it prints `Linger=no`, the
owner turns it on with `loginctl enable-linger "$USER"`.

### 4. Set up Synapse behind the door

Wait for owner step C.

Synapse listens on 127.0.0.1:8008 only, so matrix-door is the one way to it. Our settings,
`config/synapse/messages-mcp.yaml`, are read after the ones Synapse generates and win over them:
- a push carries no message text (`push.include_content: false`);
- nobody sees the owner as online;
- messages go after 30 days;
- it talks to no other server, and nobody can sign up.

Messages rest unencrypted in Synapse's database on this laptop for at most 30 days. The bridge
makes no encrypted Matrix rooms in v1: everything runs on this laptop behind Tailscale, and
Messenger's own encryption ends in the bridge here either way. Encrypting the rooms later is
one way, since Matrix cannot take encryption off a room again.

`bridge.yaml` names the bridge to Synapse, and stays empty until step 6.

<!-- not run: it writes Synapse's config and starts a service on the host -->
```
mkdir -p ~/.local/share/synapse
cd ~/.local/share/synapse
python3 -m synapse.app.homeserver --server-name <laptop>.<tailnet>.ts.net \
  --config-path homeserver.yaml --generate-config --report-stats=no
cd -
cp config/synapse/messages-mcp.yaml ~/.local/share/synapse/
echo 'app_service_config_files: []' > ~/.local/share/synapse/bridge.yaml
cp systemd/synapse.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now synapse.service
```

Check: `curl -s http://127.0.0.1:8008/_matrix/client/versions` prints JSON with `"versions"`.

### 5. Start ntfy behind the door

Wait for owner step C.

ntfy tells the phone that something is new. It never carries the message text. It listens on
127.0.0.1:2586 only, behind the door, and keeps its cache in memory. Our settings,
`config/ntfy/server.yml`, let nobody read or send without an entry of their own. The phone gets
one in owner step G.

`messages-mcp ntfy-config` writes them to ntfy's config, with the door's address for this
laptop and the place of ntfy's user list. That list is in `~/.local/state/ntfy`, a folder only
the owner can open.

UnifiedPush needs Synapse to send without logging in (ntfy's own docs), so step G lets anyone
send, but only to the `up*` topics. Every process on this laptop can read Synapse's data, and so
the phone's topic name. So a local process could still send the phone a fake "something is new"
ping. It carries no text, and it can't read anything.

<!-- not run: it writes ntfy's config and starts a service on the host -->
```
messages-mcp ntfy-config config/ntfy/server.yml ~/.config/ntfy/server.yml
cp systemd/ntfy.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ntfy.service
```

Check: `curl -s http://127.0.0.1:2586/v1/health` prints `{"healthy":true}`, and
`curl -s -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:2586/up-made-up/json?poll=1'`
prints `403`.

### 6. Bridge Messenger with mautrix-meta

Wait for owner steps C and D.

The bridge generates its own config, and `messages-mcp bridge-config` merges ours into it,
`config/mautrix-meta/messages-mcp.yaml`. It fetches no old messages and never marks the owner
online when typing. It logs warnings only, and reports to nobody. It talks to Synapse on this
laptop and lets only that server's users use it.

The bridge's config has no switch for read receipts or typing toward Messenger. It passes on
what a Matrix client sends, and three walls stop that:
- Synapse sends the bridge no receipts, typing or presence at all (`appservice.ephemeral_events:
  false`).
- messages-mcp never sends them.
- Element is set not to, in owner step E.

Owner step H checks all three on a second account. The bridge's bot is named `facebookbot` in our
settings, and messages-mcp reads a room only when that bot marked it as a Messenger chat.

<!-- not run: it writes the bridge's config and starts services on the host -->
```
mkdir -p ~/.local/share/mautrix-meta
cd ~/.local/share/mautrix-meta
~/.local/bin/mautrix-meta -e
cd -
messages-mcp bridge-config config/mautrix-meta/messages-mcp.yaml ~/.local/share/mautrix-meta/config.yaml
cd ~/.local/share/mautrix-meta
~/.local/bin/mautrix-meta -g -c config.yaml -r registration.yaml
cd -
echo "app_service_config_files: [$HOME/.local/share/mautrix-meta/registration.yaml]" \
  > ~/.local/share/synapse/bridge.yaml
cp systemd/mautrix-meta.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user restart synapse.service
systemctl --user enable --now mautrix-meta.service
```

Check: `systemctl --user is-active mautrix-meta.service` prints `active`.

`bridge-config` lets every user of this laptop's Synapse use the bridge, as an ordinary user, and
nobody as an admin. That is enough to log in. For the bridge's admin commands, add the owner's
own Matrix user to `bridge.permissions` in `~/.local/share/mautrix-meta/config.yaml`, as
`"@<user>:<laptop>.<tailnet>.ts.net": admin`, and restart the bridge. A later `bridge-config`
run keeps that line, and any admin line for a user on another server goes.

### 7. Add the plugin to claude-voice

Wait for owner step I. The plugin reads nothing until it has its own login.

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

Install Synapse and ntfy with the system's package manager, as each project's install docs say.
Put mautrix-meta's binary for this laptop, from its release page, at `~/.local/bin/mautrix-meta`,
and make it executable. If `tailscale cert` needs root for the owner's user, let it run without:

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

Check: `command -v ntfy` and `ls ~/.local/bin/mautrix-meta` each print a path, and
`stat -c %a ~/.config/messages-mcp/door.key` prints `600`.

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

Log in on both, verify each against the other, and keep the recovery key somewhere safe. On
both, in Settings, Preferences, turn off "Send read receipts" and "Send typing notifications".

Check: in Element's sessions list, both sessions show as verified, and both switches are off on
each device.

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
Why: the rule is a policy in the owner's own Tailscale account, and the phone's password is a
secret only the owner types.

Paste the prepared rule into the Tailscale admin page. It gives only the phone and the laptop
port 8448 and ntfy's port. Then give the phone its own entry in ntfy, which asks for a password
twice, and let Synapse send to the `up*` topics:

```
ntfy user --config ~/.config/ntfy/server.yml add phone
ntfy access --config ~/.config/ntfy/server.yml phone 'up*' read-only
ntfy access --config ~/.config/ntfy/server.yml '*' 'up*' write-only
```

Install the ntfy app on the phone. In Settings, Manage Users, Add New User, give it
`https://<laptop>.<tailnet>.ts.net:2586`, the user `phone` and that password.

Check: `ntfy access --config ~/.config/ntfy/server.yml` lists only those two entries, and the
ntfy app shows its topic as connected. `stat -c %a ~/.local/state/ntfy/user.db` prints `600`.
These commands ran in the owner's own shell, so if it prints anything else, run
`chmod 600 ~/.local/state/ntfy/user.db`.

### H. Log in to Messenger through the bridge, and check that nothing shows as seen

<!-- owner -->
Why: it is a login to the owner's own account, with Messenger's encryption PIN. The check needs a
second, real Messenger account that the owner chooses, such as one of the owner's own or a family
member's, with that person's yes. Meta's terms forbid made-up accounts, so none is made for this.

Log in to account 1 in a plain terminal. Account 2 follows a week later, if account 1 had no
lock.

Check: do these with the second account on its own phone or browser, beside the laptop.
1. From the second account, send account 1 a message that says only "test 1". It appears in
   Element within a minute.
2. In Element, reply "test 2". It reaches the second account within a minute.
3. Send "test 3" from the second account. Open it in Element on the laptop and on the phone.
   Wait two minutes. On the second account, "test 3" must not show as seen or read.
4. In Element, type in the reply box for ten seconds without sending. The second account must
   not show account 1 as typing.
5. On the second account, account 1 must not show as active or online.

If 1 or 2 fails, the bridge does not bridge: tell the manager. If 3, 4 or 5 fails, stop here.
Log the bridge out with `logout` in its management room. Then tell the manager which one failed:
something still passes a read, typing or presence on to Messenger.

### I. Give the plugin its own login to the inbox

<!-- owner -->
Why: it asks for the owner's Matrix password, typed at a prompt that does not show it.

The plugin gets a device of its own on the owner's Matrix user, called `messages-mcp (voice)`
in Element's session list. The password is never kept; the token is, in
`~/.config/messages-mcp/matrix-login.json`, readable by the owner only. The plugin only ever asks
Synapse for a sync: it sends no read receipt, typing or presence, so reading by voice leaves
every message unread. Sending comes in v1 after the reading part, in a slice of its own.

```
messages-mcp login
```

Check: it says `Logged in as device ...`, `stat -c %a ~/.config/messages-mcp/matrix-login.json`
prints `600`, and `messages-mcp unread` says what is new rather than that it is not logged in.
Then send "test 4" from the second account in step H, and run `messages-mcp unread`. It names
"test 4", and after two minutes the second account still does not show it as seen.

### J. Approve the plugin

<!-- owner -->
Why: claude-voice runs a new plugin only once its owner has typed its name.

Do this only once the voice's private mark and its denies are in claude-voice.

```
claude-voice plugins approve messages
```

Check: `claude-voice plugins list` shows `messages` as approved.
