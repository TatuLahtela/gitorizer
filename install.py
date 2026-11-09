#!/usr/bin/env python3
"""
Gitorizer installer: installs the package via uv and registers it as a
system service (launchd on macOS, systemd on Linux).
"""

import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"+ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

INSTALL_TRAY = "--tray" in sys.argv[1:]

system = platform.system()
if system not in ("Darwin", "Linux"):
    die(f"unsupported platform: {system}")

HOME = Path.home()
USERNAME = os.environ.get("USER") or os.environ.get("LOGNAME") or HOME.name

# ---------------------------------------------------------------------------
# Locate / install uv
# ---------------------------------------------------------------------------

uv = shutil.which("uv")
if not uv:
    die(
        "uv not found. Install it from https://docs.astral.sh/uv/ and re-run this script."
    )

# ---------------------------------------------------------------------------
# Install gitorizer via uv tool
# ---------------------------------------------------------------------------

repo_dir = Path(__file__).parent.resolve()
install_target = f"{repo_dir}[tray]" if INSTALL_TRAY else str(repo_dir)
# `uv tool install` only honours .python-version when run from inside the
# repo, so pass the pinned version explicitly (uv downloads it if needed).
python_version_file = repo_dir / ".python-version"
python_args = (
    ["--python", python_version_file.read_text().strip()]
    if python_version_file.exists()
    else []
)
run([uv, "tool", "install", install_target, *python_args, "--force"])

bin_path = HOME / ".local" / "bin" / "gitorizer"
if not bin_path.exists():
    die(f"installation succeeded but binary not found at {bin_path}")

print(f"installed: {bin_path}")

# ---------------------------------------------------------------------------
# macOS — launchd LaunchAgent
# ---------------------------------------------------------------------------

if system == "Darwin":
    log_dir = HOME / ".local" / "share" / "gitorizer"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "gitorizer.log"

    agents_dir = HOME / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    plist_path = agents_dir / "com.gitorizer.daemon.plist"

    plist_content = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>com.gitorizer.daemon</string>

            <key>ProgramArguments</key>
            <array>
                <string>{bin_path}</string>
            </array>

            <key>RunAtLoad</key>
            <true/>

            <key>KeepAlive</key>
            <true/>

            <key>StandardOutPath</key>
            <string>{log_file}</string>

            <key>StandardErrorPath</key>
            <string>{log_file}</string>

            <key>EnvironmentVariables</key>
            <dict>
                <key>HOME</key>
                <string>{HOME}</string>
                <key>PATH</key>
                <string>{HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
            </dict>

            <key>WorkingDirectory</key>
            <string>{HOME}</string>
        </dict>
        </plist>
    """)

    plist_path.write_text(plist_content)
    print(f"wrote: {plist_path}")

    # Unload any existing instance before (re)loading
    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    run(["launchctl", "load", str(plist_path)])

    print()
    print("service started. Useful commands:")
    print(f"  launchctl start com.gitorizer.daemon")
    print(f"  launchctl stop  com.gitorizer.daemon")
    print(f"  launchctl list | grep gitorizer")
    print(f"  tail -f {log_file}")

# ---------------------------------------------------------------------------
# Linux — systemd user service
# ---------------------------------------------------------------------------

elif system == "Linux":
    systemd_dir = HOME / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_path = systemd_dir / "gitorizer.service"

    service_content = textwrap.dedent(f"""\
        [Unit]
        Description=Gitorizer git auto-commit daemon
        After=network.target

        [Service]
        ExecStart={bin_path}
        Restart=on-failure
        StandardOutput=journal
        StandardError=journal
        Environment=PYTHONUNBUFFERED=1

        [Install]
        WantedBy=default.target
    """)

    service_path.write_text(service_content)
    print(f"wrote: {service_path}")

    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", "--now", "gitorizer"])
    # enable --now leaves an already-running service on the old binary
    run(["systemctl", "--user", "restart", "gitorizer"])

    print()
    print("service started. Useful commands:")
    print("  systemctl --user status gitorizer")
    print("  systemctl --user restart gitorizer")
    print("  journalctl --user -fu gitorizer")

# ---------------------------------------------------------------------------
# Optional tray indicator (python3 install.py --tray)
# ---------------------------------------------------------------------------

if INSTALL_TRAY:
    tray_bin = HOME / ".local" / "bin" / "gitorizer-tray"
    if not tray_bin.exists():
        die(f"tray binary not found at {tray_bin}")

    if system == "Linux":
        autostart_dir = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "autostart"
        autostart_dir.mkdir(parents=True, exist_ok=True)
        desktop_path = autostart_dir / "gitorizer-tray.desktop"
        desktop_path.write_text(textwrap.dedent(f"""\
            [Desktop Entry]
            Type=Application
            Name=Gitorizer Tray
            Comment=Show gitorizer sync status in the system tray
            Exec={tray_bin}
            Icon=vcs-normal
            Terminal=false
            X-GNOME-Autostart-enabled=true
        """))
        print(f"wrote: {desktop_path}")
        subprocess.Popen([str(tray_bin)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("tray started (and will start on login).")

    elif system == "Darwin":
        tray_plist = HOME / "Library" / "LaunchAgents" / "com.gitorizer.tray.plist"
        tray_plist.write_text(textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0">
            <dict>
                <key>Label</key>
                <string>com.gitorizer.tray</string>
                <key>ProgramArguments</key>
                <array>
                    <string>{tray_bin}</string>
                </array>
                <key>RunAtLoad</key>
                <true/>
                <key>LimitLoadToSessionType</key>
                <string>Aqua</string>
            </dict>
            </plist>
        """))
        print(f"wrote: {tray_plist}")
        subprocess.run(["launchctl", "unload", str(tray_plist)], capture_output=True)
        run(["launchctl", "load", str(tray_plist)])
        print("tray started (and will start on login).")
