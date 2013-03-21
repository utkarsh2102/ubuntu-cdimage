# Copyright (C) 2012, 2013 Canonical Ltd.
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

"""Image publication trees."""

from __future__ import print_function

__metaclass__ = type

import errno
from itertools import count
from optparse import OptionParser
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import traceback

from cdimage.atomicfile import AtomicFile
from cdimage.checksums import (
    ChecksumFileSet,
    checksum_directory,
    metalink_checksum_directory,
)
from cdimage.config import Series
from cdimage.log import logger, reset_logging
from cdimage import osextras
from cdimage.project import setenv_for_project


# TODO: This should be in a configuration file.  ALL_PROJECTS is not
# currently suitable, because it only lists projects currently being built,
# but manifest generation needs to know about anything currently in a
# published tree.
projects = [
    "edubuntu",
    "gobuntu",
    "jeos",
    "kubuntu",
    "kubuntu-active",
    "kubuntu-netbook",
    "lubuntu",
    "mythbuntu",
    "ubuntu",
    "ubuntu-gnome",
    "ubuntu-headless",
    "ubuntu-netbook",
    "ubuntu-server",
    "ubuntukylin",
    "ubuntustudio",
    "xubuntu",
]


def zsyncmake(infile, outfile, url):
    command = ["zsyncmake"]
    if infile.endswith(".gz"):
        command.append("-Z")
    command.extend(["-o", outfile, "-u", url, infile])
    if subprocess.call(command) != 0:
        logger.info("Trying again with block size 2048 ...")
        command[1:1] = ["-b", "2048"]
        subprocess.check_call(command)


class Tree:
    """A publication tree."""

    @staticmethod
    def get_daily(config, directory=None):
        if config["UBUNTU_DEFAULTS_LOCALE"] == "zh_CN":
            cls = ChinaDailyTree
        else:
            cls = DailyTree
        return cls(config, directory=directory)

    def __init__(self, config, directory):
        self.config = config
        self.directory = directory

    def path_to_project(self, path):
        """Determine the project for a file based on its tree-relative path."""
        first_dir = path.split("/")[0]
        if first_dir in projects:
            return first_dir
        else:
            return "ubuntu"

    def name_to_series(self, name):
        """Return the series for a file basename."""
        raise NotImplementedError

    @property
    def site_name(self):
        """Return the public host name corresponding to this tree."""
        raise NotImplementedError

    def path_to_manifest(self, path):
        """Return a manifest file entry for a tree-relative path.

        May raise ValueError for unrecognised file naming schemes.
        """
        if path.startswith("tocd"):
            return None
        project = self.path_to_project(path)
        base = os.path.basename(path)
        try:
            series = self.name_to_series(base)
        except ValueError:
            return None
        size = os.stat(os.path.join(self.directory, path)).st_size
        return "%s\t%s\t/%s\t%d" % (project, series, path, size)

    def manifest_file_allowed(self, path):
        """Return true if a given file is allowed in the manifest."""
        if (path.endswith(".iso") or path.endswith(".img") or
                path.endswith(".img.gz") or path.endswith(".tar.gz") or
                path.endswith(".tar.xz")):
            if stat.S_ISREG(os.stat(path).st_mode):
                return True
        return False

    def manifest_files(self):
        """Yield all the files to include in a manifest of this tree."""
        raise NotImplementedError

    def manifest(self):
        """Return a manifest of this tree as a sequence of lines."""
        return sorted(filter(
            lambda line: line is not None,
            (self.path_to_manifest(path) for path in self.manifest_files())))

    @staticmethod
    def mark_current_trigger(config, args=None):
        if not args:
            args = config["SSH_ORIGINAL_COMMAND"].split()
        if not args:
            return

        log_path = os.path.join(config.root, "log", "mark-current.log")
        osextras.ensuredir(os.path.dirname(log_path))
        log = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
        os.dup2(log, 1)
        os.close(log)
        sys.stdout = os.fdopen(1, "w", 1)
        reset_logging()

        logger.info("[%s] %s" % (time.strftime("%F %T"), " ".join(args)))

        parser = OptionParser("%prog [options] BUILD-ID")
        parser.add_option("-p", "--project", help="set project")
        parser.add_option("-S", "--subproject", help="set subproject")
        parser.add_option("-l", "--locale", help="set locale")
        parser.add_option("-s", "--series", help="set series")
        parser.add_option("-t", "--publish-type", help="set publish type")
        parser.add_option("-i", "--image-type", help="set image type")
        parser.add_option("-a", "--architecture", help="set architecture")
        options, parsed_args = parser.parse_args(args)

        if options.subproject:
            config["SUBPROJECT"] = options.subproject
        if options.locale:
            config["UBUNTU_DEFAULTS_LOCALE"] = options.locale
        if options.project:
            if not setenv_for_project(options.project):
                parser.error("unrecognised project '%s'" % options.project)
            config["PROJECT"] = os.environ["PROJECT"]
            config["CAPPROJECT"] = os.environ["CAPPROJECT"]
        else:
            parser.error("need project")

        if options.series:
            config["DIST"] = options.series

        if options.image_type:
            config["IMAGE_TYPE"] = options.image_type
        elif options.publish_type:
            config["IMAGE_TYPE"] = DailyTreePublisher._guess_image_type(
                options.publish_type)
            if not config["IMAGE_TYPE"]:
                parser.error(
                    "unrecognised publish type '%s'" % options.publish_type)
        else:
            parser.error("need image type or publish type")

        if options.architecture:
            arches = [options.architecture]
        else:
            parser.error("need architecture")

        if len(parsed_args) < 1:
            parser.error("need build ID")
        date = parsed_args[0]

        tree = Tree.get_daily(config)
        publisher = Publisher.get_daily(tree, config["IMAGE_TYPE"])
        try:
            publisher.mark_current(date, arches)
        except Exception:
            for line in traceback.format_exc().splitlines():
                logger.error(line)
            sys.stdout.flush()
            raise


