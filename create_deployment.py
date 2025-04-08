#!/usr/bin/env python3

import argparse
import base64
import functools
import os
import proxmoxer
import requests
import sys
import time
import yaml

from lib.log import *

NETWORK_DELAY = 3


class ProxmoxNode(object):
    def __init__(self, host, user, password, pool=''):
        self.proxmox = proxmoxer.ProxmoxAPI(
            host=host,
            user=user,
            password=password,
            verify_ssl=True)
        node_name = self.proxmox.nodes.get()[0]['node']
        self.node = self.proxmox.nodes(node_name)
        self.pool = pool
        self.base_id = 0
        self.get_networks()

    def find_template_id(self, template_name):
        for vm in self.node.qemu.get():
            if template_name == vm['name']:
                return vm['vmid']
        error('ID could not be found for template name:', template_name)

    def exists(self, id):
        vm_id = self.base_id + id
        try:
            vm = self.node.qemu(vm_id).status.current.get()
            return True
        except proxmoxer.core.ResourceException:
            return False

    def get_name(self, id):
        vm_id = self.base_id + id
        vm = self.node.qemu(vm_id).status.current.get()
        return vm['name']

    def get_node_deployments(self):
        description = self.node.config.get()
        try:
            node_deployments = yaml.safe_load(description.get('description')).get('deployments')
        except AttributeError:
            node_deployments = []
        return node_deployments

    def set_node_deployments(self, node_deployments):
        data = yaml.dump({'deployments': node_deployments})
        self.node.config.set(description=data)

    def set_base_id(self, base_id):
        self.base_id = base_id

    def get_network_name(self, num):
        if type(num) == int:
            iface_number = self.base_id + num
            return f'vmbr{iface_number}'
        elif type(num) == str:
            if num.startswith('vmbr'):
                return num
        error('Unexpected type', __func__)

    def get_networks(self):
        networks = []
        for network in self.node.network.get():
            iface = network['iface']
            if iface.startswith('vmbr'):
                networks.append(iface)
        self.networks = networks

    def get_unbound_networks(self):
        from pprint import pprint
        bound_networks = set()
        for vm in self.node.qemu.get():
            for key, value in self.node.qemu(vm['vmid']).config().get().items():
                if key.startswith('net'):
                    for p in value.split(','):
                        if p.startswith('bridge='):
                            bound_networks.add(p.strip('bridge='))
        return set(self.networks) - bound_networks

    def has_network(self, network):
            return self.get_network_name(network) in self.networks

    def create_network(self, num, description):
        iface_name = self.get_network_name(num)
        info('Creating network:', iface_name)
        self.node.network.post(type='bridge', iface=iface_name, comments=description, autostart=1)
        # update internal networks dictionary
        self.get_networks()

    def delete_network(self, iface_name, dry_run_string):
        info('Deleting network:', iface_name, dry_run_string)
        if not dry_run_string:
            self.node.network(iface_name).delete()

    def commit_network_config(self):
        self.node.network().put()

    def clone(self, template_id, id):
        vm_id = self.base_id + id
        info('Cloning VM', vm_id)
        self.node.qemu(template_id).clone.create(newid=vm_id, pool=self.pool)

    def start(self, id):
        vm_id = self.base_id + id
        self.node.qemu(vm_id).status.start.post()

    def destroy(self, id):
        i = 0
        vm_id = self.base_id + id
        while i < 30 and self.node.qemu(vm_id).status.current.get().get('status') == 'running':
            if not i:
                info('Stopping VM', vm_id)
            self.node.qemu(vm_id).status.stop.post()
            time.sleep(1)
            i += 1
        self.node.qemu(vm_id).delete()

    def set_options(self, id, options):
        # vm_id = self.base_id + id
        # self.node.qemu(vm_id).config.set(**options)
        id = self.base_id + id
        del(options['vmid'])
        self.node.qemu(id).config.set(**options)


def parse_arguments():
    """Get commandline arguments."""
    parser = argparse.ArgumentParser('Deploy VMs on Proxmox')
    parser.add_argument('-c', '--config', default='config.yaml',
                        help='Configuration file name')
    parser.add_argument('-d', '--deployment',
                        help='Deployment description URL (yaml format)')
    parser.add_argument('-x', '--exclude', action='append', default=[],
                        help='Exclude virtual machines from being processed')
    parser.add_argument('-r', '--remove', action='store_true',
                        help='Remove virtual machines of a previously run ')
    parser.add_argument('--nic-type', default='virtio',
                        help='Type of virtual NICs')
    parser.add_argument('--range', help='range of VM IDs to process')
    parser.add_argument('--force-delete', action='store_true',
                        help='Delete existing VMs on deployment')
    parser.add_argument('--cleanup-networks', action='store_true',
                        help='Delete unbound networks on host')
    parser.add_argument('--force', '-f', action='store_true',
                        help='Force cleanup')
    parser.add_argument('--autostart', action='store_true',
                        help='Automatically start VMs after deployment')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug messages')
    parser.add_argument('--dry-run', action='store_true',
                        help='Do not create or remove virtual machines - just show task')
    parser.add_argument('vm', help='VM name', nargs='*')
    return parser.parse_args()


def load_config(filename):
    with open(filename) as fd:
        return yaml.safe_load(fd)


def load_deployment(url):
    r = requests.get(url)
    if r.status_code == 200:
        return yaml.safe_load(r.text)
    error('Cannot load deployment description from URL:', url)


def confirm(message, force=False):
    if force:
        return True
    yn = input(f'{message} [yN]? ')
    if yn == 'y' or yn == 'Y':
        return True
    return False


