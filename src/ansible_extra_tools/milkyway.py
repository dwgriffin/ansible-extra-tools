#!/usr/bin/env python3
"""Downloads an ansible role from a git hosted source.

Acts as a wrapper for ansible-galaxy for "non-galaxy" ansible roles.
Provides more logical arguments and help than ansible-galaxy.

Copyright (C) 2026 Dan Griffin
Portions Copyright: (c) Ansible Project

Based on work from ansible

Licensed under the GNU General Public License v3.0+
(see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
"""
# PYTHON_ARGCOMPLETE_OK

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union
from dwgriffin_config import ConfigManager, MissingSettingError


@dataclass
class MilkywayGitDefaults:
    """Built-in defaults for git.

    Inherited by MilkywayDefaults.
    """

    user: str = "git"
    protocol: str = "ssh"
    owner: Optional[str] = None
    host: str = "github.com"


@dataclass
class MilkywayDefaults(MilkywayGitDefaults):
    """Built-in defaults for milkyway.  Also defines configurable options for
    ConfigManager.
    """

    role_prefix: str = "ansible-role-"
    branch: Optional[str] = None
    requirements: Optional[str] = None
    destination: Optional[str] = None
    force: bool = True
    force_with_deps: bool = True
    verbose: bool = False


class Milkyway:
    """Wrapper for ansible-galaxy

    Downloads role(s) from private git host.
    """

    def __init__(self, config: ConfigManager):
        """Initialize Milkyway

        Args:
            config (ConfigManager): configuration settings for downloading role(s).
        """
        self.config = config

    def git_repo(self, role: str) -> str:
        """Return the git repo URL

        Args:
            role (str): Role's short name. e.g. "ssh"

        Returns:
            str: Git SSH url string and branch.
                e.g. git+ssh://git@github.com/dwgriffin/ansible-role-ssh.git
        """
        branch = self.config.get("branch") or ""
        role_prefix = self.config.role_prefix or ""
        git_base = f"{self.config.host}/{self.config.owner}"
        if self.config.protocol == "ssh":
            repo = f"git+ssh://{self.config.user}@{git_base}/{role_prefix}{role}.git"
            return f"{repo},{branch},{role}"
        return f"https://{self.config.host}/{self.config.owner}"

    def galaxy_cmd(
        self,
        roles: Optional[Union[str, List[str]]] = None,
        requirements: Optional[str] = None,
    ) -> List[str]:
        """Returns the ansible-galaxy command that will be run

        Args:
            roles (str | list[str] | None):  Role(s) short name to install.
                If multiple roles, they will all share same branch name.
            requirements (str | None): Path to a requirements file.

        Returns:
            list[str]: The full ansible-galaxy command.
        """
        requirements = (
            requirements
            if requirements is not None
            else self.config.get("requirements")
        )
        destination = self.config.get("destination")

        if isinstance(roles, str):
            roles = [roles]
        roles = roles or []

        if not roles and not requirements:
            raise ValueError("Provide one or more role names or a --requirements file.")
        if roles and requirements:
            raise ValueError("Cannot provide role name(s) and --requirements.")

        cmd = ["ansible-galaxy", "install"]

        if self.config.get("force"):
            cmd.append("--force")
        if self.config.get("force_with_deps"):
            cmd.append("--force-with-deps")

        if destination:
            cmd.extend(["-p", destination])

        if self.config.get("verbose"):
            cmd.append("-v")

        if requirements:
            cmd.extend(["-r", requirements])
        else:
            cmd.extend(self.git_repo(role) for role in roles)

        return cmd

    def install(
        self,
        roles: Optional[Union[str, List[str]]] = None,
        requirements: Optional[str] = None,
        dry_run: bool = False,
    ) -> int:
        """Run the ansible-galaxy command

        Args:
            roles (str | list[str] | None):  Role(s) short name to install.
                If multiple roles, they will all share same branch name.
            requirements (str | None): Path to a requirements file.
            dry_run (bool): Defaults to False. If true, print ansible-galaxy command.

        Returns:
            int: Process exit code.
        """
        cmd = self.galaxy_cmd(roles=roles, requirements=requirements)

        if dry_run:
            print(" ".join(cmd))
            return 0

        try:
            result = subprocess.run(cmd, check=False)
        except FileNotFoundError:
            print("Error: 'ansible-galaxy' not found on PATH.", file=sys.stderr)
            return 127
        return result.returncode


class MilkywayCLI:
    """The Milkyway CLI tool.

    Downloads an ansible role.
    """

    def __init__(self, argv: Optional[List[str]] = None):
        """Initialize the CLI tool.

        Args:
           argv (list[str] | None): CLI args to parse.
        """
        self.parser = self.arg_parser()
        self.args = self.parser.parse_args(argv)
        self.settings = self.config()
        self.milkyway = Milkyway(self.settings)

    @staticmethod
    def _find_config() -> Optional[Path]:
        """Search for a .milkyway.ini file.

        Search is performed in the following order. Stopping at first found.
            1. MILKYWAY_CFG (environment variable if set)
            2. .milkyway.ini (in current directory)
            3. ~/.milkyway.ini

        Returns:
            Path | None: The first found milkyway configuration file.
        """
        env_path = os.environ.get("MILKYWAY_CFG")
        if env_path:
            return Path(env_path)

        for config in (Path.cwd() / ".milkyway.ini", Path.home() / ".milkyway.ini"):
            if config.exists():
                return config
        return None

    @staticmethod
    def arg_parser() -> argparse.ArgumentParser:
        """Build the milkyway CLI argument parser.

        Returns:
            argparse.ArgumentParser: parser for arguments to download role.
        """
        parser = argparse.ArgumentParser(
            prog="milkyway",
            description="An ansible-galaxy wrapper for private hosted ansible roles.",
        )
        parser.add_argument(
            "role", nargs="*", default=None, help="Ansible role(s) to download."
        )
        parser.add_argument(
            "-b", "--branch", default=None, help="Git branch to install."
        )
        parser.add_argument(
            "-r",
            "--requirements",
            default=None,
            help="Path to a requirements.yml file to use to download role(s).",
        )
        parser.add_argument(
            "-d",
            "--destination",
            default=None,
            help="Role install path (defaults to Ansible's configured roles_path)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the ansible-galaxy command instead of running it.",
        )

        return parser

    def config(self) -> ConfigManager:
        """Configure Milkyway settings from CLI, env, and defaults.

        Returns:
            ConfigManager: Merged settings from all sources.
        """
        try:
            return ConfigManager(
                defaults=MilkywayDefaults(),
                config_file=self._find_config(),
                env_prefix="MILKYWAY_",
                cli_args=self.args,
                required=["owner"],
            )
        except MissingSettingError as err:
            self.parser.error(str(err))
            return None

    def run(self) -> int:
        """Executes ansible-galaxy using the resolved config.

        Returns:
            int: Exit code of process.
        """
        try:
            return self.milkyway.install(
                roles=self.args.role, dry_run=self.args.dry_run
            )
        except ValueError as err:
            self.parser.error(str(err))
            return None

def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entry point.

    Args:
        argv (list[str] | None): CLI args

    Returns:
        int: process exit code
    """
    return MilkywayCLI(argv).run()


if __name__ == "__main__":
    raise SystemExit(main())
