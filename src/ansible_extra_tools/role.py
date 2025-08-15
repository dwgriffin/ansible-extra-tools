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
from ansible.module_utils.facts import timeout
from ansible.playbook import Playbook
from ansible.playbook.play import Play
from ansible.utils.collection_loader._collection_finder import _get_collection_role_path
from ansible.utils.display import Display


class RoleCLI(CLI):
    """The tool to run Ansible roles"""

    name = "ansible-role"

    def init_parser(self, usage="", desc=None, epilog=None):
        super().init_parser(
            usage="%prog [options] role [role_two ...]",
            desc="Runs Ansible roles on targeted hosts.",
        )

        opt_help.add_connect_options(self.parser)
        opt_help.add_runas_options(self.parser)
        opt_help.add_vault_options(self.parser)
        opt_help.add_fork_options(self.parser)
        opt_help.add_output_options(self.parser)
        opt_help.add_module_options(self.parser)
        opt_help.add_check_options(self.parser)
        opt_help.add_subset_options(self.parser)
        opt_help.add_runtask_options(self.parser)
        self.parser.add_argument("--disable-facts", dest="gather_facts",
                                 action="store_false", default=True,
                                 help="Disable fact gathering for play.")
        self.parser.add_argument("--fact-path", dest="fact_path", default=None,
                                 help="Path used for local ansible facts")
        self.parser.add_argument("--flush-cache", dest="flush_cache", action="store_true",
                                 help="clear the fact cache for every host in inventory")
        self.parser.add_argument("--force-handlers", dest="force_handlers",
                                 action="store_true", default=C.DEFAULT_FORCE_HANDLERS,
                                 help="run handlers even if a task fails")
        self.parser.add_argument("--gather-subset", dest="gather_subset", action="append",
                                 default=C.DEFAULT_SUBSET,
                                 help="Gather only subset of facts")
        self.parser.add_argument("--gather-timeout", dest="gather_timeout", type=int,
                                 default=timeout.DEFAULT_GATHER_TIMEOUT,
                                 help="Timeout for individual fact gathering")
        self.parser.add_argument("-i", "--inventory", dest="inventory", action="append",
                                 help="specify inventory host path or comma separated host list")
        self.parser.add_argument("--max-fail-percentage", dest="max_fail_percentage", default=None,
                                 help="Maximum percentage of failed hosts before aborting")
        self.parser.add_argument("--order", default=None,
                                 help="Order in which hosts are run: inventory (default), " +
                                 "reverse_inventory, sorted, reverse_sorted, shuffle")
        self.parser.add_argument("--serial", action="append", default=None,
                                 help="Amount of hosts ansible should manage at a sinlge time")
        self.parser.add_argument("--start-at-task", dest="start_at_task",
                                 help="start the playbook at the task matching this name")
        self.parser.add_argument("--step", dest="step", action="store_true",
                                 help="one-step-at-a-time: confirm each task before running")
        self.parser.add_argument("--strategy", default=C.DEFAULT_STRATEGY,
                                 help="Strategy that tasks are run.  See strategy plugins")

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

        # initial error check, to make sure all specified roles are accessible
        self._verify_roles(context.CLIARGS["args"])

        passwords = self._get_passwords()

        # create base objects
        loader, inventory, variable_manager = self._play_prereqs()

        if context.CLIARGS["flush_cache"]:
            self._flush_cache(inventory, variable_manager)

        cb = self._config_callback()
        try:
            tqm = self._config_tqm(inventory, variable_manager, loader, passwords, cb)
            play = self._role_play(variable_manager, loader)
            playbook = self._config_playbook(loader, play)

            tqm.load_callbacks()
            tqm.send_callback("v2_playbook_on_start", playbook)
            result = tqm.run(play)

            tqm.send_callback("v2_playbook_on_stats", tqm._stats)
        finally:
            if tqm is not None:
                tqm.cleanup()
            if loader:
                loader.cleanup_all_tmp_files()

        shutil.rmtree(C.DEFAULT_LOCAL_TMP, True)
        return result

    def _get_passwords(self):
        """Handle passwords"""
        sshpass = None
        becomepass = None
        sshpass, becomepass = self.ask_passwords()
        return {"conn_pass": sshpass, "become_pass": becomepass}

    def _config_callback(self):
        """Configure callback plugin"""
        if self.callback:
            cb = self.callback
        elif context.CLIARGS["one_line"]:
            cb = "oneline"
        # Respect custom 'stdout_callback' only with enabled 'bin_ansible_callbacks'
        elif C.DEFAULT_LOAD_CALLBACK_PLUGINS:
            cb = C.DEFAULT_STDOUT_CALLBACK
        else:
            cb = "default"

        run_tree = False
        if context.CLIARGS["tree"]:
            C.CALLBACKS_ENABLED.append("tree")
            C.TREE_DIR = context.CLIARGS["tree"]
            run_tree = True

        return {"plugin": cb, "run_tree": run_tree}

    def _config_tqm(self, inv, var_manager, loader, passwords, callback):
        """Configure TaskQueueManager"""
        return TaskQueueManager(
            inventory=inv,
            variable_manager=var_manager,
            loader=loader,
            passwords=passwords,
            stdout_callback=callback["plugin"],
            run_additional_callbacks=C.DEFAULT_LOAD_CALLBACK_PLUGINS,
            run_tree=callback["run_tree"],
            forks=context.CLIARGS["forks"],
        )

    def _role_play(self, var_manager, loader):
        """Configure Role Play"""
        role_play = {
            "name": "Ansible Role Play",
            "hosts": context.CLIARGS["subset"],
            "roles": list(context.CLIARGS["args"]),
            "gather_facts": context.CLIARGS["gather_facts"],
            "gather_subset": context.CLIARGS["gather_subset"],
            "gather_timeout": context.CLIARGS["gather_timeout"],
            "fact_path": context.CLIARGS["fact_path"],
            "force_handlers": context.CLIARGS["force_handlers"],
            "max_fail_percentage": context.CLIARGS["max_fail_percentage"],
            "order": context.CLIARGS["order"],
            "serial": context.CLIARGS["serial"],
            "strategy": context.CLIARGS["strategy"],
        }
        return Play().load(role_play, variable_manager=var_manager, loader=loader)

    def _config_playbook(self, loader, play):
        """Configure Playbook"""
        playbook = Playbook(loader)
        playbook._entries.append(play)
        playbook._file_name = "__role_playbook__"
        return playbook

    @staticmethod
    def _flush_cache(inventory, variable_manager):
        """Flush fact cache"""
        for host in inventory.list_hosts():
            hostname = host.get_name()
            variable_manager.clear_facts(hostname)

    @staticmethod
    def _verify_roles(roles):
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
