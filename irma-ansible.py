#!/usr/bin/env python

import os
import sys
import yaml
import tempfile
import shutil
from collections import defaultdict
import argparse
from ansible.cli.galaxy import GalaxyCLI
from ansible.cli.playbook import PlaybookCLI

__WARNING__ = "# This file is generated automatically. Do not edit it\n"


class IrmaConfigError(Exception):
    pass


class IrmaConfig(object):

    def __init__(self, type, filename, offline, no_vars_address):
        self.type = type
        self.offline = offline
        self.no_vars_address = no_vars_address
        self.filename = None
        self.config = None
        try:
            self.name = os.path.basename(filename).split(".")[0]
        except Exception:
            raise IrmaConfigError("Wrong filename")
        self.servers = None
        self._read(filename)

    def _read(self, filename):
        try:
            with open(filename) as f:
                self.config = yaml.load(f.read())
            self.filename = filename
            self._parse()
        except ValueError as e:
            raise IrmaConfigError(str(e))

    def _parse(self):
        self.servers = self.config.get("servers", [])
        self.ansible_hosts = dict()
        self.ansible_groups = defaultdict(list)
        for server in self.config["servers"]:
            server_name = server["name"]
            server_ip = server["ip"]
            server_groups = server["ansible_groups"]
            if server_name in self.ansible_hosts:
                raise IrmaConfigError("%s defined twice" % server_name)
            self.ansible_hosts[server_name] = {
                "address": server_ip}
            if self.type == 'core':
                is_windows = server.get("windows", False)
                self.ansible_hosts[server_name]["windows"] = is_windows
            for group in server_groups:
                self.ansible_groups[group].append(server_name)

    def ansible_vars(self):
        def get_server_addr(name):
            if len(self.ansible_groups[name]) == 0:
                raise IrmaConfigError("Missing group {}".format(name))
            if len(self.ansible_groups[name]) > 1:
                raise IrmaConfigError("More than one {}".format(name))
            server_name = self.ansible_groups[name][0]
            server_addr = self.ansible_hosts[server_name]["address"]
            return server_addr

        ansible_vars = self.config.get("ansible_vars", [])
        ansible_vars["offline"] = self.offline
        if self.type == 'core':
            if not self.no_vars_address:
                # output brain address in all cases
                ansible_vars["brain_addr"] = get_server_addr("brain")

                frontend_addr = get_server_addr("frontend")
                sql_addr = get_server_addr("sql-server")
                if sql_addr != frontend_addr:
                    # Output frontend & sql addresses only if standalone SQL
                    ansible_vars["frontend_addr"] = frontend_addr
                    ansible_vars["sql_addr"] = sql_addr

                if ansible_vars.get("monitoring", False):
                    # Output monitoring only if enabled
                    ansible_vars["monitoring_addr"] = \
                        get_server_addr("monitoring-core")

        return ansible_vars

    def ansible_inventory(self):
        ansible_inventory = ""
        for (hostname, param) in self.ansible_hosts.items():
            address = param["address"]
            if self.type == 'core':
                is_windows = param["windows"]
                if is_windows:

                    ansible_inventory += hostname + " ansible_ssh_host=" + \
                                         address + " ansible_ssh_port=5985" \
                                         " ansible_connection=winrm" \
                                         " ansible_ssh_pass='vagrant'"
                else:
                    ansible_inventory += hostname + " ansible_ssh_host=" + \
                                         address
                    if address in ["localhost", "127.0.0.1"]:
                        ansible_inventory += " ansible_connection=local"
                    ansible_inventory += " ansible_ssh_private_key_file="
                    ansible_inventory += "'~/.vagrant.d/insecure_private_key'"
            else:
                ansible_inventory += hostname
                ansible_inventory += " ansible_ssh_host=" + address
                if address in ["localhost", "127.0.0.1"]:
                    ansible_inventory += " ansible_connection=local"
                ansible_inventory += " ansible_ssh_private_key_file="
                ansible_inventory += "'~/.vagrant.d/insecure_private_key'"
            ansible_inventory += "\n"

        ansible_inventory += "\n\n"
        for (group, servers_list) in self.ansible_groups.items():
            ansible_inventory += "[" + group + "]\n"
            for server in set(servers_list):
                ansible_inventory += server + "\n"
                ansible_inventory += "\n"
        return ansible_inventory

    def write_ansible_vars(self, dstname=None):
        if dstname is None:
            dstname = "{}.vars.yml".format(self.name)
        with open(dstname, "w") as yamlfile:
            yamlfile.write(__WARNING__)
            yaml.dump(self.ansible_vars(),
                      yamlfile,
                      default_flow_style=False,
                      explicit_start=True)
        print("[+] Ansible vars written to {}".format(dstname))
        return dstname

    def write_ansible_inventory(self, dstname=None):
        if dstname is None:
            dstname = "{}.hosts".format(self.name)
        with open(dstname, "w") as dstfile:
            dstfile.write(__WARNING__)
            dstfile.write(self.ansible_inventory())
        print("[+] Ansible inventory written to {}".format(dstname))
        return dstname


