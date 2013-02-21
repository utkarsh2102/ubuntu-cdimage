# Copyright (C) 2013 Canonical Ltd.
# Author: Colin Watson <cjwatson@ubuntu.com>

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

"""Image building."""

from __future__ import print_function

__metaclass__ = type

import os
import subprocess

from cdimage import osextras


def _dpkg_field(path, field):
    return subprocess.check_output(
        ["dpkg", "-f", path, field], universal_newlines=True).rstrip("\n")


def _find_endswith(path, suffixes):
    for dirpath, _, filenames in os.walk(path):
        for filename in filenames:
            for suffix in suffixes:
                if filename.endswith(suffix):
                    yield dirpath, filename
                    break


def update_local_indices(config):
    packages = os.path.join(config.root, "local", "packages")
    if not os.path.isdir(packages):
        return

    database = os.path.normpath(os.path.join(packages, os.pardir, "database"))
    dists = os.path.join(database, "dists")
    indices = os.path.join(database, "indices")
    pool = os.path.join(packages, "pool", "local")
    osextras.ensuredir(dists)
    osextras.ensuredir(indices)

    for arch in config.cpuarches:
        binary_list_path = os.path.join(
            dists, "%s_local_binary-%s.list" % (config.series, arch))
        di_binary_list_path = os.path.join(
            dists, "%s_local_debian-installer_binary-%s.list" % (
                config.series, arch))
        override_path = os.path.join(
            indices, "override.%s.local.%s" % (config.series, arch))
        di_override_path = os.path.join(
            indices, "override.%s.local.debian-installer.%s" % (
                config.series, arch))

        with open(binary_list_path, "w") as binary_list, \
                open(di_binary_list_path, "w") as di_binary_list, \
                open(override_path, "w") as override, \
                open(di_override_path, "w") as di_override:
            for dirpath, deb in _find_endswith(
                    pool, ["_%s.deb" % arch, "_all.deb"]):
                deb_path = os.path.join(dirpath, deb)
                print(os.path.relpath(deb_path, packages), file=binary_list)
                name = deb.split("_", 1)[0]
                section = _dpkg_field(deb_path, "Section").split("/")[-1]
                priority = _dpkg_field(deb_path, "Priority")
                print(
                    "%s\t%s\tlocal/%s" % (name, priority, section),
                    file=override)

            for dirpath, udeb in _find_endswith(
                    pool, ["_%s.udeb" % arch, "_all.udeb"]):
                udeb_path = os.path.join(dirpath, udeb)
                print(
                    os.path.relpath(udeb_path, packages), file=di_binary_list)
                name = udeb.split("_", 1)[0]
                priority = _dpkg_field(udeb_path, "Priority")
                print(
                    "%s\t%s\tlocal/debian-installer" % (name, priority),
                    file=di_override)

        osextras.ensuredir(os.path.join(
            packages, "dists", config.series, "local", "binary-%s" % arch))
        osextras.ensuredir(os.path.join(
            packages, "dists", config.series, "local", "debian-installer",
            "binary-%s" % arch))

    subprocess.check_call(
        ["apt-ftparchive", "generate", "apt-ftparchive.conf"], cwd=packages)
