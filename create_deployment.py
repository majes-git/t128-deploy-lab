#!/usr/bin/env python3

import argparse
import base64
import proxmoxer
import requests
import sys
import time
import yaml

from lib.log import *


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

    def find_template_id(self, template_name):
        for vm in self.node.qemu.get():
            if template_name == vm['name']:
                return vm['vmid']
        error('ID could not be found for template name:', template_name)

    def exists(self, id):
        try:
            vm = self.node.qemu(id).status.current.get()
            return True
        except proxmoxer.core.ResourceException:
            return False

    def get_name(self, id):
        vm = self.node.qemu(id).status.current.get()
        return vm['name']

    def clone(self, template_id, vm_id):
        info('Cloning VM', id)
        self.node.qemu(template_id).clone.create(newid=vm_id, pool=self.pool)

    def start(self, id):
        self.node.qemu(id).status.start.post()

    def destroy(self, id):
        i = 0
        while i < 30 and self.node.qemu(id).status.current.get().get('status') == 'running':
            if not i:
                info('Stopping VM', id)
            self.node.qemu(id).status.stop.post()
            time.sleep(1)
            i += 1
        self.node.qemu(id).delete()

    def set_options(self, id, options):
        self.node.qemu(id).config.set(**options)


def parse_arguments():
    """Get commandline arguments."""
    parser = argparse.ArgumentParser('Deploy VMs on Proxmox')
    parser.add_argument('-c', '--config', default='config.yaml',
                        help='Configuration file name')
    parser.add_argument('-d', '--deployment', required=True,
                        help='Deployment description URL (yaml format)')
    parser.add_argument('-x', '--exclude', action='append', default=[],
                        help='Exclude virtual machines from being processed')
    parser.add_argument('-r', '--remove', action='store_true',
                        help='Remove virtual machines of a previously run ')
    parser.add_argument('--nic-type', default='virtio',
                        help='Type of virtual NICs')
    parser.add_argument('--force-delete', action='store_true',
                        help='Delete existing VMs on deployment')
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


def create_vm(proxmox_node, vm, config, deployment, args):
    vm_name = vm['name']
    vm_id = vm['id']
    vm_options = deployment.get('global', {}).get('options', {}).copy()
    vm_options.update(vm.get('options', {}))
    vm_name = f"{deployment['deployment']}-{vm_name}"
    vm_options['vmid'] = vm_id
    vm_options['name'] = vm_name
    if 'serial' in vm_options:
        serial = vm_options['serial'].format(
            name=vm_name,
            id=vm_id,
            deployment=deployment['deployment'],
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
    for network in networks:
        already_added = False
        bridge = network.split(',')[0]
        for key, value in vm_options.items():
            if key.startswith('net') and f'bridge={bridge}' in value.split(','):
                already_added = True
                break
        if not already_added:
            vm_options[f'net{i}'] = f'{args.nic_type},bridge={bridge}'
        i += 1

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

    for vm in vms:
        vm_name = vm.get('name')
        vm_id = vm.get('id')
        if args.vm:
            if vm_name not in args.vm:
                continue
        if vm_name in args.exclude:
            continue
        if args.remove:
            info(f'Removing VM: {vm_name} (id: {vm_id})', dry_run_string)
            if not args.dry_run:
                proxmox_node.destroy(vm_id)
        else:
            info('Creating VM:', vm_name, dry_run_string)
            if not args.dry_run:
                create_vm(proxmox_node, vm, config, deployment, args)


if __name__ == '__main__':
    main()
