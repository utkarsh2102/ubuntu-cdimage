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

import errno
import os
import subprocess

from cdimage.log import logger
from cdimage import osextras

__metaclass__ = type


def find_mirror(config, arch):
    return 'http://ftpmaster.internal/ubuntu/'


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
    if config["TRIGGER_MIRRORS"]:
        return config["TRIGGER_MIRRORS"].split()
    else:
        return _trigger_mirrors_production_config(config, "sync")


def _get_mirrors_async(config):
    if config["TRIGGER_MIRRORS_ASYNC"]:
        return config["TRIGGER_MIRRORS_ASYNC"].split()
    else:
        return _trigger_mirrors_production_config(config, "async")


def _trigger_command(config):
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
    paths = [
        os.path.join(config.root, "production", "STOP_SYNC_MIRRORS"),
        os.path.join(config.root, "etc", "STOP_SYNC_MIRRORS"),
    ]
    for path in paths:
        if os.path.exists(path):
            return

    check_manifest(config)

    key = _get_mirror_key(config)

    for host in _get_mirrors(config):
        _trigger_mirror(config, key, "archvsync", host)

    for host in _get_mirrors_async(config):
        _trigger_mirror(config, key, "archvsync", host, background=True)


APT_CONF_TMPL = """\
Apt {{
   Architecture "{ARCH}";
   Architectures "{OTHERARCH}";
}};

Dir "{DIR}";
Dir::state::status "/dev/null";
"""

SOURCES_TMPL = """\
Types: deb deb-src
URIs: {MIRROR}
Suites: {SUITES}
Components: {COMPONENTS}
Signed-By: {KEYRING}
"""


class AptStateManager:
    def __init__(self, config):
        self.config = config
        self._apt_conf_per_arch = {}

    def _output_dir(self, arch):
        return os.path.join(
            self.config.root, "scratch", self.config.subtree,
            self.config.project, self.config.full_series,
            self.config.image_type, "apt-state", arch)

    def _otherarch(self, arch):
        # XXX should probably move this knowledge into ubuntu-cdimage
        # once debian-cd no longer cares.
        debian_cd_dir = os.path.join(self.config.root, "debian-cd")
        archlist_file = os.path.join(
            debian_cd_dir, "data", self.config.series, "multiarch", arch)
        if os.path.exists(archlist_file):
            with open(archlist_file) as f:
                return f.read().strip()
        return arch

    def _components(self):
        components = ["main"]
        if not self.config["CDIMAGE_ONLYFREE"]:
            components.append("restricted")
        if self.config["CDIMAGE_UNSUPPORTED"]:
            components.append("universe")
            if not self.config["CDIMAGE_ONLYFREE"]:
                components.append("multiverse")
        return " ".join(components)

    def _suites(self):
        suite_patterns = ["%s", "%s-security", "%s-updates"]
        if self.config.get("PROPOSED", "0") not in ("", "0"):
            suite_patterns.append("%s-proposed")
        return " ".join(
            [pattern % self.config.series for pattern in suite_patterns])

    def _get_sources_text(self, arch):
        keyring = "/etc/apt/trusted.gpg.d/ubuntu-keyring-2018-archive.gpg"

        return SOURCES_TMPL.format(
            MIRROR=find_mirror(self.config, arch),
            SUITES=self._suites(),
            COMPONENTS=self._components(),
            KEYRING=keyring)

    def _setup_arch(self, arch):
        state_dir = self._output_dir(arch)
        osextras.mkemptydir(state_dir)

        conf_path = os.path.join(state_dir, 'base.conf')
        with open(conf_path, 'w') as conf:
            conf.write(APT_CONF_TMPL.format(
                ARCH=arch,
                OTHERARCH=self._otherarch(arch),
                DIR=state_dir))

        needed_dirs = [
            'etc/apt/sources.list.d',
            'etc/apt/apt.conf.d',
            'etc/apt/preferences.d',
            'var/lib/apt/lists/partial',
            ]

        for path in needed_dirs:
            osextras.mkemptydir(os.path.join(state_dir, path))

        sources_path = os.path.join(
            state_dir, 'etc/apt/sources.list.d/default.sources')

        with open(sources_path, 'w') as sources:
            sources.write(self._get_sources_text(arch))

        # XXX set up apt proxy here?

        subprocess.check_call(
            ['apt-get', 'update'],
            env=dict(os.environ, APT_CONFIG=conf_path))

        return conf_path

    def setup(self):
        for arch in self.config.arches:
            logger.info(
                "Setting up apt state for %s/%s ...",
                self.config.series, arch)
            self._apt_conf_per_arch[arch] = self._setup_arch(arch)

    def apt_conf_for_arch(self, arch):
        return self._apt_conf_per_arch[arch]