def create_vm(proxmox_node, vm, config, deployment, args):
    vm_name = vm['name']
    vm_id = vm['id']
    deployment_name = deployment['deployment']
    vm_options = deployment.get('global', {}).get('options', {}).copy()
    vm_options.update(vm.get('options', {}))
    vm_name = f"{deployment_name}-{vm_name}"
    vm_options['vmid'] = vm_id
    vm_options['name'] = vm_name
    if 'serial' in vm_options:
        serial = vm_options['serial'].format(
            name=vm_name,
            id=vm_id,
            deployment=deployment_name,
        )
        del(vm_options['serial'])
        vm_options['smbios1'] = 'serial={},base64=1'.format(
            base64.b64encode(bytes(serial, 'ascii')).decode('ascii'))

    # add networks
    i = 0
    key = 'net{}'
    while key.format(i) in vm_options:
        i += 1
    networks = deployment.get('global', {}).get('networks', []).copy()
    networks.extend(vm.get('networks', []))
    new_network = False
    for network in networks:
        network_name = proxmox_node.get_network_name(network)
        if not proxmox_node.has_network(network):
            if confirm(f'Network {network_name} does not exist. Create it', args.force):
                proxmox_node.create_network(network_name, f'Network is part of deployment {deployment_name}')
                new_network = True
            else:
                error('Exiting due to missing network.')
        already_added = False
        bridge = network_name.split(',')[0]
        for key, value in vm_options.items():
            if key.startswith('net') and f'bridge={bridge}' in value.split(','):
                already_added = True
                break
        if not already_added:
            vm_options[f'net{i}'] = f'{args.nic_type},bridge={bridge}'
        i += 1

    if new_network:
        proxmox_node.commit_network_config()
        info(f'Waiting {NETWORK_DELAY} seconds to bring up new networks...')
        time.sleep(NETWORK_DELAY)


    template_name = vm.get('template')
    if not template_name:
        error('No template name for VM provided:', vm_name)
    template_id = proxmox_node.find_template_id(template_name)
    if proxmox_node.exists(vm_id):
        if args.force_delete:
            old_name = proxmox_node.get_name(vm_id)
            if old_name == vm_name:
                proxmox_node.destroy(vm_id)
                time.sleep(2)
            else:
                error('VM names do not match. Do not delete old VM:', old_name)
        else:
            error(f'VM already exists: {vm_name} ({vm_id})')

    proxmox_node.clone(template_id, vm_id)
    time.sleep(1)
    debug('vm_options:', vm_options)
    proxmox_node.set_options(vm_id, vm_options)
    if args.autostart:
        proxmox_node.start(vm_id)


def main():
    args = parse_arguments()
    if not args.deployment:
        environment_url = os.environ.get('DEPLOYMENT_URL')
        if environment_url:
            args.deployment = environment_url
        else:
            error('Must specify --deployment or set DEPLOYMENT_URL in environment.')
    if args.debug:
        set_debug()
    config = load_config(args.config)
    deployment = load_deployment(args.deployment)
    dry_run_string = ''
    if args.dry_run:
        dry_run_string = '(dry-run)'
    vms = deployment.get('vms')

    try:
        proxmox_node = ProxmoxNode(
            config.get('hostname'),
            config.get('username'),
            config.get('password'),
            config.get('pool'))
    except requests.exceptions.JSONDecodeError:
        error('Cannot connect to Proxmox server', config.get('hostname'))

    if args.cleanup_networks:
        # loop through all unbound networks and remove if confirmed, finally commit network changes
        proxmox_node.get_networks()
        for network in proxmox_node.get_unbound_networks():
            if not args.force and not args.dry_run:
                if not confirm(f'Really delete network {network}'):
                    continue
            proxmox_node.delete_network(network, dry_run_string)
        proxmox_node.commit_network_config()
        return

    deployment_name = deployment['deployment']
    hostname = config.get('hostname')
    node_deployments = proxmox_node.get_node_deployments()
    try:
        node_deployments_dict = dict(functools.reduce(lambda a, b: {**a, **b}, node_deployments))
    except TypeError:
        # no persistent deployment data found in node's description field - let's initialize it
        node_deployments_dict = {}

    try:
        multiplier = node_deployments_dict[deployment_name]
    except KeyError:
        if args.remove:
            error(f'Deployment "{deployment_name}" was not implemented on host "{hostname}".')
        multiplier = len(node_deployments) + 1
        node_deployments.append({deployment_name: multiplier})
        proxmox_node.set_node_deployments(node_deployments)
    base_id = multiplier * 10000
    #print('base_id:', base_id)
    proxmox_node.set_base_id(base_id)

    # proxmox_node.create_network(111, 'foo')
    # return

    _range = []
    if args.range:
        _range = range(*[int(e) for e in args.range.split(',')])

    for vm in vms:
        vm_id = vm.get('id')
        vm_id_host = base_id + vm_id
        if _range and vm_id not in _range:
            continue

        vm_name = vm.get('name')
        if args.vm:
            if vm_name not in args.vm:
                continue
        if vm_name in args.exclude:
            continue
        if args.remove:
            info(f'Removing VM: {vm_name} (id: {vm_id_host})', dry_run_string)
            if not args.dry_run:
                proxmox_node.destroy(vm_id)
        else:
            info('Creating VM:', vm_name, dry_run_string)
            if not args.dry_run:
                create_vm(proxmox_node, vm, config, deployment, args)


if __name__ == '__main__':
    main()
