# -*- coding: utf-8 -*-

# Copyright (C) 2023 Canonical Ltd.
# Author: Łukasz 'sil2100' Zemczak <lukasz.zemczak@ubuntu.com>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Additional image metadata generation module."""

import datetime
import os
import re
import tarfile
import tempfile
import json


lxd_arch_map = {
    "i386": "i686",
    "amd64": "x86_64",
    "armhf": "armv7l",
    "arm64": "aarch64",
    "powerpc": "ppc",
    "ppc64el": "ppc64le",
    "riscv64": "riscv64",
    "s390x": "s390x",
}


def arch_to_lxd_arch(arch):
    arch = arch.split('+')[0]
    return lxd_arch_map.get(arch, arch)


def lxd_metadata_from_assertion(assertion_path):
    """Return an LXD metadata dictionary generated from a model assertion."""
    # Prepare data for the LXD metadata.yaml
    with open(assertion_path, 'r') as f:
        model_assertion_data = f.read()
    model_description_match = re.search(
        r'display-name: (.*)', model_assertion_data)
    if model_description_match:
        model_description = model_description_match.group(1)
    else:
        # If there's no description, use the model name
        model_description = re.search(
            r'model: (.*)', model_assertion_data).group(1)
    model_arch = re.search(
        r'architecture: (.*)', model_assertion_data).group(1)
    lxd_arch = arch_to_lxd_arch(model_arch)
    model_series = re.search(
        r'base: (.*)', model_assertion_data).group(1)
    timestamp = int(datetime.datetime.now().timestamp())

    metadata_data = {
        "architecture": lxd_arch,
        "creation_date": timestamp,
        "properties": {
            "architecture": model_arch,
            "description": model_description,
            "os": "Ubuntu",
            "series": model_series,
        },
    }
    return metadata_data


def generate_ubuntu_core_image_lxd_metadata(image_path):
    """Generate LXD metadata for the given Ubuntu Core image."""
    if not image_path.endswith('.img.xz'):
        raise Exception("Invalid Ubuntu Core path provided for lxd "
                        "metadata generation")
    basename = image_path[:-7]
    model_assertion = "%s.model-assertion" % basename
    metadata_path = "%s.lxd.tar.xz" % basename
    if not os.path.exists(model_assertion):
        raise Exception("Missing model assertion for Ubuntu Core image")
    metadata_data = lxd_metadata_from_assertion(model_assertion)
    # Generate the metadata.yaml and the tarball
    with tempfile.TemporaryDirectory() as tmpdir:
        metadata_yaml = os.path.join(tmpdir, 'metadata.yaml')
        with open(metadata_yaml, 'w') as f:
            f.write(json.dumps(metadata_data))
        with tarfile.open(metadata_path, 'w:xz') as outf:
            outf.add(metadata_yaml, arcname='metadata.yaml')