class Publisher:
    """A object that can publish images to a tree."""

    @staticmethod
    def get_daily(tree, image_type, try_zsyncmake=True):
        if tree.config["UBUNTU_DEFAULTS_LOCALE"] == "zh_CN":
            cls = ChinaDailyTreePublisher
        else:
            cls = DailyTreePublisher
        return cls(tree, image_type, try_zsyncmake=try_zsyncmake)

    def __init__(self, tree, image_type):
        self.tree = tree
        self.config = tree.config
        self.project = self.config.project
        self.image_type = image_type


class DailyTree(Tree):
    """A publication tree containing daily builds."""

    def __init__(self, config, directory=None):
        if directory is None:
            directory = os.path.join(config.root, "www", "full")
        super(DailyTree, self).__init__(config, directory)

    def name_to_series(self, name):
        """Return the series for a file basename."""
        dist = name.split("-")[0]
        return Series.find_by_name(dist)

    @property
    def site_name(self):
        return "cdimage.ubuntu.com"

    def manifest_files(self):
        """Yield all the files to include in a manifest of this tree."""
        seen_inodes = []
        for dirpath, dirnames, filenames in os.walk(
                self.directory, followlinks=True):
            # Detect loops.
            st = os.stat(dirpath)
            dev_ino = (st.st_dev, st.st_ino)
            seen_inodes.append(dev_ino)
            for i in range(len(dirnames) - 1, -1, -1):
                st = os.stat(os.path.join(dirpath, dirnames[i]))
                dev_ino = (st.st_dev, st.st_ino)
                if dev_ino in seen_inodes:
                    del dirnames[i]

            dirpath_bits = dirpath.split(os.sep)
            if "current" in dirpath_bits or "pending" in dirpath_bits:
                relative_dirpath = dirpath[len(self.directory) + 1:]
                for filename in filenames:
                    path = os.path.join(dirpath, filename)
                    if self.manifest_file_allowed(path):
                        yield os.path.join(relative_dirpath, filename)

            if not dirnames:
                seen_inodes.pop()


