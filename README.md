# messages-mcp

Messages by voice: SMS and chat messages, read from a Matrix inbox of your own. This is an MCP
server that a voice assistant reaches through claude-voice, as one of its plugins.

It is being built in slices. The reading tools are here: `messages_unread`, `messages_read` and
`messages_chats`. They read the bridged Messenger chats and never mark anything read. Sending
comes in v1 after the reading part, in a slice of its own.

It started from claude-voice's plugin template. The guide for writing one is
`docs/write-a-plugin.md` in claude-voice, and `docs/plugin-standard.md` there holds the rules.

## What you need first

Linux with systemd user units, Tailscale, uv, Python 3.12 or newer, and claude-voice. Five
services run beside the plugin: matrix-door, Synapse, Element, ntfy and mautrix-meta.

## Setting it up

The setup guide, [docs/setup.md](docs/setup.md), is written so an agent can follow it alone.
Every step there has a check. Some steps only the owner can do, and they are listed in full here.

<!-- steps:begin -->
<!-- Made by scripts/docs.py from docs/setup.md. Change the guide, then run it. -->

The steps, in full, are in [docs/setup.md](docs/setup.md):

1. Install the project
2. Hear the plugin read made-up chats
3. Put matrix-door in front of the inbox
4. Set up Synapse behind the door
5. Start ntfy behind the door
6. Bridge Messenger with mautrix-meta
7. Add the plugin to claude-voice

### Steps only the owner can do

#### A. Keep the laptop awake with its lid closed

Why: these are system files, which need `sudo` on the owner's machine.

Put these lines in `/etc/systemd/logind.conf.d/lid.conf`. They take effect at the next boot.
Do not restart logind for them, since that can end the desktop session.

```
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
```

Check: `systemd-analyze cat-config systemd/logind.conf` shows both lines.

#### B. List the devices that may reach the inbox

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

#### C. Install Synapse and ntfy, and get the door's certificate

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

#### D. Create the Matrix user

Why: the password is the owner's, and only the owner types it.

```
register_new_matrix_user -c ~/.local/share/synapse/homeserver.yaml http://127.0.0.1:8008
```

Check: the owner can log in with it in step E.

#### E. Log in to Element on the laptop and the phone

Why: it is a login, and the recovery key is a secret only the owner keeps.

Log in on both, verify each against the other, and keep the recovery key somewhere safe. On
both, in Settings, Preferences, turn off "Send read receipts" and "Send typing notifications".

Check: in Element's sessions list, both sessions show as verified, and both switches are off on
each device.

#### F. Try the door from a device that is not on the list

Why: only the owner can reach the other devices on the tailnet.

From another laptop on the tailnet, try the inbox:

```
curl -sS https://<laptop>.<tailnet>.ts.net:8448/_matrix/client/versions
```

Check: the connection is refused, and `~/.local/state/messages-mcp/door.log` has a new line.

#### G. Paste the tailnet rule, and give the phone its push token

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

#### H. Log in to Messenger through the bridge

Why: it is a login to the owner's own account, with Messenger's encryption PIN.

Log in to account 1 in a plain terminal. Account 2 follows a week later, if account 1 had no
lock.

Check: the bridge's management room in Element says the account is logged in. Then have a
message sent to account 1 from another Messenger account, read it in Element on each device, and
look at it on the sender's side. It must not show as seen. If it does, stop here and tell the
manager: something still passes a read on to Messenger.

#### I. Give the plugin its own login to the inbox

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

#### J. Approve the plugin

Why: claude-voice runs a new plugin only once its owner has typed its name.

Do this only once the voice's private mark and its denies are in claude-voice.

```
claude-voice plugins approve messages
```

Check: `claude-voice plugins list` shows `messages` as approved.

<!-- steps:end -->

## What is where

| Part | What it is for |
|---|---|
| `src/messages_mcp/server.py` | The MCP tools. Each answer carries a `say`, the sentence the voice reads aloud. |
| `src/messages_mcp/service.py` | The core: it reads the inbox and words every answer, failures included. Other people's words come back quoted and marked untrusted. |
| `src/messages_mcp/device.py` | What the service needs from an inbox: chats and their messages. |
| `src/messages_mcp/matrix.py` | The real inbox: one sync with Synapse, nothing sent, nothing logged but counts. |
| `src/messages_mcp/login.py` | The plugin's own Matrix login, kept for the owner only. |
| `src/messages_mcp/accounts.py` | What the voice calls each Messenger login, once there are two. |
| `src/messages_mcp/fake.py` | Made-up chats, for `--simulate` and the tests. |
| `src/messages_mcp/settings.py` | The plugin's own config, from its own folder. |
| `src/messages_mcp/platform/` | The one place that knows the operating system. |
| `scripts/ratchet.py` | The quality levels, which may never get worse than main's. |
| `scripts/mutate.py` | Switches each rule off in turn, to prove its test can fail. |
| `scripts/docs.py` | Makes this page's step list from `docs/setup.md`. |

## The checks

Every check is a recipe in the `justfile`, and CI runs the same lines.
`just check` runs the ones a pull request needs, with no network.

<!-- not run: these are the gates; running them from a gate would recurse -->
```
just check
just commits
just ratchet
```

A new test must fail on the code before it, and the `red-first` job proves that. It runs only
the test files a change touches. So it does not see a change to shared test setup, such as
`tests/conftest.py`; say that in the pull request.

A rule is a line marked `# RULE: what it says`. Each one needs a test with its words as the
name, and a row in `scripts/mutations/`.

A commit may carry `Co-Authored-By` and an outside contributor's `Signed-off-by`, and no other
trailers. Any other note
belongs in the pull request's text. `just setup` turns on the hook that says so early.

## Cleaning up your own worktree

A session that worked in a worktree of its own removes it when it is done. Do it in this order.

1. While `scripts/.mutate-recovery.json` exists, a mutation run of yours still has a line
   switched off. Stop that run with SIGTERM, and it puts the line back itself.
2. Run the repair, which puts back a line a killed run left and runs nothing else.
3. Check that `git status` shows nothing. A plain `git worktree remove` refuses a worktree with
   changes, and that refusal is the point: never add `--force`.
4. Remove the worktree. Each run's temporary folder went with the run itself.

<!-- not run: these clean up a worktree, so they would remove the one the docs check runs in -->
```
kill -TERM <the mutation run's process>
uv run python scripts/mutate.py --repair-only
git status --short
git worktree remove <the worktree's path>
```
