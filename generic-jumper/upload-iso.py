#!/usr/bin/env python3

import argparse
import proxmoxer
import yaml

class ProxmoxNode(object):
    def __init__(self, host, user, password, pool=''):
        self.proxmox = proxmoxer.ProxmoxAPI(
            host=host,
            user=user,
            password=password,
            verify_ssl=True)
        node_name = self.proxmox.nodes.get()[0]['node']
        self.node = self.proxmox.nodes(node_name)

    def upload_iso(self, filename):
        with open(filename, 'rb') as fd:
            self.node.storage('local').upload.post(content='iso', filename=fd)


def parse_arguments():
    """Get commandline arguments."""
    parser = argparse.ArgumentParser('Upload ISO image to Proxmox')
    parser.add_argument('-c', '--config', default='config.yaml',
                        help='Configuration file name')
    parser.add_argument('-f', '--filename', required=True,
                        help='ISO file name')
    return parser.parse_args()


def load_config(filename):
    with open(filename) as fd:
        return yaml.safe_load(fd)


def main():
    args = parse_arguments()
    config = load_config(args.config)

    try:
        proxmox_node = ProxmoxNode(
            config.get('hostname'),
            config.get('username'),
            config.get('password')
        )
    except requests.exceptions.JSONDecodeError:
        error('Cannot connect to Proxmox server', config.get('hostname'))

    proxmox_node.upload_iso(args.filename)


if __name__ == '__main__':
    main()