class DailyTreePublisher(Publisher):
    """An object that can publish daily builds."""

    def __init__(self, tree, image_type, try_zsyncmake=True):
        super(DailyTreePublisher, self).__init__(tree, image_type)
        self.checksum_dirs = []
        self.try_zsyncmake = try_zsyncmake  # for testing

    def image_output(self, arch):
        return os.path.join(
            self.config.root, "scratch", self.project, self.config.series,
            self.image_type, "debian-cd", arch)

    @property
    def source_extension(self):
        return "raw"

    @property
    def britney_report(self):
        return os.path.join(
            self.config.root, "britney", "report", self.project,
            self.image_type)

    @property
    def full_tree(self):
        if self.project == "ubuntu":
            return self.tree.directory
        else:
            return os.path.join(self.tree.directory, self.project)

    @property
    def image_type_dir(self):
        image_type_dir = self.image_type.replace("_", "/")
        if not self.config["DIST"].is_latest:
            image_type_dir = os.path.join(self.config.series, image_type_dir)
        return image_type_dir

    @property
    def publish_base(self):
        return os.path.join(self.full_tree, self.image_type_dir)

    def metalink_dirs(self, date):
        if self.project == "ubuntu":
            reldir = os.path.join(self.image_type_dir, date)
        else:
            reldir = os.path.join(self.project, self.image_type_dir, date)
        return self.tree.directory, reldir

    # Keep this in sync with _guess_image_type below.
    @property
    def publish_type(self):
        if self.image_type.endswith("-preinstalled"):
            if self.project == "ubuntu-netbook":
                return "preinstalled-netbook"
            elif self.project == "ubuntu-headless":
                return "preinstalled-headless"
            elif self.project == "ubuntu-server":
                return "preinstalled-server"
            else:
                return "preinstalled-desktop"
        elif self.image_type.endswith("-live"):
            if self.project == "edubuntu":
                if self.config["DIST"] <= "edgy":
                    return "live"
                else:
                    return "desktop"
            elif self.project == "ubuntu-mid":
                return "mid"
            elif self.project == "ubuntu-moblin-remix":
                return "moblin-remix"
            elif self.project in ("ubuntu-netbook", "kubuntu-netbook"):
                return "netbook"
            elif self.project == "ubuntu-server":
                return "live"
            else:
                if self.config["DIST"] <= "breezy":
                    return "live"
                else:
                    return "desktop"
        elif self.image_type.endswith("_dvd") or self.image_type == "dvd":
            return "dvd"
        else:
            if self.project == "edubuntu":
                if self.config["DIST"] <= "edgy":
                    return "install"
                elif self.config["DIST"] <= "gutsy":
                    return "server"
                else:
                    return "addon"
            elif self.project == "ubuntu-server":
                if self.config["DIST"] <= "breezy":
                    return "install"
                else:
                    return "server"
            elif self.project == "jeos":
                return "jeos"
            elif self.project == "ubuntu-core":
                return "core"
            else:
                if self.config["DIST"] <= "breezy":
                    return "install"
                else:
                    return "alternate"

    # Keep this in sync with publish_type above.
    @staticmethod
    def _guess_image_type(publish_type):
        if publish_type.startswith("preinstalled-"):
            return "daily-preinstalled"
        elif publish_type in (
                "desktop", "live", "mid", "moblin-remix", "netbook"):
            return "daily-live"
        elif publish_type == "dvd":
            return "dvd"
        elif publish_type in (
                "addon", "alternate", "core", "install", "jeos", "server"):
            return "daily"
        else:
            return None

    @property
    def size_limit(self):
        if self.project in ("edubuntu", "ubuntustudio"):
            # All Edubuntu images are DVD sized (including arm).
            # Ubuntu Studio is always DVD-sized for now.
            return 4700372992
        elif self.project in (
                "ubuntu-mid", "ubuntu-moblin-remix",
                "kubuntu-active", "kubuntu"):
            # Mobile images are designed for USB drives; arbitrarily pick
            # 1GB as a limit.
            return 1024 * 1024 * 1024
        elif (self.project == "ubuntu" and self.publish_type != "dvd" and
              self.config["DIST"] >= "quantal"):
            # Ubuntu quantal onward has an (arbitrary) 801MB limit.
            return 801000000
        elif self.project == "xubuntu" and self.config["DIST"] >= "raring":
            # http://irclogs.ubuntu.com/2013/02/11/%23xubuntu-devel.html#t21:48
            return 1024 * 1024 * 1024
        else:
            if self.publish_type == "dvd":
                # http://en.wikipedia.org/wiki/DVD_plus_RW
                return 4700372992
            else:
                # http://en.wikipedia.org/wiki/CD-ROM#Capacity gives a
                # maximum of 737280000; RedBook requires reserving 300
                # sectors, so we do the same here Just In Case.  If we need
                # to surpass this limit we should rigorously re-test and
                # check again with ProMese, the CD pressing vendor.
                return 736665600

    def size_limit_extension(self, extension):
        """Some output file types have adjusted limits.  Cope with this."""
        # TODO: Shouldn't this be per-project/publish_type instead?
        if self.project == "edubuntu":
            return self.size_limit
        elif extension == "img" or extension.endswith(".gz"):
            return 1024 * 1024 * 1024
        else:
            return self.size_limit

    def new_publish_dir(self, date):
        """Copy previous published tree as a starting point for a new one.

        This allows single-architecture rebuilds to carry over other
        architectures from previous builds.
        """
        publish_base = self.publish_base
        publish_date = os.path.join(publish_base, date)
        osextras.ensuredir(publish_date)
        if self.config["CDIMAGE_NOCOPY"]:
            return
        for previous_name in "pending", "current":
            publish_previous = os.path.join(publish_base, previous_name)
            if os.path.exists(publish_previous):
                for name in sorted(os.listdir(publish_previous)):
                    if name.startswith("%s-" % self.config.series):
                        os.link(
                            os.path.join(publish_previous, name),
                            os.path.join(publish_date, name))
                break

    def detect_image_extension(self, source_prefix):
        subp = subprocess.Popen(
            ["file", "-b", "%s.%s" % (source_prefix, self.source_extension)],
            stdout=subprocess.PIPE, universal_newlines=True)
        output = subp.communicate()[0].rstrip("\n")
        if output.startswith("# "):
            output = output[2:]

        if output.startswith("ISO 9660 CD-ROM filesystem data "):
            return "iso"
        elif output.startswith("x86 boot sector"):
            return "img"
        elif output.startswith("gzip compressed data"):
            with open("%s.type" % source_prefix) as compressed_type:
                real_output = compressed_type.readline().rstrip("\n")
            if real_output.startswith("ISO 9660 CD-ROM filesystem data "):
                return "iso.gz"
            elif real_output.startswith("x86 boot sector"):
                return "img.gz"
            elif real_output.startswith("tar archive"):
                return "tar.gz"
            else:
                logger.warning(
                    "Unknown compressed file type '%s'; assuming .img.gz" %
                    real_output)
                return "img.gz"
        else:
            logger.warning("Unknown file type '%s'; assuming .iso" % output)
            return "iso"

    def jigdo_ports(self, arch):
        series = self.config["DIST"]
        cpuarch = arch.split("+")[0]
        if cpuarch == "powerpc":
            # https://lists.ubuntu.com/archives/ubuntu-announce/2007-February/000098.html
            if series > "edgy":
                return True
        elif cpuarch == "sparc":
            # https://lists.ubuntu.com/archives/ubuntu-devel-announce/2008-March/000400.html
            if series < "dapper" or series > "gutsy":
                return True
        elif cpuarch in ("armel", "armhf", "hppa", "ia64", "lpia"):
            return True
        return False

    def replace_jigdo_mirror(self, path, from_mirror, to_mirror):
        with open(path) as jigdo_in:
            with AtomicFile(path) as jigdo_out:
                from_line = "Debian=%s" % from_mirror
                to_line = "Debian=%s" % to_mirror
                for line in jigdo_in:
                    jigdo_out.write(line.replace(from_line, to_line))

    def publish_binary(self, publish_type, arch, date):
        in_prefix = "%s-%s-%s" % (self.config.series, publish_type, arch)
        out_prefix = "%s-%s-%s" % (self.config.series, publish_type, arch)
        source_dir = self.image_output(arch)
        source_prefix = os.path.join(source_dir, in_prefix)
        target_dir = os.path.join(self.publish_base, date)
        target_prefix = os.path.join(target_dir, out_prefix)

        if not os.path.exists(
                "%s.%s" % (source_prefix, self.source_extension)):
            logger.warning("No %s image for %s!" % (publish_type, arch))
            for name in osextras.listdir_force(target_dir):
                if name.startswith("%s." % out_prefix):
                    os.unlink(os.path.join(target_dir, name))
            return

        logger.info("Publishing %s ..." % arch)
        osextras.ensuredir(target_dir)
        extension = self.detect_image_extension(source_prefix)
        shutil.move(
            "%s.%s" % (source_prefix, self.source_extension),
            "%s.%s" % (target_prefix, extension))
        if os.path.exists("%s.list" % source_prefix):
            shutil.move("%s.list" % source_prefix, "%s.list" % target_prefix)
        self.checksum_dirs.append(source_dir)
        with ChecksumFileSet(
                self.config, target_dir, sign=False) as checksum_files:
            checksum_files.remove("%s.%s" % (out_prefix, extension))

        # Jigdo integration
        if os.path.exists("%s.jigdo" % source_prefix):
            logger.info("Publishing %s jigdo ..." % arch)
            shutil.move("%s.jigdo" % source_prefix, "%s.jigdo" % target_prefix)
            shutil.move(
                "%s.template" % source_prefix, "%s.template" % target_prefix)
            if self.jigdo_ports(arch):
                self.replace_jigdo_mirror(
                    "%s.jigdo" % target_prefix,
                    "http://archive.ubuntu.com/ubuntu",
                    "http://ports.ubuntu.com/ubuntu-ports")
        else:
            osextras.unlink_force("%s.jigdo" % target_prefix)
            osextras.unlink_force("%s.template" % target_prefix)

        # Live filesystem manifests
        if os.path.exists("%s.manifest" % source_prefix):
            logger.info("Publishing %s live manifest ..." % arch)
            shutil.move(
                "%s.manifest" % source_prefix, "%s.manifest" % target_prefix)
        else:
            osextras.unlink_force("%s.manifest" % target_prefix)

        if (self.config["CDIMAGE_SQUASHFS_BASE"] and
                os.path.exists("%s.squashfs" % source_prefix)):
            logger.info("Publishing %s squashfs ..." % arch)
            shutil.move(
                "%s.squashfs" % source_prefix, "%s.squashfs" % target_prefix)
        else:
            osextras.unlink_force("%s.squashfs" % target_prefix)

        # Flashable Android boot images
        if os.path.exists("%s.bootimg" % source_prefix):
            logger.info("Publishing %s abootimg bootloader images ..." % arch)
            shutil.move(
                "%s.bootimg" % source_prefix, "%s.bootimg" % target_prefix)

        # zsync metafiles
        if self.try_zsyncmake and osextras.find_on_path("zsyncmake"):
            logger.info("Making %s zsync metafile ..." % arch)
            osextras.unlink_force("%s.%s.zsync" % (target_prefix, extension))
            zsyncmake(
                "%s.%s" % (target_prefix, extension),
                "%s.%s.zsync" % (target_prefix, extension),
                "%s.%s" % (out_prefix, extension))

        size = os.stat("%s.%s" % (target_prefix, extension)).st_size
        if size > self.size_limit_extension(extension):
            with open("%s.OVERSIZED" % target_prefix, "a"):
                pass
        else:
            osextras.unlink_force("%s.OVERSIZED" % target_prefix)

        qa_project = self.project
        if self.config["UBUNTU_DEFAULTS_LOCALE"]:
            qa_project = "-".join(
                [qa_project, self.config["UBUNTU_DEFAULTS_LOCALE"]])
        yield os.path.join(qa_project, self.image_type_dir, in_prefix)

    def publish_livecd_base(self, arch, date):
        source_dir = os.path.join(
            self.config.root, "scratch", self.project, self.config.series,
            self.image_type, "live")
        source_prefix = os.path.join(source_dir, arch)
        target_dir = os.path.join(self.publish_base, date)
        target_prefix = os.path.join(target_dir, arch)

        if os.path.exists("%s.cloop" % source_prefix):
            fs = "cloop"
        elif os.path.exists("%s.squashfs" % source_prefix):
            fs = "squashfs"
        else:
            logger.warning("No filesystem for %s!" % arch)
            return

        logger.info("Publishing %s ..." % arch)
        osextras.ensuredir(target_dir)
        shutil.copy2(
            "%s.%s" % (source_prefix, fs), "%s.%s" % (target_prefix, fs))
        if os.path.exists("%s.kernel" % source_prefix):
            shutil.copy2(
                "%s.kernel" % source_prefix, "%s.kernel" % target_prefix)
        if os.path.exists("%s.initrd" % source_prefix):
            shutil.copy2(
                "%s.initrd" % source_prefix, "%s.initrd" % target_prefix)
        shutil.copy2(
            "%s.manifest" % source_prefix, "%s.manifest" % target_prefix)
        if os.path.exists("%s.manifest-remove" % source_prefix):
            shutil.copy2(
                "%s.manifest-remove" % source_prefix,
                "%s.manifest-remove" % target_prefix)
        elif os.path.exists("%s.manifest-desktop" % source_prefix):
            shutil.copy2(
                "%s.manifest-desktop" % source_prefix,
                "%s.manifest-desktop" % target_prefix)

        yield os.path.join("livecd-base", self.image_type_dir, arch)

    def publish_source(self, date):
        for i in count(1):
            in_prefix = "%s-src-%d" % (self.config.series, i)
            out_prefix = "%s-src-%d" % (self.config.series, i)
            source_dir = self.image_output("src")
            source_prefix = os.path.join(source_dir, in_prefix)
            target_dir = os.path.join(self.publish_base, date, "source")
            target_prefix = os.path.join(target_dir, out_prefix)
            if not os.path.exists(
                    "%s.%s" % (source_prefix, self.source_extension)):
                break

            logger.info("Publishing source %d ..." % i)
            osextras.ensuredir(target_dir)
            shutil.move(
                "%s.%s" % (source_prefix, self.source_extension),
                "%s.iso" % target_prefix)
            shutil.move("%s.list" % source_prefix, "%s.list" % target_prefix)
            with ChecksumFileSet(
                    self.config, target_dir, sign=False) as checksum_files:
                checksum_files.remove("%s.iso" % out_prefix)

            # Jigdo integration
            if os.path.exists("%s.jigdo" % source_prefix):
                logger.info("Publishing source %d jigdo ..." % i)
                shutil.move(
                    "%s.jigdo" % source_prefix, "%s.jigdo" % target_prefix)
                shutil.move(
                    "%s.template" % source_prefix,
                    "%s.template" % target_prefix)
            else:
                logger.warning("No jigdo for source %d!" % i)
                osextras.unlink_force("%s.jigdo" % target_prefix)
                osextras.unlink_force("%s.template" % target_prefix)

            # zsync metafiles
            if self.try_zsyncmake and osextras.find_on_path("zsyncmake"):
                logger.info("Making source %d zsync metafile ..." % i)
                osextras.unlink_force("%s.iso.zsync" % target_prefix)
                zsyncmake(
                    "%s.iso" % target_prefix, "%s.iso.zsync" % target_prefix,
                    "%s.iso" % out_prefix)

            yield os.path.join(
                self.project, self.image_type, "%s-src" % self.config.series)

    def polish_directory(self, date):
        """Apply various bits of polish to a published directory."""
        target_dir = os.path.join(self.publish_base, date)

        if not self.config["CDIMAGE_ONLYSOURCE"]:
            checksum_directory(
                self.config, target_dir, old_directories=self.checksum_dirs,
                map_expr=r"s/\.\(img\|img\.gz\|iso\|iso\.gz\|tar\.gz\)$/.raw/")
        if (self.config.project != "livecd-base" and
                not self.config["CDIMAGE_ONLYSOURCE"]):
            subprocess.check_call(
                [os.path.join(self.config.root, "bin", "make-web-indices"),
                 target_dir, self.config.series, "daily"])

        target_dir_source = os.path.join(target_dir, "source")
        if os.path.isdir(target_dir_source):
            checksum_directory(
                self.config, target_dir_source,
                old_directories=[self.image_output("src")],
                map_expr=r"s/\.\(img\|img\.gz\|iso\|iso\.gz\|tar\.gz\)$/.raw/")
            subprocess.check_call(
                [os.path.join(self.config.root, "bin", "make-web-indices"),
                 target_dir_source, self.config.series, "daily"])

        if (self.image_type.endswith("-live") or
                self.image_type.endswith("dvd")):
            # Create and publish metalink files.
            md5sums_metalink = os.path.join(target_dir, "MD5SUMS-metalink")
            md5sums_metalink_gpg = os.path.join(
                target_dir, "MD5SUMS-metalink.gpg")
            osextras.unlink_force(md5sums_metalink)
            osextras.unlink_force(md5sums_metalink_gpg)
            basedir, reldir = self.metalink_dirs(date)
            if subprocess.call([
                os.path.join(self.config.root, "bin", "make-metalink"),
                basedir, self.config.series, reldir, self.tree.site_name,
            ]) == 0:
                metalink_checksum_directory(self.config, target_dir)
            else:
                for name in os.listdir(target_dir):
                    if name.endswith(".metalink"):
                        osextras.unlink_force(os.path.join(target_dir, name))

    def link(self, date, name):
        target = os.path.join(self.publish_base, name)
        osextras.unlink_force(target)
        os.symlink(date, target)

    def published_images(self, date):
        """Return all the images published at a particular date (or alias)."""
        images = set()
        publish_dir = os.path.join(self.publish_base, date)
        for entry in osextras.listdir_force(publish_dir):
            entry_path = os.path.join(publish_dir, entry)
            if self.tree.manifest_file_allowed(entry_path):
                images.add(entry)
        return images

    def mark_current(self, date, arches):
        """Mark images as current."""
        # First, build a map of what's available at the requested date, and
        # what's already marked as current.
        available = self.published_images(date)
        existing = {}
        publish_current = os.path.join(self.publish_base, "current")
        if os.path.islink(publish_current):
            target_date = os.readlink(publish_current)
            if "/" not in target_date:
                for entry in self.published_images("current"):
                    existing[entry] = target_date
        else:
            for entry in self.published_images("current"):
                entry_path = os.path.join(publish_current, entry)
                # Be very careful to check that entries in a "current"
                # directory match the expected form, since we may feel the
                # need to delete them later.
                assert os.path.islink(entry_path)
                target_bits = os.readlink(entry_path).split(os.sep)
                assert len(target_bits) == 3
                assert target_bits[0] == os.pardir
                assert target_bits[2] == entry
                existing[entry] = target_bits[1]

        # Update the map according to this request.
        changed = set()
        for image in available:
            image_base = image.split(".", 1)[0]
            for arch in arches:
                if image_base.endswith("-%s" % arch):
                    changed.add(image)
                    existing[image] = date
                    break

        if (set(existing) == available and
                set(existing.values()) == set([date])):
            # Everything is consistent and complete.  Replace "current" with
            # a single symlink.
            if (not os.path.islink(publish_current) and
                    os.path.isdir(publish_current)):
                shutil.rmtree(publish_current)
            self.link(date, "current")
        else:
            # It's more complicated than that: the current images differ on
            # different architectures.  Make a directory, populate it with
            # symlinks, and reapply polish such as indices and checksums.
            if os.path.islink(publish_current):
                os.unlink(publish_current)
            if not os.path.exists(publish_current):
                os.mkdir(publish_current)
                changed = set(existing)
            for image in changed:
                date = existing[image]
                publish_date = os.path.join(self.publish_base, date)
                for entry in osextras.listdir_force(publish_date):
                    if entry.split(".", 1)[0] == image.split(".", 1)[0]:
                        source = os.path.join(os.pardir, date, entry)
                        target = os.path.join(publish_current, entry)
                        osextras.unlink_force(target)
                        os.symlink(source, target)
            self.polish_directory("current")

    def current_uses_trigger(self, arch):
        """Find out whether the "current" symlink is trigger-controlled."""
        current_triggers_path = os.path.join(
            self.config.root, "production", "current-triggers")
        if not os.path.exists(current_triggers_path):
            return False
        want_project_bits = [self.project]
        if self.subproject:
            want_project_bits.append(self.subproject)
        if self["UBUNTU_DEFAULTS_LOCALE"]:
            want_project_bits.append(self["UBUNTU_DEFAULTS_LOCALE"])
        want_project = "-".join(want_project_bits)
        with open(current_triggers_path) as current_triggers:
            for line in current_triggers:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    project, image_type, series, arches = line.split(None, 3)
                except ValueError:
                    continue
                if want_project != project:
                    continue
                if self.image_type != image_type:
                    continue
                if self.config.series != series:
                    continue
                if arch in arches:
                    return True
        return False

    def set_link_descriptions(self):
        """Set standard link descriptions in publish_base/.htaccess."""
        descriptions = {
            "pending": (
                "Most recently built images; not yet automatically tested"),
            "current": (
                "Latest images to have passed any automatic testing; "
                "try this first"),
        }
        htaccess_path = os.path.join(self.publish_base, ".htaccess")
        if not os.path.exists(htaccess_path):
            with AtomicFile(htaccess_path) as htaccess:
                for name, description in sorted(descriptions.items()):
                    print('AddDescription "%s" %s' % (description, name),
                          file=htaccess)
                print("IndexOptions FancyIndexing", file=htaccess)

    def qa_product(self, project, image_type, publish_type, arch):
        """Return the QA tracker product for an image, or None.

        Any changes here must be coordinated with the tracker
        (iso.qa.ubuntu.com), since we can only return products that exist
        there and they are not necessarily consistently named.
        """
        if project == "ubuntu":
            if image_type == "daily" and publish_type == "alternate":
                return "Ubuntu Alternate %s" % arch
            elif image_type == "daily-live" and publish_type == "desktop":
                return "Ubuntu Desktop %s" % arch
            elif (image_type == "daily-preinstalled" and
                  publish_type == "preinstalled-desktop"):
                return "Ubuntu Desktop Preinstalled %s" % arch
            elif image_type == "dvd" and publish_type == "dvd":
                return "Ubuntu DVD %s" % arch
            elif image_type == "wubi" and publish_type == "wubi":
                return "Ubuntu Wubi %s" % arch
        elif project == "kubuntu":
            if image_type == "daily" and publish_type == "alternate":
                return "Kubuntu Alternate %s" % arch
            elif image_type == "daily-live" and publish_type == "desktop":
                return "Kubuntu Desktop %s" % arch
            elif (image_type == "daily-preinstalled" and
                  publish_type == "preinstalled-desktop"):
                return "Kubuntu Desktop %s" % arch
            elif image_type == "dvd" and publish_type == "dvd":
                return "Kubuntu DVD %s" % arch
        elif project == "kubuntu-active":
            if image_type == "daily-live" and publish_type == "desktop":
                return "Kubuntu Active %s" % arch
            elif (image_type == "daily-preinstalled" and
                  publish_type == "preinstalled-mobile"):
                return "Kubuntu Active %s" % arch
        elif project == "edubuntu":
            if image_type == "dvd" and publish_type == "dvd":
                return "Edubuntu DVD %s" % arch
        elif project == "xubuntu":
            if image_type == "daily" and publish_type == "alternate":
                return "Xubuntu Alternate %s" % arch
            elif image_type == "daily-live" and publish_type == "desktop":
                return "Xubuntu Desktop %s" % arch
        elif project == "ubuntu-server":
            if image_type == "daily" and publish_type == "server":
                return "Ubuntu Server %s" % arch
            elif (image_type == "daily-preinstalled" and
                  publish_type == "preinstalled-server"):
                return "Ubuntu Server %s" % arch
        elif project == "ubuntustudio":
            if image_type == "daily" and publish_type == "alternate":
                return "Ubuntu Studio Alternate %s" % arch
            elif image_type == "dvd" and publish_type == "dvd":
                return "Ubuntu Studio DVD %s" % arch
        elif project == "mythbuntu":
            if image_type == "daily-live" and publish_type == "desktop":
                return "Mythbuntu Desktop %s" % arch
        elif project == "lubuntu":
            if image_type == "daily" and publish_type == "alternate":
                return "Lubuntu Alternate %s" % arch
            elif image_type == "daily-live" and publish_type == "desktop":
                return "Lubuntu Desktop %s" % arch
            elif (image_type == "daily-preinstalled" and
                  publish_type == "preinstalled-desktop"):
                return "Lubuntu Desktop Preinstalled %s" % arch
        elif project == "ubuntu-core":
            if image_type == "daily" and publish_type == "core":
                return "Ubuntu Core %s" % arch
        elif project == "ubuntu-zh_CN":
            if image_type == "daily-live" and publish_type == "desktop":
                return "Ubuntu Chinese Desktop %s" % arch
        elif project == "ubuntukylin":
            if image_type == "daily-live" and publish_type == "desktop":
                return "UbuntuKylin Desktop %s" % arch
        elif project == "ubuntu-gnome":
            if image_type == "daily-live" and publish_type == "desktop":
                return "Ubuntu GNOME Desktop %s" % arch

    def post_qa(self, date, images):
        """Post a list of images to the QA tracker."""
        from isotracker import ISOTracker

        tracker = None

        for image in images:
            image_bits = image.split("/")
            if len(image_bits) == 3:
                project, image_type, base = image_bits
                image_series = None
            else:
                project, image_series, image_type, base = image_bits
            base_match = re.match(r"(.*?)-(.*)-(.*)", base)
            if not base_match:
                continue
            dist, publish_type, arch = base_match.groups()
            product = self.qa_product(project, image_type, publish_type, arch)
            if product is None:
                logger.warning(
                    "No iso.qa.ubuntu.com product found for %s; skipping." %
                    image)
                continue

            # Try to figure out the path to the OVERSIZED indicator for the
            # build.
            iso_path_bits = [self.full_tree]
            if image_series is not None:
                iso_path_bits.append(image_series)
            iso_path_bits.extend([image_type, date, base])
            iso_path = os.path.join(*iso_path_bits)
            note = ""
            if os.path.exists("%s.OVERSIZED" % iso_path):
                note = (
                    "<strong>WARNING: This image is OVERSIZED. This should "
                    "never happen during milestone testing.</strong>")

            if tracker is None or tracker.target != dist:
                tracker = ISOTracker(target=dist)
            try:
                tracker.post_build(product, date, note=note)
            except Exception:
                traceback.print_exc()

    def publish(self, date):
        self.new_publish_dir(date)
        published = []
        self.checksum_dirs = []
        if self.config.project == "livecd-base":
            for arch in self.config.cpuarches:
                published.extend(list(self.publish_livecd_base(arch, date)))
        elif not self.config["CDIMAGE_ONLYSOURCE"]:
            for arch in self.config.arches:
                published.extend(
                    list(self.publish_binary(self.publish_type, arch, date)))
            if self.project == "edubuntu" and self.publish_type == "server":
                for arch in self.config.arches:
                    published.extend(
                        list(self.publish_binary("serveraddon", arch, date)))
        published.extend(list(self.publish_source(date)))

        if not published:
            logger.warning("No images produced!")
            return

        source_report = os.path.join(
            self.britney_report, "%s_probs.html" % self.config.series)
        target_report = os.path.join(self.publish_base, date, "report.html")
        if (self.config["CDIMAGE_INSTALL_BASE"] and
                os.path.exists(source_report)):
            shutil.copy2(source_report, target_report)
        else:
            osextras.unlink_force(target_report)

        self.polish_directory(date)
        self.link(date, "pending")
        current_arches = [
            arch for arch in self.config.arches
            if not self.current_uses_trigger(arch)]
        self.mark_current(date, current_arches)
        self.link(date, "current")
        self.set_link_descriptions()

        manifest_lock = os.path.join(
            self.config.root, "etc", ".lock-manifest-daily")
        try:
            subprocess.check_call(["lockfile", "-r", "4", manifest_lock])
        except subprocess.CalledProcessError:
            logger.error("Couldn't acquire manifest-daily lock!")
            raise
        try:
            manifest_daily = os.path.join(
                self.tree.directory, ".manifest-daily")
            with AtomicFile(manifest_daily) as manifest_daily_file:
                for line in self.tree.manifest():
                    print(line, file=manifest_daily_file)
            os.chmod(
                manifest_daily, os.stat(manifest_daily).st_mode | stat.S_IWGRP)

            # Create timestamps for this run.
            # TODO cjwatson 20120807: Shouldn't these be in www/full
            # rather than www/full[/project]?
            trace_dir = os.path.join(self.full_tree, ".trace")
            osextras.ensuredir(trace_dir)
            fqdn = socket.getfqdn()
            with open(os.path.join(trace_dir, fqdn), "w") as trace_file:
                subprocess.check_call(["date", "-u"], stdout=trace_file)
        finally:
            osextras.unlink_force(manifest_lock)

        self.post_qa(date, published)

    def get_purge_days(self, key):
        path = os.path.join(self.config.root, "etc", "purge-days")
        try:
            with open(path) as purge_days:
                for line in purge_days:
                    if line.startswith("#"):
                        continue
                    line = line.rstrip("\n")
                    words = line.split(None, 1)
                    if len(words) != 2:
                        continue
                    if words[0] == key:
                        return int(words[1])
        except IOError as e:
            if e.errno != errno.ENOENT:
                raise
        return None

    def purge(self, days=None):
        project = self.project
        if self.config["UBUNTU_DEFAULTS_LOCALE"]:
            project = "-".join(
                [project, self.config["UBUNTU_DEFAULTS_LOCALE"]])
        project_image_type = "%s/%s" % (project, self.image_type)

        if days is None:
            days = self.get_purge_days(project)
        if days is None:
            days = self.get_purge_days(project_image_type)
        if days is None:
            days = self.get_purge_days(self.image_type)
        if days is None:
            logger.info("No purge time configured for %s" % project_image_type)
            return
        if days == 0:
            logger.info("Not purging images for %s" % project_image_type)
            return
        logger.info(
            "Purging %s images older than %d days ..." %
            (project_image_type, days))
        oldest = time.strftime(
            "%Y%m%d", time.gmtime(time.time() - 60 * 60 * 24 * days))

        for entry in sorted(osextras.listdir_force(self.publish_base)):
            entry_path = os.path.join(self.publish_base, entry)

            # Directory?
            if not os.path.isdir(entry_path):
                continue

            # Numeric directory?
            if not entry[0].isdigit():
                continue

            # Older than cut-off date?
            if int(oldest) <= int(entry.split(".", 1)[0]):
                continue

            # Pointed to by "pending" or "current" symlink?
            publish_pending = os.path.join(self.publish_base, "pending")
            if (os.path.islink(publish_pending) and
                    os.readlink(publish_pending) == entry):
                continue
            publish_current = os.path.join(self.publish_base, "current")
            if os.path.islink(publish_current):
                if os.readlink(publish_current) == entry:
                    continue
            elif os.path.isdir(publish_current):
                found_current = False
                for current_entry in os.listdir(publish_current):
                    current_entry_path = os.path.join(
                        publish_current, current_entry)
                    if os.path.islink(current_entry_path):
                        target_bits = os.readlink(
                            current_entry_path).split(os.sep)
                        if (len(target_bits) == 3 and
                                target_bits[0] == os.pardir and
                                target_bits[1] == entry and
                                target_bits[2] == current_entry):
                            found_current = True
                            break
                if found_current:
                    continue

            if self.config["DEBUG"] or self.config["CDIMAGE_NOPURGE"]:
                logger.info(
                    "Would purge %s/%s/%s" %
                    (project, self.image_type_dir, entry))
            else:
                logger.info(
                    "Purging %s/%s/%s" % (project, self.image_type_dir, entry))
                shutil.rmtree(entry_path)


