#!/usr/bin/env python3
#
# Copyright (c) Juniper Networks, Inc. 2025. All rights reserved.

"""Create an ISO image that can be used to cloud-init a deployment jumper VM."""

import argparse
import pkgutil

from io import BytesIO
from pycdlib import PyCdlib

from lib import *


def load_file(filename):
    """Load file from .pyz"""
    data = b''
    try:
        data = pkgutil.get_data('resources', filename)
    except OSError:
        pass
    return data


def add_file(filename, facade):
    """Add file to ISO image using a facade"""
    path = '/' + filename
    data = load_file(filename)
    file_io = BytesIO(data)
    facade.add_fp(file_io, len(data), joliet_path=path)


def main():
    iso = PyCdlib()
    iso.new(vol_ident='cidata', joliet=3)
    facade = iso.get_joliet_facade()
    for filename in ('meta-data', 'user-data'):
        add_file(filename, facade)
    iso.write('generic-jumper.iso')
    iso.close()


if __name__ == '__main__':
    main()
