"""Installer execution transports."""

from pawflow_installer.transports.base import CommandResult, InstallTransport
from pawflow_installer.transports.local import LocalTransport
from pawflow_installer.transports.ssh import SshTransport

__all__ = ["CommandResult", "InstallTransport", "LocalTransport", "SshTransport"]