def clean_and_exit(dir, exit_code):
    shutil.rmtree(dir, ignore_errors=True)
    sys.exit(exit_code)


if __name__ == "__main__":
    try:
        with open('irma-ansible.cfg', 'r') as f:
            type = f.readline().rstrip()
    except IOError:
        print('Missing configuration file')
        sys.exit(1)
    if type not in ('core', 'kiosk'):
        print('Bad configuration: ' + type)
        sys.exit(1)
    parser = argparse.ArgumentParser(description="Create ansible inventory, "
                                                 "vars file and run Ansible")
    parser.add_argument('-na', '--no-ansible',
                        action='store_true',
                        help='do not launch ansible-playbook')
    parser.add_argument('-ng', '--no-ansible-galaxy',
                        action='store_true',
                        help='do not launch ansible-galaxy')
    parser.add_argument('-nva', '--no-vars-address',
                        action='store_true',
                        help='do not generate ansible vars containing an '
                             'address')
    parser.add_argument('--offline',
                        action='store_true',
                        help='perform an offline installation')
    parser.add_argument('config_file', help='config_file (yaml format)')
    parser.add_argument('ansible_args', nargs=argparse.REMAINDER,
                        help='options for ansible-playbook <ansible_args>')

    options = parser.parse_args()
    conf = IrmaConfig(type, options.config_file, options.offline,
                      options.no_vars_address)
    tmpdir = tempfile.mkdtemp()
    ansible_vars_path = os.path.join(tmpdir, 'vars.yml')
    inventory_path = os.path.join(tmpdir, 'inventory')
    if (options.no_ansible_galaxy and options.no_ansible):
        ansible_vars_path = None
        inventory_path = None
    vars_file = conf.write_ansible_vars(
                    dstname=ansible_vars_path)
    inventory_file = conf.write_ansible_inventory(dstname=inventory_path)
    exit_code = 0

    if not options.no_ansible_galaxy:
        print("[+] launching ansible-galaxy")
        default_opts = ["ansible-galaxy"]
        default_opts += ["install"]
        default_opts += ["-r", "ansible-requirements.yml"]
        default_opts += ["--force"]
        galaxy_cli = GalaxyCLI(default_opts)
        galaxy_cli.parse()
        exit_code = galaxy_cli.run()
        if exit_code:
            clean_and_exit(tmpdir, exit_code)

    if not options.no_ansible:
        print("[+] launching ansible-playbook")
        default_opts = ["ansible-playbook"]
        default_opts += ["-i", "default_groups"]
        default_opts += ["-i", inventory_file]
        default_opts += ["-e", "@{}".format(vars_file)]
        if '-u' not in options.ansible_args:
            default_opts += ["-u", "vagrant"]
        if options.offline:
            default_opts += [
                "--module-path",
                "ansible_plugins/modules:offline/ansible_modules/offline"]
        ansible_args = default_opts + options.ansible_args
        play_cli = PlaybookCLI(ansible_args)
        play_cli.parse()
        exit_code = play_cli.run()

    clean_and_exit(tmpdir, exit_code)
