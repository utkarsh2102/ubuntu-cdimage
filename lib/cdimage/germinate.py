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

"""Germinate handling."""

from __future__ import print_function

import os
import shutil
import subprocess

from cdimage import osextras
from cdimage.log import logger
from cdimage.mirror import find_mirror
from cdimage.proxy import proxy_check_call

__metaclass__ = type


class GerminateNotInstalled(Exception):
    pass


class Germination:
    def __init__(self, config, apt_state_mgr=None):
        self.config = config
        self.apt_state_mgr = apt_state_mgr

    @property
    def germinate_path(self):
        path = os.path.join(self.config.root, "germinate", "bin", "germinate")
        if os.access(path, os.X_OK):
            return path
        else:
            raise GerminateNotInstalled(
                "Please check out lp:germinate in %s." %
                os.path.join(self.config.root, "germinate"))

    def output_dir(self):
        return os.path.join(
            self.config.root, "scratch", self.config.subtree,
            self.config.project, self.config.full_series,
            self.config.image_type, "germinate")

    def seed_sources(self):
        project = self.config.project
        if self.config["LOCAL_SEEDS"]:
            return [self.config["LOCAL_SEEDS"]]
        else:
            gitpattern = "https://git.launchpad.net/~%s/ubuntu-seeds/+git/"
            sources = [gitpattern % "ubuntu-core-dev"]
            if project == "kubuntu":
                sources.insert(0, gitpattern % "kubuntu-dev")
            elif project == "edubuntu":
                sources.insert(0, gitpattern % "edubuntu-dev")
            elif project == "ubuntustudio":
                sources.insert(0, gitpattern % "ubuntustudio-dev")
            elif project == "xubuntu":
                sources.insert(0, gitpattern % "xubuntu-dev")
            elif project in ("lubuntu", "lubuntu-next"):
                sources.insert(0, gitpattern % "lubuntu-dev")
            elif project == "ubuntu-gnome":
                sources.insert(0, gitpattern % "ubuntu-gnome-dev")
            elif project == "ubuntu-budgie":
                sources.insert(0, gitpattern % "ubuntubudgie-dev")
            elif project == "ubuntu-mate":
                sources.insert(0, gitpattern % "ubuntu-mate-dev")
            elif project == "ubuntu-unity":
                sources.insert(0, gitpattern % "unity7maintainers")
            elif project == "ubuntucinnamon":
                sources.insert(0, gitpattern % "ubuntucinnamon-dev")
            elif project == "ubuntukylin":
                sources.insert(0, gitpattern % "ubuntukylin-members")
            elif project == "ubuntu-oem":
                sources.insert(0, gitpattern % "oem-solutions-engineers")
            return sources

    @property
    def use_vcs(self):
        # Local changes may well not be committed.
        return not bool(self.config["LOCAL_SEEDS"])

    @property
    def germinate_dists(self):
        if self.config["GERMINATE_DISTS"]:
            return self.config["GERMINATE_DISTS"].split(",")
        else:
            dist_patterns = ["%s", "%s-security", "%s-updates"]
            if self.config.get("PROPOSED", "0") not in ("", "0"):
                dist_patterns.append("%s-proposed")
            return [pattern % self.config.series for pattern in dist_patterns]

    def seed_dist(self):
        project = self.config.project
        if project in ("ubuntu-server", "ubuntu-core-desktop"):
            return "ubuntu.%s" % self.config.series
        elif project == "ubuntukylin":
            return "ubuntukylin.%s" % self.config.series
        elif project == "lubuntu-next":
            return "lubuntu.%s" % self.config.series
        else:
            return "%s.%s" % (project, self.config.series)

    @property
    def components(self):
        yield "main"
        if not self.config["CDIMAGE_ONLYFREE"]:
            yield "restricted"
        if self.config["CDIMAGE_UNSUPPORTED"]:
            yield "universe"
            if not self.config["CDIMAGE_ONLYFREE"]:
                yield "multiverse"

    # TODO: convert to Germinate's native Python interface
    def germinate_arch(self, arch):
        cpuarch = arch.split("+")[0]

        arch_output_dir = os.path.join(self.output_dir(), arch)
        osextras.mkemptydir(arch_output_dir)
        if (self.config["GERMINATE_HINTS"] and
                os.path.isfile(self.config["GERMINATE_HINTS"])):
            shutil.copy2(
                self.config["GERMINATE_HINTS"],
                os.path.join(arch_output_dir, "hints"))
        command = [
            self.germinate_path,
            "--seed-source", ",".join(self.seed_sources()),
            "--seed-dist", self.seed_dist(),
            "--arch", cpuarch,
            "--no-rdepends",
        ]
        if self.apt_state_mgr is not None:
            command.extend([
                "--apt-config", self.apt_state_mgr.apt_conf_for_arch(cpuarch),
                ])
        else:
            command.extend([
                "--mirror", find_mirror(self.config.project, arch),
                "--components", ",".join(self.components),
                "--dist", ",".join(self.germinate_dists),
                ])
        if self.use_vcs:
            command.append("--vcs=git")
        proxy_check_call(
            self.config, "germinate", command, cwd=arch_output_dir,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        output_structure = os.path.join(self.output_dir(), "STRUCTURE")
        shutil.copy2(
            os.path.join(arch_output_dir, "structure"), output_structure)

    def run(self):
        osextras.mkemptydir(self.output_dir())

        for arch in self.config.arches:
            logger.info(
                "Germinating for %s/%s ..." % (self.config.series, arch))
            self.germinate_arch(arch)

    def output(self):
        return GerminateOutput(self.config, self.output_dir())


class NoMasterSeeds(Exception):
    pass


class GerminateOutput:
    def __init__(self, config, directory):
        self.config = config
        self.directory = directory

    def pool_seeds(self):
        if not self.config["CDIMAGE_LIVE"]:
            raise NoMasterSeeds("No seeds found for master task!")
        project = self.config.project

        if project == "ubuntustudio":
            if self.config.series <= "noble":
                yield "dvd"
            yield "ship-live"
        elif project == "ubuntu-server":
            yield "server-ship-live"
        elif project == "ubuntu" and self.config["SUBPROJECT"] == "canary":
            # ubuntu-desktop-installer
            yield "canary-ship-live"
            # TODO: will we need a legacy-ship-live seed?
        else:
            yield "ship-live"

    def seed_path(self, arch, seed):
        return os.path.join(self.directory, arch, seed)

    def seed_packages(self, arch, seed):
        try:
            seed_file = open(self.seed_path(arch, seed))
        except FileNotFoundError:
            return []
        with seed_file:
            lines = seed_file.read().splitlines()[2:-2]
            return [line.split(None, 1)[0] for line in lines]

    def tasks_output_dir(self):
        return os.path.join(
            self.config.root, "scratch", self.config.subtree,
            self.config.project, self.config.full_series,
            self.config.image_type, "tasks")

    def write_tasks(self):
        output_dir = self.tasks_output_dir()
        osextras.mkemptydir(output_dir)

        for arch in self.config.arches:
            arch_packages = set()
            for seed in self.pool_seeds():
                arch_packages.update(self.seed_packages(arch, seed))
            with open(os.path.join(output_dir, f"{arch}-packages"), "w") as fp:
                for package in sorted(arch_packages):
                    print(package, file=fp)

    def diff_tasks(self, output=None):
        tasks_dir = self.tasks_output_dir()
        previous_tasks_dir = "%s-previous" % tasks_dir
        filenames = []
        for arch in self.config.arches:
            filenames.append(f"{arch}-packages")

        for filename in filenames:
            old = os.path.join(previous_tasks_dir, filename)
            new = os.path.join(tasks_dir, filename)
            if os.path.exists(old) and os.path.exists(new):
                kwargs = {}
                if output is not None:
                    kwargs["stdout"] = output
                subprocess.call(["diff", "-u", old, new], **kwargs)

    def update_tasks(self, date):
        tasks_dir = self.tasks_output_dir()
        previous_tasks_dir = "%s-previous" % tasks_dir
        debian_cd_tasks_dir = os.path.join(
            self.config.root, "debian-cd", "tasks", "auto",
            self.config.image_type, self.config.project,
            self.config.full_series)

        self.diff_tasks()

        osextras.mkemptydir(debian_cd_tasks_dir)
        osextras.mkemptydir(previous_tasks_dir)
        for entry in os.listdir(tasks_dir):
            shutil.copy2(
                os.path.join(tasks_dir, entry),
                os.path.join(debian_cd_tasks_dir, entry))
            shutil.copy2(
                os.path.join(tasks_dir, entry),
                os.path.join(previous_tasks_dir, entry))
