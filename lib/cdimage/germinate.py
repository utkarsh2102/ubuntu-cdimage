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
import re
import shutil
import subprocess
import traceback

from cdimage import osextras
from cdimage.log import logger
from cdimage.mail import send_mail
from cdimage.mirror import find_mirror
from cdimage.proxy import proxy_check_call

__metaclass__ = type


class GerminateNotInstalled(Exception):
    pass


class Germination:
    def __init__(self, config, prefer_vcs=True, apt_state_mgr=None):
        self.config = config
        # Set to False to use old-style seed checkouts.
        self.prefer_vcs = prefer_vcs
        self.apt_state_mgr = apt_state_mgr

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

    def output_dir(self):
        return os.path.join(
            self.config.root, "scratch", self.config.subtree,
            self.config.project, self.config.full_series,
            self.config.image_type, "germinate")

    def seed_sources(self):
        project = self.config.project
        if self.config["LOCAL_SEEDS"]:
            return [self.config["LOCAL_SEEDS"]]
        elif self.prefer_vcs:
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
        else:
            return ["http://people.canonical.com/~ubuntu-archive/seeds/"]

    @property
    def use_vcs(self):
        if self.config["LOCAL_SEEDS"]:
            # Local changes may well not be committed.
            return False
        else:
            return self.prefer_vcs

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


re_not_base = re.compile(
    r"^(linux-(image|restricted|amd64|386|686|k7|power|"
    r"imx51|dove|omap).*|"
    r"nvidia-kernel-common|grub|yaboot|efibootmgr|elilo|silo|palo)$")


class GerminateOutput:
    def __init__(self, config, directory):
        self.config = config
        self.directory = directory
        self.structure = os.path.join(directory, "STRUCTURE")
        self._parse_structure()

    def _parse_structure(self):
        self._seeds = []
        with open(self.structure) as structure:
            for line in structure:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                seed, inherit = line.split(":", 1)
                self._seeds.append(seed)

    def list_seeds(self, mode):
        project = self.config.project
        series = self.config["DIST"]

        if mode == "all":
            for seed in self._seeds:
                yield seed
        elif mode == "ship-live":
            if project == "lubuntu" and series == "bionic":
                yield "ship-live-gtk"
                yield "ship-live-share"
            elif project == "lubuntu-next" and series == "bionic":
                yield "ship-live-qt"
                yield "ship-live-share"
            elif project == "ubuntu-server" and series >= "bionic":
                yield "server-ship-live"
            elif project == "ubuntu" and self.config["SUBPROJECT"] == "canary":
                # ubuntu-desktop-installer
                yield "canary-ship-live"
                # TODO: we will probably need a legacy-ship-live seed
            else:
                yield "ship-live"
        elif mode == "dvd":
            if project == "ubuntu":
                # no inheritance; most of this goes on the live filesystem
                yield "usb-langsupport"
                yield "usb-ship-live"
            elif project == "ubuntustudio":
                # no inheritance; most of this goes on the live filesystem
                yield "dvd"
                if series >= "bionic":
                    yield "ship-live"
            else:
                raise Exception("unsupported configuration")

    def seed_path(self, arch, seed):
        return os.path.join(self.directory, arch, seed)

    def seed_packages(self, arch, seed):
        with open(self.seed_path(arch, seed)) as seed_file:
            lines = seed_file.read().splitlines()[2:-2]
            return [line.split(None, 1)[0] for line in lines]

    def master_seeds(self):
        if self.config["CDIMAGE_DVD"]:
            for seed in self.list_seeds("dvd"):
                if seed not in ("installer", "casper"):
                    yield seed
        else:
            if self.config.get("CDIMAGE_LIVE") == "1":
                for seed in self.list_seeds("ship-live"):
                    if seed not in ("installer", "casper"):
                        yield seed

    def master_task_entries(self):
        project = self.config.project
        series = self.config.series

        found = False
        for seed in self.master_seeds():
            yield "#include <%s/%s/%s>" % (project, series, seed)
            found = True

        if not found:
            raise NoMasterSeeds("No seeds found for master task!")

    def tasks_output_dir(self):
        return os.path.join(
            self.config.root, "scratch", self.config.subtree,
            self.config.project, self.config.full_series,
            self.config.image_type, "tasks")

    def write_tasks(self):
        output_dir = self.tasks_output_dir()
        osextras.mkemptydir(self.tasks_output_dir())

        for arch in self.config.arches:
            cpparch = arch.replace("+", "_").replace("-", "_")
            for seed in self.list_seeds("all"):
                seed_path = self.seed_path(arch, seed)
                if not os.path.exists(seed_path):
                    continue
                with open(os.path.join(output_dir, seed), "a") as task_file:
                    print("#ifdef ARCH_%s" % cpparch, file=task_file)
                    for package in sorted(self.seed_packages(arch, seed)):
                        print(package, file=task_file)
                    print("#endif /* ARCH_%s */" % cpparch, file=task_file)

            with open(os.path.join(output_dir, "MASTER"), "w") as master:
                for entry in self.master_task_entries():
                    print(entry, file=master)

    def diff_tasks(self, output=None):
        tasks_dir = self.tasks_output_dir()
        previous_tasks_dir = "%s-previous" % tasks_dir
        for seed in ["MASTER"] + list(self.list_seeds("all")):
            old = os.path.join(previous_tasks_dir, seed)
            new = os.path.join(tasks_dir, seed)
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

        task_recipients = []
        task_mail_path = os.path.join(self.config.root, "etc", "task-mail")
        if os.path.exists(task_mail_path):
            with open(task_mail_path) as task_mail:
                task_recipients = task_mail.read().split()
        if task_recipients:
            read, write = os.pipe()
            pid = os.fork()
            if pid == 0:  # child
                try:
                    os.close(read)
                    with os.fdopen(write, "w", 1) as write_file:
                        self.diff_tasks(output=write_file)
                    os._exit(0)
                except Exception:
                    traceback.print_exc()
                finally:
                    os._exit(1)
            else:  # parent
                os.close(write)
                with os.fdopen(read) as read_file:
                    send_mail(
                        "Task changes for %s %s/%s on %s" % (
                            self.config.capproject, self.config.image_type,
                            self.config.full_series, date),
                        "update-tasks", task_recipients, read_file)
                os.waitpid(pid, 0)

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
