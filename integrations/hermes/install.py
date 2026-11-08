#!/usr/bin/env python3
"""Install the VibeLamp status bridge for Hermes Agent.

What it does:

1. copies ``hermes_plugin/`` to ``~/.hermes/plugins/vibelamp-status/``;
2. enables the plugin in Hermes' config (``hermes plugins enable``);
3. creates a private virtualenv with ``pyserial`` for the daemon;
4. registers the daemon as a user service (launchd on macOS,
   systemd --user on Linux; other platforms get manual instructions).

Usage::

    python install.py                 # full install
    python install.py --no-service    # files only, run the daemon yourself
    python install.py --dry-run       # print the plan, change nothing
    python install.py --uninstall     # remove everything it added
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
PLUGIN_SRC = HERE / "hermes_plugin"
PLUGIN_NAME = "vibelamp-status"
STATE_DIR = Path(os.environ.get("VIBELAMP_HOME") or (Path.home() / ".vibelamp"))
VENV_DIR = STATE_DIR / "venv"
DAEMON = HERE / "lampd.py"

LAUNCHD_LABEL = "com.vibelamp.hermes"
LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
SYSTEMD_UNIT = Path.home() / ".config" / "systemd" / "user" / "vibelamp-hermes.service"


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def plugin_target() -> Path:
    return hermes_home() / "plugins" / PLUGIN_NAME


def say(message: str) -> None:
    print(message, flush=True)


def run(command: list) -> bool:
    try:
        return subprocess.run(command, capture_output=True, text=True).returncode == 0
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def install_plugin(dry_run: bool) -> None:
    target = plugin_target()
    say(f"[1/4] plugin -> {target}")
    if dry_run:
        return
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PLUGIN_SRC, target)


def enable_plugin(dry_run: bool) -> None:
    say(f"[2/4] enable {PLUGIN_NAME} in Hermes")
    if dry_run:
        return
    hermes = shutil.which("hermes")
    if not hermes:
        say(f"      hermes CLI not found — run manually: "
            f"hermes plugins enable {PLUGIN_NAME}")
        return
    if run([hermes, "plugins", "enable", PLUGIN_NAME]):
        say("      ok")
    else:
        say(f"      could not enable automatically — run: "
            f"hermes plugins enable {PLUGIN_NAME}")


def make_venv(dry_run: bool) -> Path:
    say(f"[3/4] virtualenv -> {VENV_DIR}")
    python = VENV_DIR / "bin" / "python"
    if dry_run:
        return python
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        if not run([sys.executable, "-m", "venv", str(VENV_DIR)]):
            raise SystemExit("failed to create the virtualenv "
                             f"(python -m venv {VENV_DIR})")
    # Демон работает через SDK, поэтому ставим сам пакет (вместе с pyserial).
    # Ставим из этого чекаута, чтобы версия совпадала с исходниками; если
    # сборка не вышла — пробуем имя из индекса.
    if not run([str(python), "-m", "pip", "install", "--quiet", str(REPO_ROOT)]):
        if not run([str(python), "-m", "pip", "install", "--quiet", "vibelamp"]):
            raise SystemExit("failed to install the vibelamp package into "
                             + str(VENV_DIR))
    return python


def install_service(python: Path, dry_run: bool) -> None:
    system = platform.system()
    say(f"[4/4] service ({system})")
    if dry_run:
        say(f"      would run: {python} {DAEMON}")
        return

    if system == "Darwin":
        LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHD_PLIST.write_text(LAUNCHD_TEMPLATE.format(
            python=python, daemon=DAEMON, state=STATE_DIR), encoding="utf-8")
        run(["launchctl", "unload", str(LAUNCHD_PLIST)])
        if run(["launchctl", "load", str(LAUNCHD_PLIST)]):
            say(f"      loaded {LAUNCHD_PLIST}")
        else:
            say(f"      wrote {LAUNCHD_PLIST} — load it manually: "
                f"launchctl load {LAUNCHD_PLIST}")

    elif system == "Linux":
        SYSTEMD_UNIT.parent.mkdir(parents=True, exist_ok=True)
        SYSTEMD_UNIT.write_text(SYSTEMD_TEMPLATE.format(
            python=python, daemon=DAEMON, state=STATE_DIR), encoding="utf-8")
        run(["systemctl", "--user", "daemon-reload"])
        if run(["systemctl", "--user", "enable", "--now", "vibelamp-hermes"]):
            say(f"      enabled {SYSTEMD_UNIT}")
        else:
            say(f"      wrote {SYSTEMD_UNIT} — enable it manually: "
                f"systemctl --user enable --now vibelamp-hermes")

    else:
        say("      no service manager wired for this platform. Run the daemon "
            "yourself and keep it alive:")
        say(f'      "{python}" "{DAEMON}"')


LAUNCHD_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{daemon}</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict><key>VIBELAMP_HOME</key><string>{state}</string></dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>5</integer>
    <key>StandardOutPath</key><string>{state}/lampd.out.log</string>
    <key>StandardErrorPath</key><string>{state}/lampd.err.log</string>
</dict>
</plist>
""".replace("{label}", LAUNCHD_LABEL)

SYSTEMD_TEMPLATE = """[Unit]
Description=VibeLamp status bridge for Hermes Agent
After=default.target

[Service]
ExecStart="{python}" "{daemon}"
Environment=VIBELAMP_HOME={state}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


def uninstall(dry_run: bool) -> None:
    say("removing the VibeLamp bridge")
    if dry_run:
        return
    system = platform.system()
    if system == "Darwin" and LAUNCHD_PLIST.exists():
        run(["launchctl", "unload", str(LAUNCHD_PLIST)])
        LAUNCHD_PLIST.unlink()
    if system == "Linux" and SYSTEMD_UNIT.exists():
        run(["systemctl", "--user", "disable", "--now", "vibelamp-hermes"])
        SYSTEMD_UNIT.unlink()
        run(["systemctl", "--user", "daemon-reload"])

    hermes = shutil.which("hermes")
    if hermes:
        run([hermes, "plugins", "disable", PLUGIN_NAME])

    for path in (plugin_target(), VENV_DIR):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    spool = STATE_DIR / "events.jsonl"
    if spool.exists():
        spool.unlink()
    say("done — restart the Hermes gateway to unload the plugin")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Install the VibeLamp status bridge for Hermes Agent")
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    parser.add_argument("--no-service", action="store_true",
                        help="install files, do not register a user service")
    parser.add_argument("--uninstall", action="store_true",
                        help="remove the plugin, the venv and the service")
    args = parser.parse_args(argv)

    if args.uninstall:
        uninstall(args.dry_run)
        return 0

    if not hermes_home().exists():
        say(f"warning: {hermes_home()} does not exist — is Hermes Agent installed?")

    install_plugin(args.dry_run)
    enable_plugin(args.dry_run)
    python = make_venv(args.dry_run)
    if args.no_service:
        say("[4/4] service skipped — start the daemon yourself:")
        say(f'      "{python}" "{DAEMON}"')
    else:
        install_service(python, args.dry_run)

    say("")
    say("Restart the Hermes gateway so the plugin loads:")
    say("    hermes gateway restart")
    say("")
    say("Check the lamp: python lampd.py --test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
