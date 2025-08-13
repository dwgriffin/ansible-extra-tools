#!/usr/bin/env python
"""Runs Ansible Role on targeted hosts.

Copyright (C) 2025 Dan Griffin
Portions Copyright: (c) Ansible Project

Based on work from ansible

Licensed under the GNU General Public License v3.0+ 
(see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
"""
# PYTHON_ARGCOMPLETE_OK

import os
import shutil

from ansible import constants as C
from ansible import context
from ansible.cli import CLI
from ansible.cli.arguments import option_helpers as opt_help
from ansible.errors import AnsibleError
from ansible.executor.task_queue_manager import TaskQueueManager
from ansible.playbook.play import Play
from ansible.utils.collection_loader._collection_finder import _get_collection_role_path
from ansible.utils.display import Display


class RoleCLI(CLI):
    """The tool to run Ansible roles"""

    name = "ansible-role"
    role_play = {
        "name": "Ansible Role Play",
        "hosts": None,
        "roles": None,
        "gather_facts": True,
    }

    def init_parser(self, usage="", desc=None, epilog=None):
        super().init_parser(
            usage="%prog [options] role [role_two ...]",
            desc="Runs Ansible roles on targeted hosts.",
        )

        opt_help.add_connect_options(self.parser)
        opt_help.add_runas_options(self.parser)
        opt_help.add_vault_options(self.parser)
        opt_help.add_fork_options(self.parser)
        opt_help.add_module_options(self.parser)
        opt_help.add_check_options(self.parser)
        opt_help.add_subset_options(self.parser)
        opt_help.add_runtask_options(self.parser)
        self.parser.add_argument("--disable-facts", dest="gather_facts",
                                 action="store_false", default=True,
                                 help="Disable fact gathering for play.")
        self.parser.add_argument("--flush-cache", dest="flush_cache", action="store_true",
                                 help="clear the fact cache for every host in inventory")
        self.parser.add_argument("--force-handlers", dest="force_handlers", action="store_true",
                                 default=getattr(C, "DEFAULT_FORCE_HANDLERS", False),
                                 help="run handlers even if a task fails")
        self.parser.add_argument("-i", "--inventory", dest="inventory", action="append",
                                 help="specify inventory host path or comma separated host list")
        self.parser.add_argument("--start-at-task", dest="start_at_task",
                                 help="start the playbook at the task matching this name")
        self.parser.add_argument("--step", dest="step", action="store_true",
                                 help="one-step-at-a-time: confirm each task before running")

        self.parser.add_argument("-l", "--limit", dest="subset", required=True,
                                 help="Limit role(s) to selected hosts to pattern")
        self.parser.add_argument("args", help="Role(s)", metavar="role", nargs="+")

    def post_process_args(self, options):
        """Process args"""
        options = super().post_process_args(options)

        Display.verbosity = options.verbosity
        self.validate_conflicts(options, runas_opts=True, fork_opts=True)
        return options

    def run(self):
        """Run"""
        super().run()
        self.role_play["hosts"] = context.CLIARGS["subset"]
        self.role_play["roles"] = list(context.CLIARGS["args"])
        if not context.CLIARGS["gather_facts"]:
            self.role_play["gather_facts"] = False

        sshpass = None
        becomepass = None
        passwords = {}

        # initial error check, to make sure all specified roles are accessible
        verify_roles(context.CLIARGS["args"])

        (sshpass, becomepass) = self.ask_passwords()
        passwords = {"conn_pass": sshpass, "become_pass": becomepass}

        # create base objects
        loader, inventory, variable_manager = self._play_prereqs()

        CLI.get_host_list(inventory, context.CLIARGS["subset"])

        # flush fact cache if requested
        if context.CLIARGS["flush_cache"]:
            self._flush_cache(inventory, variable_manager)

        tqm = None
        try:
            tqm = TaskQueueManager(
                inventory=inventory,
                variable_manager=variable_manager,
                loader=loader,
                passwords=passwords,
            )
            play = Play().load(
                self.role_play,
                variable_manager=variable_manager,
                loader=loader,
            )
            tqm.run(play)
        finally:
            if tqm is not None:
                tqm.cleanup()
            shutil.rmtree(getattr(C, "DEFAULT_LOCAL_TMP", False), ignore_errors=True)

    @staticmethod
    def _flush_cache(inventory, variable_manager):
        """Flush fact cache"""
        for host in inventory.list_hosts():
            hostname = host.get_name()
            variable_manager.clear_facts(hostname)


def verify_roles(roles):
    """Verify that provided roles exist"""
    for role in roles:
        # resolve if it is collection role with FQCN notation
        resource = _get_collection_role_path(role)
        if resource is not None:
            break
        # not an FQCN so must be a directory
        roles_path = [os.getcwd()] + getattr(C, "DEFAULT_ROLES_PATH", False)
        role_path = None
        for search_path in roles_path:
            check_role = os.path.join(search_path, role)
            if os.path.exists(check_role):
                if os.path.isdir(check_role):
                    role_path = os.path.abspath(check_role)
                    break
                raise AnsibleError(
                    f"the role: {role} does not appear to be a directory"
                )
        if not role_path:
            raise AnsibleError(f"the role: {role} could not be found")


def main(args=None):
    """Run ansible-role"""
    RoleCLI.cli_executor(args)


if __name__ == "__main__":
    main()
