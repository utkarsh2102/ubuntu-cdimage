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

__metaclass__ = type

import gzip
import os
import shutil
import subprocess

from cdimage import osextras
from cdimage.log import logger
from cdimage.mirror import find_mirror


class GerminateNotInstalled(Exception):
    pass


class Germination:
    def __init__(self, config, prefer_bzr=True):
        self.config = config
        # Set to False to use old-style seed checkouts.
        self.prefer_bzr = prefer_bzr

    @property
    def germinate_path(self):
        paths = [
            os.path.join(self.config.root, "germinate", "bin", "germinate"),
            os.path.join(self.config.root, "germinate", "germinate.py"),
        ]
        for path in paths:
            if os.access(path, os.X_OK):
                return path
        else:
            raise GerminateNotInstalled(
                "Please check out lp:germinate in %s." %
                os.path.join(self.config.root, "germinate"))

    def output_dir(self, project):
        return os.path.join(
            self.config.root, "scratch", project, self.config.series,
            self.config["IMAGE_TYPE"], "germinate")

    def seed_sources(self, project):
        if self.config["LOCAL_SEEDS"]:
            return [self.config["LOCAL_SEEDS"]]
        elif self.prefer_bzr:
            pattern = "http://bazaar.launchpad.net/~%s/ubuntu-seeds/"
            series = self.config["DIST"]
            sources = [pattern % "ubuntu-core-dev"]
            if project in ("kubuntu", "kubuntu-active"):
                if series >= "oneiric":
                    sources.insert(0, pattern % "kubuntu-dev")
            elif project == "ubuntustudio":
                sources.insert(0, pattern % "ubuntustudio-dev")
            elif project == "mythbuntu":
                sources.insert(0, pattern % "mythbuntu-dev")
            elif project == "xubuntu":
                if series >= "intrepid":
                    sources.insert(0, pattern % "xubuntu-dev")
            elif project == "lubuntu":
                sources.insert(0, pattern % "lubuntu-dev")
            return sources
        else:
            return ["http://people.canonical.com/~ubuntu-archive/seeds/"]

    @property
    def use_bzr(self):
        if self.config["LOCAL_SEEDS"]:
            # Local changes may well not be committed.
            return False
        else:
            return self.prefer_bzr

    def make_index(self, project, arch, rel_target, rel_paths):
        target = os.path.join(self.output_dir(project), rel_target)
        osextras.mkemptydir(os.path.dirname(target))
        with gzip.GzipFile(target, "wb") as target_file:
            for rel_path in rel_paths:
                if os.path.isabs(rel_path):
                    abs_path = rel_path
                else:
                    abs_path = os.path.join(
                        find_mirror(self.config, arch), rel_path)
                if os.path.isfile(abs_path):
                    with gzip.GzipFile(abs_path, "rb") as source_file:
                        target_file.write(source_file.read())

    @property
    def germinate_dists(self):
        if self.config["GERMINATE_DISTS"]:
            return self.config["GERMINATE_DISTS"].split(",")
        else:
            dist_patterns = ["%s", "%s-security", "%s-updates"]
            if self.config.series == "precise":
                dist_patterns.append("%s-proposed")
            return [pattern % self.config.series for pattern in dist_patterns]

    def seed_dist(self, project):
        if project == "ubuntu-server" and self.config.series != "breezy":
            return "ubuntu.%s" % self.config.series
        elif project == "ubuntu-netbook":
            return "netbook.%s" % self.config.series
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
    def germinate_arch(self, project, arch):
        cpuarch = arch.split("+")[0]

        for dist in self.germinate_dists:
            for suffix in (
                "binary-%s/Packages.gz" % cpuarch,
                "source/Sources.gz",
                "debian-installer/binary-%s/Packages.gz" % cpuarch,
            ):
                files = [
                    "dists/%s/%s/%s" % (dist, component, suffix)
                    for component in self.components]
                if self.config["LOCAL"]:
                    files.append(
                        "%s/dists/%s/local/%s" %
                        (self.config["LOCALDEBS"], dist, suffix))
                self.make_index(project, arch, files[0], files)

        arch_output_dir = os.path.join(self.output_dir(project), arch)
        osextras.mkemptydir(arch_output_dir)
        if (self.config["GERMINATE_HINTS"] and
                os.path.isfile(self.config["GERMINATE_HINTS"])):
            shutil.copy2(
                self.config["GERMINATE_HINTS"],
                os.path.join(arch_output_dir, "hints"))
        command = [
            self.germinate_path,
            "--seed-source", ",".join(self.seed_sources(project)),
            "--mirror", "file://%s/" % self.output_dir(project),
            "--seed-dist", self.seed_dist(project),
            "--dist", ",".join(self.germinate_dists),
            "--arch", cpuarch,
            "--components", "main",
            "--no-rdepends",
        ]
        if self.use_bzr:
            command.append("--bzr")
        subprocess.check_call(command, cwd=arch_output_dir)
        output_structure = os.path.join(self.output_dir(project), "STRUCTURE")
        shutil.copy2(
            os.path.join(arch_output_dir, "structure"), output_structure)

        if self.config.series == "breezy":
            # Unfortunately, we now need a second germinate run to figure
            # out the dependencies of language packs and the like.
            extras = []
            with open(os.path.join(
                    arch_output_dir, "ship.acsets"), "w") as ship_acsets:
                output = GerminateOutput(self.config, output_structure)
                for pkg in output.seed_packages(arch, "ship.seed"):
                    extras.append("desktop/%s" % pkg)
                    print(pkg, file=ship_acsets)
            if extras:
                logger.info(
                    "Re-germinating for %s/%s language pack dependencies ..." %
                    (self.config.series, arch))
                command.extend(["--seed-packages", ",".join(extras)])
                subprocess.check_call(command, cwd=arch_output_dir)

    def germinate_project(self, project):
        osextras.mkemptydir(self.output_dir(project))

        for arch in self.config.arches:
            logger.info(
                "Germinating for %s/%s ..." % (self.config.series, arch))
            self.germinate_arch(project, arch)

    def run(self):
        if self.config["IMAGE_TYPE"] == "source":
            for project in self.config.all_projects:
                self.germinate_project(project)
        else:
            self.germinate_project(self.config["PROJECT"])


