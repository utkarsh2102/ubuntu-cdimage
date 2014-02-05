# Copyright (C) 2012 Canonical Ltd.
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

"""Mirror handling."""

from __future__ import print_function

__metaclass__ = type

import errno
import os
import subprocess

from cdimage.log import logger


def find_mirror(config, arch):
    return os.path.join(config.root, "ftp")


class UnknownManifestFile(Exception):
    pass


def check_manifest(config):
    # Check for non-existent files in .manifest.
    simple_tree = os.path.join(config.root, "www", "simple")
    try:
        with open(os.path.join(simple_tree, ".manifest")) as manifest:
            for line in manifest:
                name = line.rstrip("\n").split()[2]
                path = os.path.join(simple_tree, name.lstrip("/"))
                if not os.path.exists(path):
                    raise UnknownManifestFile(
                        ".manifest has non-existent file %s" % name)
    except IOError as e:
        if e.errno != errno.ENOENT:
            raise


def _get_mirror_key(config):
    secret = os.path.join(config.root, "secret")
    home_secret = os.path.expanduser("~/secret")
    if os.path.isdir(home_secret):
        secret = home_secret
    if config["UBUNTU_DEFAULTS_LOCALE"] == "zh_CN":
        base = "id-china-images"
    else:
        base = "auckland"
    return os.path.join(secret, base)


def _trigger_mirrors_production_config(config, trigger_type):
    path = os.path.join(config.root, "production", "trigger-mirrors")
    mirrors = []
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if line.startswith("#"):
                    continue
                words = line.split()
                if words and words[0] == trigger_type:
                    mirrors.extend(words[1:])
    return mirrors


def _get_mirrors(config):
    if config["UBUNTU_DEFAULTS_LOCALE"] == "zh_CN":
        return ["strix.canonical.com"]
    elif config["TRIGGER_MIRRORS"]:
        return config["TRIGGER_MIRRORS"].split()
    else:
        return _trigger_mirrors_production_config(config, "sync")


def _get_mirrors_async(config):
    if config["UBUNTU_DEFAULTS_LOCALE"] == "zh_CN":
        return []
    elif config["TRIGGER_MIRRORS_ASYNC"]:
        return config["TRIGGER_MIRRORS_ASYNC"].split()
    else:
        return _trigger_mirrors_production_config(config, "async")


def _trigger_command(config):
    if config["UBUNTU_DEFAULTS_LOCALE"] == "zh_CN":
        return "./china-sync"
    else:
        return "./releases-sync"


def _trigger_mirror(config, key, user, host, background=False):
    logger.info("%s:" % host)
    command = [
        "ssh", "-i", key,
        "-o", "StrictHostKeyChecking no",
        "-o", "BatchMode yes",
        "%s@%s" % (user, host),
        _trigger_command(config),
    ]
    if background:
        subprocess.Popen(command)
    else:
        subprocess.call(command)


def trigger_mirrors(config):
    check_manifest(config)

    key = _get_mirror_key(config)

    for host in _get_mirrors(config):
        _trigger_mirror(config, key, "archvsync", host)

    for host in _get_mirrors_async(config):
        _trigger_mirror(config, key, "archvsync", host, background=True)
