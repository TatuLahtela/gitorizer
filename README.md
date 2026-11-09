# gitorizer

A lightweight daemon that monitors git repositories for file changes and automatically commits them. It can optionally push after each commit and periodically pull from remote.

Uses native filesystem events (inotify on Linux, FSEvents on macOS) for instant change detection, with a polling fallback for other platforms.

## Features

- Watches one or more repositories simultaneously
- Auto-commits after a configurable debounce period (waits for writes to settle)
- Optional auto-push after each commit
- Periodic pull from remote (configurable per repo)
- Optional debounced post-change hook after repository content changes
- Single TOML config file with global defaults overridable per repository
- Optional system tray indicator showing each repository's last sync and errors

## Installation

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

### Quick install

The bundled installer installs the binary and registers gitorizer as a
service that starts on login (launchd on macOS, a systemd user service on
Linux):

```bash
git clone https://github.com/ration/gitorizer.git
cd gitorizer
python3 install.py
```

Create a [configuration file](#configuration) first so the service has
something to watch. The manual steps the installer performs are described
below and in the service sections at the end of this README.

### Manual install

```bash
uv tool install git+https://github.com/ration/gitorizer.git
```

Or from a local clone:

```bash
git clone https://github.com/ration/gitorizer.git
uv tool install ./gitorizer
```

This installs the `gitorizer` binary to `~/.local/bin/`.

## Configuration

Create `$XDG_CONFIG_HOME/gitorizer/gitorizer.toml` (`XDG_CONFIG_HOME` defaults to `~/.config`):

```toml
[defaults]
push = false         # push after every auto-commit
pull_interval = 300  # seconds between pulls; 0 to disable
commit_debounce = 10 # seconds of quiet before committing

[[repos]]
path = "~/projects/my-repo"
push = true        # override default for this repo
pull_interval = 60

[[repos]]
path = "~/notes"
# inherits all defaults
```

Each `[[repos]]` block must have a `path`. All other keys are optional and fall back to `[defaults]`.

To run follow-up work after local commits or successful pulls, add an
application-level post-change hook:

```toml
[post_change_hook]
debounce = 30
command = "/home/me/bin/update-document-index"
```

Changes are coalesced during the quiet period, then the hook runs once with
the paths of every repository that changed as arguments, e.g.
`update-document-index /home/me/notes /home/me/blog`. To pass fixed arguments
of your own, use an array instead: `command = ["indexer", "--update"]`.

The hook runs without a shell, so wrap pipelines or redirections in a
script. A pending run is completed during a graceful daemon shutdown. The
hook inherits the daemon's environment, so use an absolute executable path
if a service cannot find it on its `PATH`.

A run is killed after `timeout` seconds (default 300), so a hung hook
cannot block later runs or the daemon's shutdown.

## Usage

```bash
gitorizer
```

By default, gitorizer reads `$XDG_CONFIG_HOME/gitorizer/gitorizer.toml` (`XDG_CONFIG_HOME` defaults to `~/.config`). Pass `--config` to use a different file.

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | `$XDG_CONFIG_HOME/gitorizer/gitorizer.toml` | Path to TOML config file |
| `--log-level` | `INFO` | Verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Stop the daemon with `Ctrl+C` (or `SIGTERM`). It will finish any in-progress git operation before exiting.

## System tray indicator

`gitorizer-tray` puts an icon in the system tray (KDE Plasma, GNOME with the
AppIndicator extension, other StatusNotifierItem hosts, and macOS). The icon is
green when all repositories are in sync, red when any has an error, and grey
when the daemon is not running.

Its menu lists every watched repository with the time of the last successful
sync (commit, push, pull, or fetch) and, beneath it, the latest error for each
failing operation. Click a repository to open its folder, or click an error to
copy the message. An error clears once the same operation succeeds again.

The tray needs PySide6, available as the `tray` extra:

```bash
python3 install.py --tray          # also starts the tray on login
# or
uv tool install --force './gitorizer[tray]'
gitorizer-tray
```

The daemon and the tray share state through
`$XDG_STATE_HOME/gitorizer/status.json` (`XDG_STATE_HOME` defaults to
`~/.local/state`), so the tray works whether the daemon runs as a service
or in a terminal.

## Commit message format

```
gitorizer: auto-commit 2026-02-28T14:32 [notes.md, ideas.txt]
```

File lists longer than 10 entries are truncated with a count of the remainder.

## Running as a systemd service (Linux)

`install.py` sets this up automatically; the manual steps follow.

Create `~/.config/systemd/user/gitorizer.service`:

```ini
[Unit]
Description=Gitorizer git auto-commit daemon
After=network.target

[Service]
ExecStart=%h/.local/bin/gitorizer
Restart=on-failure
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
```

Then enable and start it:

```bash
systemctl --user enable --now gitorizer
journalctl --user -fu gitorizer
```

## Running as a launchd service (macOS)

`install.py` sets this up automatically; the manual steps follow.

Create a log directory and a LaunchAgent plist:

```bash
mkdir -p ~/.local/share/gitorizer
```

Create `~/Library/LaunchAgents/com.gitorizer.daemon.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.gitorizer.daemon</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USERNAME/.local/bin/gitorizer</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/.local/share/gitorizer/gitorizer.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/.local/share/gitorizer/gitorizer.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>/Users/YOUR_USERNAME</string>
        <key>PATH</key>
        <string>/Users/YOUR_USERNAME/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>

    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME</string>
</dict>
</plist>
```

Replace `YOUR_USERNAME` with your macOS username (`whoami`).

Load and start the service:

```bash
launchctl load ~/Library/LaunchAgents/com.gitorizer.daemon.plist
```

**Managing the service:**

```bash
# Start / stop
launchctl start com.gitorizer.daemon
launchctl stop com.gitorizer.daemon

# Disable autostart
launchctl unload ~/Library/LaunchAgents/com.gitorizer.daemon.plist

# Restart after config changes
launchctl stop com.gitorizer.daemon && launchctl start com.gitorizer.daemon

# Check status (shows PID and last exit code)
launchctl list | grep gitorizer

# View logs
tail -f ~/.local/share/gitorizer/gitorizer.log
```