class ChinaDailyTree(DailyTree):
    """A publication tree containing daily builds of the Chinese edition.

    There isn't really any natural reason for Chinese to be special here,
    but the Chinese edition was initially done as a special-case hack.  Its
    successor, UbuntuKylin, is implemented more normally.
    """

    def __init__(self, config, directory=None):
        if directory is None:
            directory = os.path.join(config.root, "www", "china-images")
        super(ChinaDailyTree, self).__init__(config, directory)

    @property
    def site_name(self):
        return "china-images.ubuntu.com"


class ChinaDailyTreePublisher(DailyTreePublisher):
    """An object that can publish daily builds of the Chinese edition."""

    def image_output(self, arch):
        if self.config["DIST"] < "oneiric":
            return os.path.join(
                self.config.root, "scratch", "ubuntu-chinese-edition",
                self.config.series)
        else:
            project = "ubuntu"
            if self.config["UBUNTU_DEFAULTS_LOCALE"]:
                project = "-".join([
                    project, self.config["UBUNTU_DEFAULTS_LOCALE"]])
            return os.path.join(
                self.config.root, "scratch", project, self.config.series,
                self.image_type, "live")

    @property
    def source_extension(self):
        return "iso"

    @property
    def full_tree(self):
        return self.tree.directory

    @property
    def image_type_dir(self):
        return os.path.join(
            self.config.series, self.image_type.replace("_", "/"))

    def metalink_dirs(self, date):
        return self.tree.directory, os.path.join(self.image_type_dir, date)

    @property
    def size_limit(self):
        if self.publish_type == "dvd":
            # http://en.wikipedia.org/wiki/DVD_plus_RW
            return 4700372992
        else:
            # In the New World Order, we like round numbers, plus add
            # another 50MB for Chinese localisation overhead.
            return 850000000


