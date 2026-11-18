# messages-mcp

Messages by voice: SMS and chat messages, read from a Matrix inbox of your own. This is an MCP
server that a voice assistant reaches through claude-voice, as one of its plugins.

It is being built in slices. For now it holds only the template's walking skeleton: one tool,
`messages_status`, answered by a made-up gizmo in `fake.py`. The reading tools replace it.

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
2. Hear the plugin answer from its made-up device
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

#### D. Create the Matrix user

Why: the password is the owner's, and only the owner types it.

```
register_new_matrix_user -c ~/.local/share/synapse/homeserver.yaml http://127.0.0.1:8008
```

Check: the owner can log in with it in step E.

#### E. Log in to Element on the laptop and the phone

Why: it is a login, and the recovery key is a secret only the owner keeps.

Log in on both, verify each against the other, and keep the recovery key somewhere safe.

Check: in Element's sessions list, both sessions show as verified.

#### F. Try the door from a device that is not on the list

Why: only the owner can reach the other devices on the tailnet.

From another laptop on the tailnet, try the inbox:

```
curl -sS https://<laptop>.<tailnet>.ts.net:8448/_matrix/client/versions
```

Check: the connection is refused, and `~/.local/state/messages-mcp/door.log` has a new line.

#### G. Paste the tailnet rule, and give the phone its push token

Why: the rule is a policy in the owner's own Tailscale account, and the token is a secret.

Paste the prepared rule into the Tailscale admin page. It gives only the phone and the laptop
port 8448 and ntfy's port. Install the ntfy app on the phone, and give it its token.

Check: the ntfy app shows its topic as connected.

#### H. Log in to Messenger through the bridge

Why: it is a login to the owner's own account, with Messenger's encryption PIN.

Install mautrix-meta from its release page, then log in to account 1 in a plain terminal.
Account 2 follows a week later, if account 1 had no lock.

Check: the bridge's management room in Element says the account is logged in.

#### I. Approve the plugin

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
| `src/messages_mcp/service.py` | The core: it asks the gizmo and words every answer, failures included. |
| `src/messages_mcp/device.py` | What the service needs from a gizmo. A real adapter answers it. |
| `src/messages_mcp/fake.py` | A made-up gizmo that refuses what the real one refuses. |
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