class GerminateOutput:
    def __init__(self, config, structure):
        self.config = config
        self.structure = structure
        self._parse_structure()

    def _parse_structure(self):
        self._seeds = {}
        # TODO: move to collections.OrderedDict with 2.7
        self._seed_order = []
        with open(self.structure) as structure:
            for line in structure:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                seed, inherit = line.split(":", 1)
                self._seeds[seed] = inherit.split()
                self._seed_order.append(seed)

    def _expand_inheritance(self, seed, inherit):
        for s in self._seeds.get(seed, ()):
            self._expand_inheritance(s, inherit)
        if seed not in inherit:
            inherit.append(seed)

    def _inheritance(self, seed):
        inherit = []
        self._expand_inheritance(seed, inherit)
        return inherit

    def _without_inheritance(self, subtract, seeds):
        subtract_inherit = self._inheritance(subtract)
        remaining = set(seeds) - set(subtract_inherit)
        return [seed for seed in seeds if seed in remaining]

    def list_seeds(self, mode):
        project = self.config["PROJECT"]
        series = self.config["DIST"]

        if mode == "all":
            for seed in self._seed_order:
                yield seed
        elif mode == "tasks":
            ship = "ship"
            if "ship-addon" in self._seeds:
                ship = "ship-addon"
            if project == "ubuntu-server":
                if series <= "breezy":
                    pass
                elif series <= "dapper":
                    ship = "server"
                else:
                    ship = "server-ship"
            elif project == "kubuntu-active":
                ship = "active-ship"
            for seed in self._inheritance(ship):
                yield seed
            if self.config["CDIMAGE_DVD"]:
                if series >= "edgy":
                    # TODO cjwatson 2007-04-18: hideous hack to fix DVD tasks
                    yield "dns-server"
                    yield "lamp-server"
        elif mode == "installer":
            if self.config["CDIMAGE_INSTALL_BASE"]:
                yield "installer"
            if self.config["CDIMAGE_LIVE"]:
                if series >= "hoary" and series <= "breezy":
                    yield "casper"
        elif mode == "debootstrap":
            if series <= "hoary":
                yield "base"
            elif series <= "feisty":
                yield "minimal"
            else:
                yield "required"
                yield "minimal"
        elif mode == "base":
            if series <= "hoary":
                yield "base"
            elif series <= "breezy":
                yield "minimal"
                yield "standard"
            elif series <= "feisty":
                yield "boot"
                yield "minimal"
                yield "standard"
            else:
                yield "boot"
                yield "required"
                yield "minimal"
                yield "standard"
        elif mode == "ship-live":
            if project == "kubuntu-active":
                yield "ship-active-live"
            elif project == "ubuntu-server":
                seeds = self._inheritance("server-ship")
                seeds = self._without_inheritance("minimal", seeds)
                for seed in seeds:
                    yield seed
            else:
                if series >= "dapper":
                    yield "ship-live"
        elif mode == "addon":
            ship = self._inheritance("ship")
            ship_addon = self._inheritance("ship-addon")
            for seed in ship_addon:
                if seed not in ship:
                    yield seed
        elif mode == "dvd":
            if series <= "gutsy":
                for seed in self._inheritance("supported"):
                    yield seed
            elif series <= "karmic":
                for seed in self._inheritance("dvd"):
                    yield seed
            else:
                if project == "edubuntu":
                    # no inheritance; most of this goes on the live filesystem
                    yield "dvd"
                    yield "ship-live"
                elif project == "ubuntu" and series >= "oneiric":
                    # no inheritance; most of this goes on the live filesystem
                    yield "usb-langsupport"
                    yield "usb-ship-live"
                elif project == "ubuntustudio" and series >= "precise":
                    # no inheritance; most of this goes on the live filesystem
                    yield "dvd"
                else:
                    for seed in self._inheritance("dvd"):
                        yield seed

    def seed_packages(self, arch, seed):
        with open(os.path.join(
                os.path.dirname(self.structure), arch, seed)) as seed_file:
            lines = seed_file.read().splitlines()[2:-2]
            return [line.split(None, 1)[0] for line in lines]