class SimpleTree(Tree):
    """A publication tree containing a few important releases."""

    def __init__(self, config, directory=None):
        if directory is None:
            directory = os.path.join(config.root, "www", "simple")
        super(SimpleTree, self).__init__(config, directory)

    def name_to_series(self, name):
        """Return the series for a file basename."""
        version = name.split("-")[1]
        try:
            return Series.find_by_version(".".join(version.split(".")[:2]))
        except ValueError:
            logger.warning("Unknown version: %s" % version)
            raise

    @property
    def site_name(self):
        return "releases.ubuntu.com"

    def manifest_files(self):
        """Yield all the files to include in a manifest of this tree."""
        main_filenames = set()
        for dirpath, dirnames, filenames in os.walk(self.directory):
            relative_dirpath = dirpath[len(self.directory) + 1:]
            try:
                del dirnames[dirnames.index(".pool")]
            except ValueError:
                pass
            for filename in filenames:
                path = os.path.join(dirpath, filename)
                if self.manifest_file_allowed(path):
                    main_filenames.add(filename)
                    yield os.path.join(relative_dirpath, filename)

        for dirpath, _, filenames in os.walk(self.directory):
            if os.path.basename(dirpath) == ".pool":
                relative_dirpath = dirpath[len(self.directory) + 1:]
                for filename in filenames:
                    if filename not in main_filenames:
                        path = os.path.join(dirpath, filename)
                        if self.manifest_file_allowed(path):
                            yield os.path.join(relative_dirpath, filename)
