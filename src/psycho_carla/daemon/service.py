"""
Daemon service files — run C.A.R.L.A. as a background service.

Three platforms:

  * **Linux** — a systemd user unit.
  * **macOS** — a launchd agent plist.
  * **Windows** — a scheduled task that runs the console at logon.

Each generated file embeds the *absolute* path to the current Python
interpreter, so the service keeps working after the virtual environment is
deactivated.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "psycho-carla"

SYSTEMD_TEMPLATE = """\
[Unit]
Description=PSYCHO-C.A.R.L.A. console
After=network.target

[Service]
Type=simple
ExecStart={command}
Restart=on-failure
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""

LAUNCHD_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.psycho.carla.console</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>psycho_carla</string>
        <string>serve</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir}/console.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/console.err.log</string>
</dict>
</plist>
"""

WINDOWS_TASK_TEMPLATE = """\
$action = New-ScheduledTaskAction -Execute '{python}' -Argument '{arguments}'
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName '{task_name}' -Action $action -Trigger $trigger -Settings $settings -Description 'PSYCHO-C.A.R.L.A. console'
"""


def console_command() -> list[str]:
    """The command that starts the console."""
    return [sys.executable, "-m", "psycho_carla", "serve"]


def generate_systemd_service(output: Path | None = None) -> str:
    """Generate a systemd user unit."""
    command = " ".join(console_command())
    content = SYSTEMD_TEMPLATE.format(command=command)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
    return content


def generate_launchd_plist(output: Path | None = None) -> str:
    """Generate a launchd agent plist."""
    log_dir = Path.home() / ".psycho_carla" / "logs"
    content = LAUNCHD_TEMPLATE.format(
        python=sys.executable, log_dir=str(log_dir)
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
    return content


def generate_windows_task(output: Path | None = None) -> str:
    """Generate the PowerShell that registers a Windows scheduled task."""
    arguments = " ".join(console_command()[1:])
    content = WINDOWS_TASK_TEMPLATE.format(
        python=sys.executable,
        arguments=arguments,
        task_name=SERVICE_NAME,
    )
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
    return content


def service_instructions() -> dict[str, str]:
    """Per-platform instructions for installing the service."""
    return {
        "linux": (
            "Save the unit to ~/.config/systemd/user/psycho-carla.service, "
            "then run:\n"
            "  systemctl --user daemon-reload\n"
            "  systemctl --user enable --now psycho-carla\n"
            "  journalctl --user -u psycho-carla -f"
        ),
        "darwin": (
            "Save the plist to ~/Library/LaunchAgents/com.psycho.carla.console.plist, "
            "then run:\n"
            "  launchctl load ~/Library/LaunchAgents/com.psycho.carla.console.plist"
        ),
        "windows": (
            "Run the generated PowerShell in an elevated terminal:\n"
            "  powershell -ExecutionPolicy Bypass -File install.ps1\n"
            "The console then starts automatically at logon."
        ),
    }


def install_service(platform: str | None = None, *, output: Path | None = None) -> Path:
    """Write the service file for the current platform.

    Returns the path the file was written to.
    """
    plat = (platform or sys.platform).lower()
    if plat.startswith("linux"):
        target = output or (
            Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
        )
        generate_systemd_service(target)
        return target
    if plat == "darwin":
        target = output or (
            Path.home()
            / "Library"
            / "LaunchAgents"
            / "com.psycho.carla.console.plist"
        )
        generate_launchd_plist(target)
        return target
    if plat.startswith("win"):
        target = output or (
            Path.home() / ".psycho_carla" / "install-task.ps1"
        )
        generate_windows_task(target)
        return target
    raise RuntimeError(f"No service definition for platform '{plat}'")


def uninstall_service(platform: str | None = None) -> bool:
    """Remove the service file, if it exists."""
    plat = (platform or sys.platform).lower()
    if plat.startswith("linux"):
        target = (
            Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
        )
        if target.exists():
            target.unlink()
            return True
        return False
    if plat == "darwin":
        target = (
            Path.home()
            / "Library"
            / "LaunchAgents"
            / "com.psycho.carla.console.plist"
        )
        if target.exists():
            target.unlink()
            return True
        return False
    if plat.startswith("win"):
        try:
            subprocess.run(
                ["schtasks", "/Delete", "/F", "/TN", SERVICE_NAME],
                capture_output=True,
                timeout=30,
                check=False,
            )
            return True
        except Exception:
            return False
    return False


__all__ = [
    "SERVICE_NAME",
    "console_command",
    "generate_launchd_plist",
    "generate_systemd_service",
    "generate_windows_task",
    "install_service",
    "service_instructions",
    "uninstall_service",
]
