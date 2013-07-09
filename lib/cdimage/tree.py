# -*- coding: UTF-8 -*-

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
from textwrap import dedent
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
from cdimage.mirror import trigger_mirrors
from cdimage import osextras
from cdimage.project import setenv_for_project


if sys.version < "3":
    input = raw_input


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


def zsyncmake(infile, outfile, url, dry_run=False):
    command = ["zsyncmake"]
    if infile.endswith(".gz"):
        command.append("-Z")
    command.extend(["-o", outfile, "-u", url, infile])
    if dry_run:
        logger.info(" ".join(command))
    elif subprocess.call(command) != 0:
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

    @staticmethod
    def get_release(config, official, directory=None):
        if config["UBUNTU_DEFAULTS_LOCALE"] == "zh_CN":
            cls = ChinaReleaseTree
        elif official in ("yes", "poolonly"):
            cls = SimpleReleaseTree
        elif official in ("named", "no"):
            cls = FullReleaseTree
        else:
            raise Exception("Unrecognised OFFICIAL setting: '%s'" % official)
        return cls(config, directory=directory)

    @staticmethod
    def get_for_directory(config, directory, status):
        www = os.path.join(config.root, "www")
        realpath = os.path.realpath(directory) + "/"
        if realpath.startswith(os.path.join(www, "full") + "/"):
            if status == "daily":
                cls = DailyTree
            else:
                cls = FullReleaseTree
        elif realpath.startswith(os.path.join(www, "simple") + "/"):
            cls = SimpleReleaseTree
        elif realpath.startswith(os.path.join(www, "china-images") + "/"):
            if status == "daily":
                cls = ChinaDailyTree
            else:
                cls = ChinaReleaseTree
        else:
            # Allow operating on directories outside of any root, for ease
            # of testing (e.g. make-web-indices on a copied scratch
            # directory).
            return Tree(config, "/")
        return cls(config)

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

    @property
    def project_base(self):
        """Return the per-project base directory within this tree."""
        if self.config.project == "ubuntu":
            return self.directory
        else:
            return os.path.join(self.directory, self.config.project)

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
                path.endswith(".tar.xz") or path.endswith(".zip")):
            try:
                if stat.S_ISREG(os.stat(path).st_mode):
                    return True
            except OSError:
                return False
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
    def mark_current_trigger(config, args=None, quiet=False):
        if not args:
            args = config["SSH_ORIGINAL_COMMAND"].split()[1:]
        if not args:
            return

        parser = OptionParser("%prog [options] BUILD-ID")
        parser.add_option("-p", "--project", help="set project")
        parser.add_option("-S", "--subproject", help="set subproject")
        parser.add_option("-l", "--locale", help="set locale")
        parser.add_option("-s", "--series", help="set series")
        parser.add_option("-t", "--publish-type", help="set publish type")
        parser.add_option("-i", "--image-type", help="set image type")
        parser.add_option("-a", "--architecture", help="set architecture")
        if "SSH_ORIGINAL_COMMAND" not in config:
            parser.add_option(
                "--no-log", dest="log", default=True, action="store_false",
                help="don't write to log file; don't trigger mirrors")
        options, parsed_args = parser.parse_args(args)
        if "SSH_ORIGINAL_COMMAND" in config:
            options.log = True

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

        old_stdout = os.fdopen(os.dup(1), "w", 1)
        try:
            if options.log:
                log_path = os.path.join(config.root, "log", "mark-current.log")
                osextras.ensuredir(os.path.dirname(log_path))
                log = os.open(
                    log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
                os.dup2(log, 1)
                os.close(log)
                sys.stdout = os.fdopen(1, "w", 1)
                reset_logging()

            logger.info(
                "[%s] mark-current %s" %
                (time.strftime("%F %T"), " ".join(args)))

            tree = Tree.get_daily(config)
            publisher = Publisher.get_daily(tree, config["IMAGE_TYPE"])
            try:
                for arch in arches:
                    if not publisher.current_uses_trigger(arch):
                        logger.warning(
                            "%s is not trigger-controlled; update "
                            "production/current-triggers" % arch)
                publisher.mark_current(date, arches)
                if options.log:
                    trigger_mirrors(config)
                if not quiet:
                    print(
                        "mark-current %s: success" % " ".join(args),
                        file=old_stdout)
            except Exception:
                for line in traceback.format_exc().splitlines():
                    logger.error(line)
                    if not quiet:
                        print(line, file=old_stdout)
                sys.stdout.flush()
                raise
        finally:
            old_stdout.close()


class WebIndicesException(Exception):
    pass


class Paragraph:
    def __init__(self, sentences):
        self.sentences = list(sentences)

    def __str__(self):
        return "<p>%s</p>" % "  ".join(self.sentences)


class UnorderedList:
    def __init__(self, elements):
        self.elements = list(elements)

    def __str__(self):
        return "<ul>\n%s\n</ul>" % "\n".join(
            ["<li>%s</li>" % e for e in self.elements])


class Span:
    def __init__(self, attr_class, sentences):
        self.attr_class = attr_class
        self.sentences = list(sentences)

    def __str__(self):
        return "<span class=\"%s\">%s</span>" % (
            self.attr_class, "  ".join(self.sentences))


class Link:
    def __init__(self, target, text, show_class=False):
        self.target = target
        self.text = text
        self.show_class = show_class

    def __str__(self):
        return "<a%s href=\"%s\">%s</a>" % (
            " class=\"http\"" if self.show_class else "",
            self.target, self.text)


class Publisher:
    """A object that can publish images to a tree."""

    @staticmethod
    def get_daily(tree, image_type):
        if tree.config["UBUNTU_DEFAULTS_LOCALE"] == "zh_CN":
            cls = ChinaDailyTreePublisher
        else:
            cls = DailyTreePublisher
        return cls(tree, image_type)

    def __init__(self, tree, image_type):
        self.tree = tree
        self.config = tree.config
        self.project = self.config.project
        self.image_type = image_type
        self.prefmsg_emitted = False

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
            elif self.project == "ubuntu-touch":
                return "preinstalled-touch"
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

    numbers = {
        1: "one",
        2: "two",
        3: "three",
        4: "four",
        5: "five",
        6: "six",
        7: "seven",
        8: "eight",
        9: "nine",
    }

    def titlecase(self, s):
        if s:
            return s[0].upper() + s[1:]
        else:
            return ""

    def cssincludes(self):
        if self.project == "kubuntu":
            return ["http://releases.ubuntu.com/include/kubuntu.css"]
        else:
            return ["http://releases.ubuntu.com/include/style.css"]

    def cdtypestr(self, publish_type, image_format):
        if image_format in ("tar.gz", "tar.xz"):
            cd = "filesystem archive"
        elif self.config["DIST"] < "quantal":
            if image_format in ("img", "img.gz"):
                cd = "image"
            elif self.project == "ubuntustudio":
                # Ubuntu Studio is expected to be oversized in Gutsy; sigh.
                cd = "dvd"
            else:
                cd = "cd"
        else:
            cd = "image"

        if publish_type == "live":
            return "live %s" % cd
        elif publish_type == "desktop":
            return "desktop %s" % cd
        elif publish_type == "install":
            return "install %s" % cd
        elif publish_type == "alternate":
            return "alternate install %s" % cd
        elif publish_type == "server":
            if self.project == "edubuntu":
                return "classroom server %s" % cd
            else:
                return "server install %s" % cd
        elif publish_type == "serveraddon":
            # Edubuntu only
            return "classroom server add-on %s" % cd
        elif publish_type == "addon":
            # Edubuntu only
            return "Ubuntu educational add-on %s" % cd
        elif publish_type == "dvd":
            return "install/live DVD"
        elif publish_type == "src":
            return "source %s" % cd
        elif publish_type == "netbook":
            return "netbook live %s" % cd
        elif publish_type == "mid":
            return "MID USB image"
        elif publish_type == "moblin-remix":
            return "Moblin live CD"
        elif publish_type == "active":
            return "preview active image"
        elif publish_type in ("server-uec", "uec"):
            return "UEC image"
        elif publish_type == "preinstalled-desktop":
            return "preinstalled desktop %s" % cd
        elif publish_type == "preinstalled-headless":
            return "preinstalled headless %s" % cd
        elif publish_type == "preinstalled-server":
            return "preinstalled server %s" % cd
        elif publish_type == "preinstalled-netbook":
            return "preinstalled netbook %s" % cd
        elif publish_type == "preinstalled-active":
            return "preview preinstalled active image"
        elif publish_type == "preinstalled-touch":
            return "preinstalled touch image"
        elif publish_type == "wubi":
            return "Wubi %s" % cd
        else:
            raise WebIndicesException("Unknown image type %s!" % publish_type)

    def cdtypedesc(self, publish_type, image_format):
        capproject = self.config.capproject
        series = self.config["DIST"]

        if self.project == "mid":
            # MID has lower memory requirements than others
            desktop_ram = 128
        if self.project == "xubuntu":
            if series <= "intrepid":
                desktop_ram = 128
            else:
                desktop_ram = 192
        else:
            if series <= "feisty":
                desktop_ram = 256
            elif series <= "gutsy":
                desktop_ram = 320
            elif series <= "hardy":
                desktop_ram = 384
            elif series <= "maverick":
                desktop_ram = 256
            else:
                desktop_ram = 384

        if image_format in ("tar.gz", "tar.xz"):
            cd = "filesystem archive"
        elif self.config["DIST"] < "quantal":
            if image_format in ("img", "img.gz"):
                cd = "image"
            elif self.project == "ubuntustudio":
                # Ubuntu Studio is expected to be oversized in Gutsy; sigh.
                cd = "dvd"
            else:
                cd = "cd"
        else:
            cd = "image"

        desktop_req = (
            "You will need at least %sMiB of RAM to install from this %s." %
            (desktop_ram, cd))

        sentences = []
        if publish_type == "live":
            sentences.append(
                "The live %s allows you to try %s without changing your "
                "computer at all, and at your option to install it "
                "permanently later.</p>" % (cd, capproject))
        elif publish_type == "desktop":
            sentences.append(
                "The desktop %s allows you to try %s without changing your "
                "computer at all, and at your option to install it "
                "permanently later." % (cd, capproject))
            if self.project != "edubuntu" and not self.prefmsg_emitted:
                sentences.append(
                    "This type of %s is what most people will want to use." %
                    cd)
                self.prefmsg_emitted = True
            sentences.append(desktop_req)
            if self.project == "edubuntu":
                sentences.append(
                    "You can install additional educational programs using "
                    "the classroom server add-on %s." % cd)
        elif publish_type == "install":
            sentences.append(
                "The install %s allows you to install %s permanently on a "
                "computer." % (cd, capproject))
        elif publish_type == "alternate":
            sentences.append(
                "The alternate install %s allows you to perform certain "
                "specialist installations of %s." % (cd, capproject))
            sentences.append("It provides for the following situations:")
            yield Paragraph(sentences)
            yield UnorderedList([
                "setting up automated deployments;",
                "upgrading from older installations without network access;",
                "LVM and/or RAID partitioning;",
                ("installs on systems with less than about %sMiB of RAM "
                    "(although note that low-memory systems may not be able "
                    "to run a full desktop environment reasonably)." %
                    desktop_ram),
            ])
            bug_link = Link(
                "https://bugs.launchpad.net/ubuntu/+source/debian-installer/"
                "+filebug",
                "debian-installer")
            yield Paragraph([
                "In the event that you encounter a bug using the alternate "
                "installer, please file a bug on the %s package." % bug_link,
            ])
            return
        elif publish_type == "mid":
            sentences.append(
                "The MID USB image allows you to try %s without changing your "
                "computer at all, and at your option to install it "
                "permanently later." % capproject)
            sentences.append(
                "This USB image is optimized for handheld devices with 4-7\" "
                "touchscreens and limited processing power.")
            sentences.append(desktop_req)
        elif publish_type == "moblin-remix":
            sentences.append(
                "The live %s allows you to try Ubuntu Moblin Remix without "
                "changing your computer at all, and at your option to install "
                "it permanently later." % cd)
            sentences.append(
                "This live %s is optimized for netbooks with screens up to "
                "10\"." % cd)
            sentences.append(desktop_req)
        elif publish_type == "server":
            if self.project == "edubuntu":
                sentences.append(
                    "The classroom server %s allows you to install %s "
                    "permanently on a computer." % (cd, capproject))
                sentences.append(
                    "It includes LTSP (Linux Terminal Server Project) "
                    "support, providing out-of-the-box thin client support.")
                sentences.append(
                    "After installation you can install additional "
                    "educational programs using the classroom server add-on "
                    "%s." % cd)
            else:
                sentences.append(
                    "The server install %s allows you to install %s "
                    "permanently on a computer for use as a server." %
                    (cd, capproject))
                sentences.append(
                    "It will not install a graphical user interface.")
        elif publish_type == "netbook":
            if capproject.endswith("-Netbook"):
                capproject = capproject[:-len("-Netbook")]
            sentences.append(
                "The live %s allows you to try %s Netbook Edition without "
                "changing your computer at all, and at your option to install "
                "it permanently later." % (cd, capproject))
            sentences.append(
                "This live %s is optimized for netbooks with screens up to "
                "10\"." % cd)
            sentences.append(desktop_req)
        elif publish_type == "active":
            # Kubuntu only
            sentences.append(
                "The Active Image offers a preview of the Plasma Active "
                "workspace to try or install.")
        elif publish_type == "serveraddon":
            # Edubuntu only
            sentences.append(
                "The classroom server add-on %s contains additional useful "
                "packages, including many educational programs and all "
                "available language packs." % cd)
            sentences.append(
                "It requires that an %s desktop be installed on the machine." %
                capproject)
        elif publish_type == "addon":
            # Edubuntu only
            sentences.append(
                "The Ubuntu educational add-on %s contains additional useful "
                "packages, including many educational programs." % cd)
            sentences.append(
                "It requires that an Ubuntu desktop system already be "
                "installed.")
        elif publish_type == "dvd":
            if self.project == "edubuntu":
                sentences.append(
                    "The install DVD allows you to install %s permanently on "
                    "a computer." % capproject)
            else:
                sentences.append(
                    "The combined install/live DVD allows you either to "
                    "install %s permanently on a computer, or (by entering "
                    "'live' at the boot prompt) to try %s without changing "
                    "your computer at all." % (capproject, capproject))
        elif publish_type == "src":
            yield Paragraph([
                "The source %ss contain the source code used to build %s." %
                (cd, capproject),
            ])
            sentences.append(
                "Some source package versions on this image may not match "
                "related binary images, depending on exactly when the images "
                "were built.")
            sentences.append(
                "You can always find every version of Ubuntu source packages "
                "on Launchpad, using URLs of the following form:")
            yield Paragraph(sentences)
            prefix = "https://launchpad.net/ubuntu/+source/SOURCE-PACKAGE-NAME"
            yield UnorderedList([
                "<code>%s/+publishinghistory</code> (index)" % prefix,
                "<code>%s/VERSION</code> (specific version)" % prefix,
            ])
            return
        elif publish_type in ("server-uec", "uec"):
            uec_link = Link(
                "http://www.ubuntu.com/products/whatisubuntu/serveredition/"
                "cloud/uec",
                "Ubuntu Enterprise Cloud", show_class=True)
            sentences.append(
                "The Ubuntu Enterprise Cloud image can be run on your "
                "personal %s, or modified, rebundled and uploaded to Amazon "
                "EC2." % uec_link)
            gs_link = Link(
                "https://help.ubuntu.com/community/Eucalyptus",
                "Getting Started with Ubuntu Enterprise Cloud",
                show_class=True)
            sentences.append(
                "For further instruction on setting up a personal Ubuntu "
                "Enterprise Cloud, see %s." % gs_link)
        elif publish_type == "preinstalled-active":
            sentences.append(
                "The Active Image allows you to unpack a preinstalled preview "
                "of the Plasma Active workspace onto an SD card.")
        elif publish_type == "preinstalled-touch":
            sentences.append(
                "The Preinstalled Touch Image allows you to install a "
                "preinstalled preview of Ubuntu Touch onto a target device.")
        elif publish_type.startswith("preinstalled-"):
            sentences.append(
                "The %s %s allows you to unpack a preinstalled version of %s "
                "onto a target device." % (publish_type, cd, capproject))
        elif publish_type == "ubuntu-core":
            sentences.append(
                "Ubuntu Core is a minimal rootfs for use in the creation of "
                "custom images for specific needs.")
            sentences.append(
                "Ubuntu Core strives to create a suitable minimal environment "
                "for use in Board Support Packages, constrained or integrated "
                "environments, or as the basis for application demonstration "
                "images.")
            link = Link(
                "https://wiki.ubuntu.com/Core", "Ubuntu Core wiki page",
                show_class=True)
            sentences.append("See the %s for more information." % link)
        elif publish_type == "wubi":
            sentences.append(
                "This is a filesystem image downloaded by Wubi (a system "
                "which installs Ubuntu into disk image files on a Windows "
                "filesystem).  You should not normally need to download it "
                "separately.")
        else:
            raise WebIndicesException("Unknown image type %s!" % publish_type)

        if sentences:
            yield Paragraph(sentences)

    uec_arch_strings = {
        "amd64": "64-bit",
        "i386": "32-bit",
    }

    arch_strings = {
        "amd64": "64-bit PC (AMD64)",
        "amd64+mac": "64-bit Mac (AMD64)",
        "armel": "ARM EABI",
        "armel+dove": "Marvell Dove",
        "armel+imx51": "Freescale i.MX51",
        "armel+omap": "Texas Instruments OMAP3",
        "armel+omap4": "Texas Instruments OMAP4",
        "armel+ac100": "Toshiba AC100 / Dynabook AZ",
        "armel+mx5": "Freescale i.MX5x",
        "armhf": "ARM EABI (Hard-Float)",
        "armhf+omap": "Texas Instruments OMAP3 (Hard-Float)",
        "armhf+omap4": "Texas Instruments OMAP4 (Hard-Float)",
        "armhf+ac100": "Toshiba AC100 / Dynabook AZ (Hard-Float)",
        "armhf+mx5": "Freescale i.MX5x (Hard-Float)",
        "armhf+nexus7": "Asus/Google Nexus7 Tablet",
        "hppa": "HP PA-RISC",
        "i386": "PC (Intel x86)",
        "ia64": "IA-64",
        "lpia": "Low-Power Intel Architecture",
        "powerpc": "Mac (PowerPC) and IBM-PPC (POWER5)",
        "powerpc+ps3": "PlayStation 3",
        "sparc": "SPARC",
    }

    def archdesc(self, arch, publish_type):
        sentences = []
        if arch in ("amd64", "amd64+mac"):
            sentences.append(
                "Choose this to take full advantage of computers based on the "
                "AMD64 or EM64T architecture (e.g., Athlon64, Opteron, EM64T "
                "Xeon, Core 2).")
            sentences.append(
                "If you have a non-64-bit processor made by AMD, or if you "
                "need full support for 32-bit code, use the Intel x86 images "
                "instead.")
            if arch == "amd64+mac":
                sentences.append(
                    "This image is adjusted to work properly on Mac systems.")
        elif arch == "armel":
            sentences.append("For ARMv7 processors and above.")
        elif arch == "armel+dove":
            sentences.append("For Dove boards.")
        elif arch == "armel+imx51":
            sentences.append("For i.MX51 boards.")
        elif arch in ("armel+mx5", "armhf+mx5"):
            sentences.append("For Freescale i.MX5x boards.")
            link = Link("https://wiki.ubuntu.com/ARM/MX5", "ARM/MX5")
            sentences.append(
                "See %s for detailed installation information." % link)
        elif arch in ("armel+omap", "armhf+omap"):
            sentences.append("For OMAP3 boards.")
            link = Link("https://wiki.ubuntu.com/ARM/OMAP", "ARM/OMAP")
            sentences.append(
                "See %s for detailed installation information." % link)
        elif arch in ("armel+omap4", "armhf+omap4"):
            sentences.append("For OMAP4 boards.")
            link = Link("https://wiki.ubuntu.com/ARM/OMAP", "ARM/OMAP")
            sentences.append(
                "See %s for detailed installation information." % link)
        elif arch in ("armel+ac100", "armhf+ac100"):
            sentences.append("For Toshiba AC100 / Dynabook AZ netbooks.")
            link = Link(
                "https://wiki.ubuntu.com/ARM/TEGRA/AC100", "ARM/TEGRA/AC100")
            sentences.append(
                "See %s for detailed installation information (please make "
                "sure to download the .bootimg file alongside with the "
                "filesystem archive)." % link)
        elif arch == "armhf+nexus7":
            sentences.append("For the Asus/Google Nexus7 tablet.")
            link = Link(
                "https://wiki.ubuntu.com/Nexus7", "the Nexus7 wiki pages")
            sentences.append(
                "See %s for detailed installation information." % link)
        elif arch == "armhf":
            sentences.append("For ARMv7 processors and above (Hard-Float).")
        elif arch == "hppa":
            sentences.append("For HP PA-RISC computers.")
        elif arch == "i386":
            sentences.append("For almost all PCs.")
            sentences.append(
                "This includes most machines with Intel/AMD/etc type "
                "processors and almost all computers that run Microsoft "
                "Windows, as well as newer Apple Macintosh systems based on "
                "Intel processors.")
            sentences.append("Choose this if you are at all unsure.")
        elif arch == "ia64":
            sentences.append("For Intel Itanium and Itanium 2 computers.")
        elif arch == "lpia":
            sentences.append(
                "For devices using the Low-Power Intel Architecture, "
                "including the A1xx and Atom processors.")
        elif arch == "powerpc":
            sentences.append(
                "For Apple Macintosh G3, G4, and G5 computers, including "
                "iBooks and PowerBooks as well as IBM OpenPower machines.")
        elif arch == "powerpc+ps3":
            sentences.append("For Sony PlayStation 3 systems.")
            if publish_type == "desktop" and self.config["DIST"] >= "gutsy":
                capproject = self.config.capproject
                sentences.append(
                    "(This defaults to installing %s permanently, since there "
                    "is usually not enough memory to try out the full desktop "
                    "system and run the installer at the same time." %
                    capproject)
                sentences.append(
                    "An alternative boot option to try %s without changing "
                    "your computer is available.)" % capproject)
        elif arch == "sparc":
            sentences.append(
                "For Sun UltraSPARC computers, including those based on the "
                "multicore UltraSPARC T1 (\"Niagara\") processors.")
        else:
            raise WebIndicesException("Unknown architecture %s!" % arch)
        return "  ".join(sentences)

    def maybe_oversized(self, status, path, publish_type):
        if status != "daily" or not os.path.exists(path):
            return

        usb_projects = (
            "ubuntu-mid", "ubuntu-moblin-remix", "kubuntu", "kubuntu-active")
        series = self.config["DIST"]

        yield "<br>"
        sentences = []
        if publish_type == "dvd" or self.project == "ubuntustudio":
            sentences.append(
                "Warning: This image is oversized (which is a bug) and will "
                "not fit onto a single-sided single-layer DVD.")
            sentences.append(
                "However, you may still test it using a larger USB drive or a "
                "virtual machine.")
        elif (self.project in usb_projects or
                (self.project == "xubuntu" and series >= "raring")):
            sentences.append(
                "Warning: This image is oversized (which is a bug) and will "
                "not fit onto a 1GB USB stick.")
            sentences.append(
                "However, you may still test it using a DVD, a larger USB "
                "drive, or a virtual machine.")
        else:
            sentences.append(
                "Warning: This image is oversized (which is a bug) and will "
                "not fit onto a standard 703MiB CD.")
            sentences.append(
                "However, you may still test it using a DVD, a USB drive, or "
                "a virtual machine.")
        yield Span("urgent", sentences)

    def mimetypestr(self, extension):
        # Some MIME types aren't configured by default.
        if extension == "img":
            return "application/octet-stream"
        else:
            return None

    def extensionstr(self, extension):
        if extension == "img":
            return "USB image"
        elif extension == "img.gz":
            return "preinstalled SD Card image"
        elif extension == "iso":
            return "standard download"
        elif extension.endswith(".torrent"):
            return "%s download" % Link(
                "https://help.ubuntu.com/community/BitTorrent", "BitTorrent")
        elif extension == "jigdo":
            return "%s download" % Link("http://atterer.org/jigdo", "jigdo")
        elif extension == "list":
            return "file listing"
        elif extension == "manifest":
            return "contents of live filesystem"
        elif extension == "manifest-desktop":
            return "contents of desktop part of live filesystem"
        elif extension == "manifest-remove":
            return "packages to remove from live filesystem on installation"
        elif extension == "template":
            return "%s template" % Link("http://atterer.org/jigdo", "jigdo")
        elif extension.endswith(".zsync"):
            return "%s metafile" % Link("http://zsync.moria.org.uk/", "zsync")
        elif extension == "vmlinuz-ec2":
            return "EC2 kernel image"
        elif extension == "vmlinuz-virtual":
            return "UEC kernel image"
        elif extension == "initrd-ec2":
            return "EC2 initramfs image"
        elif extension == "initrd-virtual":
            return "UEC initramfs image"
        elif extension == "img.tar.gz":
            return "UEC/EC2 filesystem image"
        elif extension == "tar.gz":
            if self.project in ("server-uec", "uec"):
                return "Cloud Images tarball"
            else:
                return "filesystem archive"
        elif extension == "bootimg":
            return "combined Android bootimage"
        elif extension == "tar.xz":
            return "Wubi filesystem archive"
        else:
            raise WebIndicesException("Unknown extension %s!" % extension)

    def web_heading(self, prefix):
        full_project_bits = [self.project]
        if self.config["UBUNTU_DEFAULTS_LOCALE"]:
            full_project_bits.append(self.config["UBUNTU_DEFAULTS_LOCALE"])
        full_project = "-".join(full_project_bits)
        series = self.config["DIST"]

        heading = "%s %s (%s)" % (
            self.config.capproject, series.displayversion(full_project),
            series.displayname)
        if "-alpha-" in prefix:
            heading += " Alpha %s" % re.sub(r"^.*-alpha-", "", prefix)
        elif prefix.endswith("-preview"):
            heading += " Preview"
        elif prefix.endswith("-beta"):
            heading += " Beta"
        elif "-beta" in prefix:
            heading += " Beta %s" % re.sub(r"^.*-beta", "", prefix)
        elif prefix.endswith("-rc"):
            heading += " Release Candidate"
        elif prefix == series.name:
            heading += " Daily Build"
        heading = heading.replace('-', ' ')
        return heading

    def ubuntu_touch_legal_notice(self):
        yield "<h3>Legal Notice</h3>"
        yield Paragraph([
            "Ubuntu Touch is released for free non-commercial use.",
            "It is provided without warranty, even the implied warranty of "
            "merchantability, satisfaction or fitness for a particular use.",
            "See the licence included with each program for details.",
        ])
        yield Paragraph([
            "Some licences may grant additional rights; this notice shall not "
            "limit your rights under each program's licence.",
            "Licences for each program are available in the /usr/share/doc "
            "directory.",
            "Source code for Ubuntu can be downloaded from %s." % Link(
                "http://archive.ubuntu.com/", "archive.ubuntu.com"),
            "Ubuntu, the Ubuntu logo and Canonical are registered trademarks "
            "of Canonical Ltd.",
            "All other trademarks are the property of their respective "
            "owners.",
        ])
        yield Paragraph([
            "Ubuntu Touch is released for limited use due to the inclusion of "
            "binary hardware support files.",
            "The original components and licenses can be found at: %s." % Link(
                "https://developers.google.com/android/nexus/drivers",
                "https://developers.google.com/android/nexus/drivers"),
        ])

    def find_images(self, directory, prefix, publish_type):
        images = []
        prefix_type = "%s-%s" % (prefix, publish_type)
        for entry in os.listdir(directory):
            if entry == ("%s.img" % prefix_type):
                images.append(entry)
            elif publish_type == "wubi" and entry.endswith(".tar.xz"):
                # Wubi images are just "ARCH.tar.xz", with no prefix.
                images.append(entry)
            elif entry.startswith("%s-" % prefix_type):
                if (entry.endswith(".list") or
                        entry.endswith(".img.gz") or
                        entry.endswith(".tar.gz")):
                    images.append(entry)
        return images

    def find_source_images(self, directory, prefix):
        numbers = []
        for entry in osextras.listdir_force(directory):
            match = re.match(r"^%s-src-([0-9]+)\.iso$" % prefix, entry)
            if match is not None:
                numbers.append(int(match.group(1)))
        return sorted(numbers)

    def find_any_with_extension(self, directory, extension):
        return bool([
            entry for entry in os.listdir(directory)
            if entry.endswith(".%s" % extension)])

    def make_web_indices(self, directory, base_prefix, status="release"):
        series = self.config["DIST"]

        prefixes = [base_prefix]
        if base_prefix.count(".") >= 2:
            # point release - need the base version too
            prefixes.append(base_prefix.rsplit(".", 1)[0])

        all_publish_types = (
            "live", "desktop",
            "server", "install", "alternate",
            "serveraddon", "addon",
            "dvd",
            "src",
            "netbook", "mid", "moblin-remix", "mobile", "active",
            "uec", "server-uec",
            "preinstalled-desktop", "preinstalled-netbook",
            "preinstalled-mobile", "preinstalled-active",
            "preinstalled-headless", "preinstalled-server",
            "preinstalled-touch", "wubi",
        )

        all_arches = (
            "i386",
            "amd64", "amd64+mac",
            "armel+dove", "armel+imx51", "armel+omap", "armel+omap4",
            "armel+ac100", "armel+mx5",
            "armhf+omap", "armhf+omap4", "armhf+ac100", "armhf+mx5",
            "armhf+nexus7", "armhf", "armel"
            "powerpc",
            "powerpc+ps3",
            "hppa",
            "ia64",
            "lpia",
            "sparc",
        )

        self.prefmsg_emitted = False

        header_path = os.path.join(directory, "HEADER.html")
        footer_path = os.path.join(directory, "FOOTER.html")
        htaccess_path = os.path.join(directory, ".htaccess")

        with AtomicFile(header_path) as header, \
                AtomicFile(footer_path) as footer, \
                AtomicFile(htaccess_path) as htaccess:
            heading = self.web_heading(base_prefix)
            print(
                dedent("""\
                    <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN"
                     "http://www.w3.org/TR/html4/strict.dtd">
                    <html>
                    <head>
                    <title>%s</title>
                    <!-- Main style sheets for CSS2 capable browsers -->
                    <style type="text/css" media="screen">""") % heading,
                file=header)
            for css in self.cssincludes():
                print("  @import url(%s);" % css, file=header)
            if self.project == "kubuntu":
                # TODO: move this into CSS, as done in /include/style.css?
                print(
                    "<link "
                    "href='http://fonts.googleapis.com/css?family=Ubuntu' "
                    "rel='stylesheet' type='text/css'>", file=header)
                print(
                    "<link rel=\"icon\" type=\"image/png\" "
                    "href=\"http://www.kubuntu.org/themes/kubuntu10.04/"
                    "favicon.ico\">", file=header)
            print(dedent("""\
                </style>
                </head>
                <body><div id="pageWrapper">

                <div id="header"><a href="http://www.ubuntu.com/"></a></div>

                <h1>%s</h1>

                <div id="main">
                """) % heading, file=header)

            mirrors_url = "http://www.ubuntu.com/getubuntu/downloadmirrors"
            reldir = os.path.realpath(directory)
            if ("full" in reldir.split(os.pardir) and
                    "-alpha-" not in base_prefix and
                    base_prefix != self.config.series):
                if self.project in (
                        "ubuntu", "ubuntu-server", "ubuntu-netbook"):
                    url = "http://releases.ubuntu.com/"
                elif self.project == "kubuntu" and series <= "oneiric":
                    url = "http://releases.ubuntu.com/kubuntu/"
                else:
                    url = None
                if url:
                    print(
                        "<p>This directory contains only less-used images "
                        "which are not mirrored widely.  For the most "
                        "frequently downloaded images, see "
                        "<a href=\"%s\">releases.ubuntu.com</a>.  Please "
                        "use a <a href=\"%s\">mirror</a> if possible.</p>" %
                        (url, mirrors_url), file=header)
                    print(file=header)
            elif "simple" in reldir.split(os.pardir):
                cdimage_url = "http://cdimage.ubuntu.com/"
                print(
                    "<p>This directory contains the most frequently "
                    "downloaded %s images.  Other images, including DVDs and "
                    "source CDs, may be available on the "
                    "<a href=\"%s\">cdimage server</a>.  See also the "
                    "<a href=\"%s\">list of download mirrors</a>.</p>" %
                    (self.config.capproject, cdimage_url, mirrors_url),
                    file=header)
                print(file=header)

            print("<h2>Select an image</h2>", file=header)
            print(file=header)

            cdtypecount = 0
            for prefix in prefixes:
                for publish_type in all_publish_types:
                    if self.find_images(directory, prefix, publish_type):
                        cdtypecount += 1

            if cdtypecount > 1:
                print(
                    "<p>%s is distributed on %s types of images described "
                    "below." %
                    (self.config.capproject, self.numbers[cdtypecount]),
                    file=header)
                print(file=header)

            foundtorrent = False
            bt_link = Link(
                "https://help.ubuntu.com/community/BitTorrent", "BitTorrent")

            for prefix in prefixes:
                for publish_type in all_publish_types:
                    if not self.find_images(directory, prefix, publish_type):
                        continue

                    if publish_type == "src":
                        # Perverse, but works.
                        arches = self.find_source_images(directory, prefix)
                    else:
                        arches = all_arches

                    for image_format in (
                        "iso", "img", "img.gz", "img.tar.gz", "tar.gz",
                        "tar.xz",
                    ):
                        paths = []
                        if image_format == "img":
                            path = os.path.join(
                                directory,
                                "%s-%s.%s" % (
                                    prefix, publish_type, image_format))
                            if os.path.exists(path):
                                paths.append((path, None))
                        elif image_format == "tar.xz":
                            for arch in arches:
                                path = os.path.join(
                                    directory, "%s.%s" % (arch, image_format))
                                if os.path.exists(path):
                                    paths.append((path, arch))
                        for arch in arches:
                            path = os.path.join(
                                directory,
                                "%s-%s-%s.%s" % (
                                    prefix, publish_type, arch, image_format))
                            if os.path.exists(path):
                                paths.append((path, arch))
                        if not paths:
                            continue

                        cdtypestr = self.cdtypestr(publish_type, image_format)

                        print(
                            "<h3>%s</h3>" % self.titlecase(cdtypestr),
                            file=header)
                        print(file=header)
                        for tag in self.cdtypedesc(publish_type, image_format):
                            print(tag, file=header)
                            print(file=header)

                        if len(paths) == 1:
                            print(
                                "<p>There is one image available:</p>",
                                file=header)
                        elif publish_type == "src":
                            print(
                                "<p>There are %s images available:</p>" %
                                self.numbers[len(paths)], file=header)
                        else:
                            print(
                                "<p>There are %s images available, each for a "
                                "different type of computer:</p>" %
                                self.numbers[len(paths)], file=header)

                        print(file=header)
                        print("<dl>", file=header)

                        for path, arch in paths:
                            base = path.rsplit(".", 1)[0]
                            if arch is None:
                                if publish_type == "mid":
                                    imgarch = "lpia"
                                else:
                                    raise WebIndicesException(
                                        "Unknown image type %s!" %
                                        publish_type)
                                archstr = self.arch_strings[imgarch]
                                imagestr = "%s %s" % (archstr, cdtypestr)
                                htaccessimagestr = "%s for %s computers" % (
                                    self.titlecase(cdtypestr), archstr)
                                archdesc = self.archdesc(imgarch, publish_type)
                            elif publish_type == "src":
                                imagestr = "%s %s" % (
                                    self.titlecase(cdtypestr), arch)
                                htaccessimagestr = imagestr
                            else:
                                if publish_type in ("server-uec", "uec"):
                                    archstr = self.uec_arch_strings[arch]
                                else:
                                    archstr = self.arch_strings[arch]
                                imagestr = "%s %s" % (archstr, cdtypestr)
                                htaccessimagestr = "%s for %s computers" % (
                                    self.titlecase(cdtypestr), archstr)
                                archdesc = self.archdesc(arch, publish_type)

                            if os.path.exists(path):
                                print(
                                    "<dt><a href=\"%s\">%s</a>" %
                                    (os.path.basename(path), imagestr),
                                    file=header)
                            elif os.path.exists("%s.torrent" % path):
                                print(
                                    "<dt><a href=\"%s.torrent\">%s</a> "
                                    "(%s only)" % (
                                        os.path.basename(path), imagestr,
                                        bt_link),
                                    file=header)
                            else:
                                continue

                            if os.path.exists("%s.torrent" % path):
                                foundtorrent = True

                            if publish_type != "src":
                                oversized_path = "%s.OVERSIZED" % base
                                print(file=header)
                                desc = archdesc
                                for tag in self.maybe_oversized(
                                        status, oversized_path, publish_type):
                                    desc += "\n%s" % tag
                                print("<dd>%s</dd>" % desc, file=header)
                                print(file=header)

                            if arch is None:
                                htaccess_extensions = ("img", "manifest")
                            else:
                                htaccess_extensions = (
                                    "img.gz.torrent", "img.gz.zsync", "img.gz",
                                    "img.tar.gz", "img.torrent", "img.zsync",
                                    "img", "iso.torrent", "iso.zsync", "iso",
                                    "jigdo", "list", "manifest",
                                    "manifest-desktop", "manifest-remove",
                                    "template", "tar.gz", "tar.gz.zsync",
                                    "bootimg", "tar.xz",
                                )
                            for extension in htaccess_extensions:
                                extpath = "%s.%s" % (base, extension)
                                if not os.path.exists(extpath):
                                    continue
                                extstr = self.extensionstr(extension)
                                extstr = extstr.replace('"', '\\"')
                                print(
                                    "AddDescription \"%s (%s)\" %s" % (
                                        htaccessimagestr, extstr,
                                        os.path.basename(extpath)),
                                    file=htaccess)
                            for extension in (
                                "initrd-ec2", "initrd-virtual",
                                "vmlinuz-ec2", "vmlinuz-virtual",
                            ):
                                extpath = "%s-%s" % (base, extension)
                                if not os.path.exists(extpath):
                                    continue
                                extstr = self.extensionstr(extension)
                                extstr = extstr.replace('"', '\\"')
                                print(
                                    "AddDescription \"%s (%s)\" %s" % (
                                        htaccessimagestr, extstr,
                                        os.path.basename(extpath)),
                                    file=htaccess)

                        print("</dl>", file=header)
                        print(file=header)

            published_ec2_path = os.path.join(
                directory, "published-ec2-%s.txt" % status)
            if os.path.exists(published_ec2_path):
                print("<h3>Amazon EC2 Published AMIs</h3>", file=header)
                print(file=header)
                features_link = Link(
                    "http://www.ubuntu.com/products/whatisubuntu/"
                    "serveredition/features/ec2",
                    "Amazon EC2", show_class=True)
                guide_link = Link(
                    "https://help.ubuntu.com/community/EC2StartersGuide",
                    "EC2 Starters Guide", show_class=True)
                print(str(Paragraph([
                    "The images have been published to %s, and can be used "
                    "immediately with no need to download anything." %
                    features_link,
                    "See the table below for the AMI ids.",
                    "For further instruction on getting started with Amazon "
                    "EC2, see the %s." % guide_link,
                ])), file=header)
                print(file=header)

                print(dedent("""\
                    <table><tbody><tr>
                      <td><p> Availability Zone </p></td>
                      <td><p> arch </p></td>
                      <td><p> ami </p></td>
                      <td><p> ec2 command</p></td>
                    </tr>"""), file=header)
                with open(published_ec2_path) as published_ec2:
                    for line in published_ec2:
                        if "ami" not in line:
                            continue
                        zone, ami, manifest = line.split(None, 2)
                        base_url = (
                            "http://developer.amazonwebservices.com/connect")

                        if "amd64" in manifest:
                            arch = "64-bit"
                            url = (
                                "%s/entry%21default.jspa?categoryID=223&amp;"
                                "externalID=2755&amp;fromSearchPage=true" %
                                base_url)
                            args = "--instance-type m1.large"
                        elif "i386" in manifest:
                            arch = "32-bit"
                            url = (
                                "%s/kbclick.jspa?categoryID=223&amp;"
                                "externalID=2754&amp;searchID=1818410" %
                                base_url)
                            args = "--instance-type m1.small"
                        link = Link(url, "<tt>%s</tt>" % ami, show_class=True)

                        if zone == "eu-west-1":
                            zonename = "Europe"
                            args += " --region %s" % zone
                        elif zone == "us-east-1":
                            zonename = "US"

                        command = (
                            "ec2-run-instances %s --key ${EC2_KEYPAIR} %s" %
                            (ami, args))
                        command = "<tt>%s</tt>" % command
                        print("<tr>", file=header)
                        for cell in (zonename, arch, link, command):
                            print("  <td><p>%s</p></td>" % cell, file=header)
                print("</tbody></table>", file=header)

            if (series >= "precise" and
                    [entry for entry in os.listdir(directory)
                     if "-arm" in entry]):
                link = Link(
                    "https://wiki.ubuntu.com/ARM/Server/Install",
                    "ARM/Server/Install")
                print(
                    "<p>For ARM hardware for which we do not ship "
                    "preinstalled images, see %s for detailed installation "
                    "information.</p>" % link, file=header)
                print(file=header)

            if foundtorrent:
                print(
                    "<p>A full list of available files, including %s files, "
                    "can be found below.</p>" % bt_link, file=header)
            else:
                print(
                    "<p>A full list of available files can be found "
                    "below.</p>", file=header)
            print(file=header)

            got_iso = self.find_any_with_extension(directory, "iso")
            got_img = self.find_any_with_extension(directory, "img")
            iso_link = Link(
                "https://help.ubuntu.com/community/BurningIsoHowto",
                "Image Burning Guide")
            img_link = Link(
                "https://wiki.ubuntu.com/MobileTeam/Mobile/HowTo/ImageWriting",
                "USB Image Writing Guide")
            if got_iso and got_img:
                print(
                    "<p>If you need help burning these images to disk, see "
                    "the %s or the %s.</p>" % (iso_link, img_link),
                    file=header)
            elif got_iso:
                print(
                    "<p>If you need help burning these images to disk, see "
                    "the %s.</p>" % iso_link, file=header)
            elif got_img:
                print(
                    "<p>It is recommended you have at least a 1GB USB storage "
                    "device to burn the image to.  If you need help burning "
                    "these images to disk, see the %s.</p>" % img_link,
                    file=header)
            if got_iso or got_img:
                print(file=header)

            if self.config.project == "ubuntu-touch":
                for tag in self.ubuntu_touch_legal_notice():
                    print(tag, file=header)
                print(file=header)

            print("</div></div></body></html>", file=footer)

            # We may not be mirrored to the webserver root, so calculate a
            # relative path for the icons.
            cdicons = "cdicons/"
            reldir = os.path.realpath(directory)
            while reldir and reldir != self.tree.directory:
                reldir, dirpart = os.path.split(reldir)
                if not dirpart:
                    continue
                cdicons = os.path.join(os.pardir, cdicons)
            if self.project.startswith("kubuntu"):
                cdicons = "%skubuntu-" % cdicons

            print(file=htaccess)
            print("HeaderName HEADER.html", file=htaccess)
            print("ReadmeName FOOTER.html", file=htaccess)
            print(
                "IndexIgnore .htaccess HEADER.html FOOTER.html "
                "published-ec2-daily.txt published-ec2-release.txt",
                file=htaccess)
            print(
                "IndexOptions NameWidth=* DescriptionWidth=* "
                "SuppressHTMLPreamble FancyIndexing "
                "IconHeight=22 IconWidth=22",
                file=htaccess)
            for icon, patterns in (
                ("folder.png", "^^DIRECTORY^^"),
                ("iso.png", ".iso"),
                ("img.png", ".img .tar.gz .tar.xz .zip"),
                ("jigdo.png", ".jigdo .template"),
                ("list.png", (
                    ".list .manifest .html .zsync "
                    "MD5SUMS MD5SUMS.gpg "
                    "MD5SUMS-metalink MD5SUMS-metalink.gpg "
                    "SHA1SUMS SHA1SUMS.gpg SHA256SUMS SHA256SUMS.gpg")),
                ("torrent.png", ".torrent .metalink"),
            ):
                print(
                    "AddIcon %s%s %s" % (cdicons, icon, patterns),
                    file=htaccess)

            for extension in (
                "img.gz.torrent", "img.gz", "img.torrent", "img",
                "iso.torrent", "iso", "jigdo", "list", "manifest",
                "manifest-desktop", "manifest-remove", "template",
            ):
                mimetype = self.mimetypestr(extension)
                if (mimetype and
                        self.find_any_with_extension(directory, extension)):
                    print(
                        "AddType %s .%s" % (mimetype, extension),
                        file=htaccess)

    def make_metalink(self, directory, version):
        """Create and publish metalink files."""
        osextras.unlink_force(os.path.join(directory, "MD5SUMS-metalink"))
        osextras.unlink_force(os.path.join(directory, "MD5SUMS-metalink.gpg"))

        reldir = os.path.relpath(directory, self.tree.directory)
        metalink_builder = os.path.join(
            self.config.root, "MirrorMetalink", "build.py")
        command = [
            metalink_builder, self.tree.directory, version, reldir,
            self.tree.site_name,
        ]
        try:
            if subprocess.call(command) == 0:
                metalink_checksum_directory(self.config, directory)
                return
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

        # Metalink creation failed.  Remove any stale .metalink files.
        for name in os.listdir(directory):
            if name.endswith(".metalink"):
                osextras.unlink_force(os.path.join(directory, name))


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

    def __init__(self, tree, image_type):
        super(DailyTreePublisher, self).__init__(tree, image_type)
        self.checksum_dirs = []

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
    def image_type_dir(self):
        image_type_dir = self.image_type.replace("_", "/")
        if not self.config["DIST"].is_latest:
            image_type_dir = os.path.join(self.config.series, image_type_dir)
        return image_type_dir

    @property
    def publish_base(self):
        return os.path.join(self.tree.project_base, self.image_type_dir)

    def size_limit(self, arch):
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
            # Ubuntu quantal onward has a succession of arbitrary limits.
            if self.config["DIST"] == "quantal":
                return 801000000
            elif arch == "powerpc":
                return 850000000
            else:
                return 835000000
        elif self.project == "ubuntu-gnome" and self.config["DIST"] >= "saucy":
            # Requested by darkxst in #ubuntu-release on 2013/06/28 03:29 UTC
            return 1024 * 1024 * 1024
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

    def size_limit_extension(self, arch, extension):
        """Some output file types have adjusted limits.  Cope with this."""
        # TODO: Shouldn't this be per-project/publish_type instead?
        if self.project == "edubuntu":
            return self.size_limit(arch)
        elif extension == "img" or extension.endswith(".gz"):
            return 1024 * 1024 * 1024
        else:
            return self.size_limit(arch)

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
            # https://lists.ubuntu.com/archives/ubuntu-announce/2007-February/
            #   000098.html
            if series > "edgy":
                return True
        elif cpuarch == "sparc":
            # https://lists.ubuntu.com/archives/ubuntu-devel-announce/
            #   2008-March/000400.html
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
            logger.info("Publishing %s abootimg images ..." % arch)
            shutil.move(
                "%s.bootimg" % source_prefix, "%s.bootimg" % target_prefix)

        for android_subarch in "maguro", "mako", "grouper", "manta":
            boot_img = "%s-preinstalled-boot-armhf+%s.img" % (
                self.config.series, android_subarch)
            system_img = "%s-preinstalled-system-armel+%s.img" % (
                self.config.series, android_subarch)
            recovery_img = "%s-preinstalled-recovery-armel+%s.img" % (
                self.config.series, android_subarch)
            system_zip = "%s-%s-armel+%s.zip" % (
                self.config.series, publish_type, android_subarch)

            for image in boot_img, system_img, recovery_img, system_zip:
                if os.path.exists(os.path.join(source_dir, image)):
                    logger.info("Publishing %s ..." % image)
                    shutil.move(
                        os.path.join(source_dir, image),
                        os.path.join(target_dir, image))

        if os.path.exists("%s.zip" % source_prefix):
            logger.info("Publishing %s zip file ..." % arch)
            shutil.move("%s.zip" % source_prefix, "%s.zip" % target_prefix)

        # zsync metafiles
        if osextras.find_on_path("zsyncmake"):
            logger.info("Making %s zsync metafile ..." % arch)
            osextras.unlink_force("%s.%s.zsync" % (target_prefix, extension))
            zsyncmake(
                "%s.%s" % (target_prefix, extension),
                "%s.%s.zsync" % (target_prefix, extension),
                "%s.%s" % (out_prefix, extension))

        size = os.stat("%s.%s" % (target_prefix, extension)).st_size
        if size > self.size_limit_extension(arch, extension):
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

    def publish_wubi(self, arch, date):
        source_dir = os.path.join(
            self.config.root, "scratch", self.project, self.config.series,
            self.image_type)
        source_prefix = os.path.join(source_dir, arch)
        target_dir = os.path.join(self.publish_base, date)
        target_prefix = os.path.join(target_dir, arch)

        if not os.path.exists("%s.tar.xz" % source_prefix):
            logger.warning("No filesystem for %s!" % arch)
            return

        logger.info("Publishing %s ..." % arch)
        osextras.ensuredir(target_dir)
        shutil.copy2("%s.tar.xz" % source_prefix, "%s.tar.xz" % target_prefix)
        shutil.copy2(
            "%s.manifest" % source_prefix, "%s.manifest" % target_prefix)

        yield os.path.join(
            self.project, self.image_type_dir,
            "%s-wubi-%s" % (self.config.series, arch))

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
            if osextras.find_on_path("zsyncmake"):
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
            self.make_web_indices(
                target_dir, self.config.series, status="daily")

        target_dir_source = os.path.join(target_dir, "source")
        if os.path.isdir(target_dir_source):
            checksum_directory(
                self.config, target_dir_source,
                old_directories=[self.image_output("src")],
                map_expr=r"s/\.\(img\|img\.gz\|iso\|iso\.gz\|tar\.gz\)$/.raw/")
            self.make_web_indices(
                target_dir_source, self.config.series, status="daily")

        if (self.image_type.endswith("-live") or
                self.image_type.endswith("dvd")):
            self.make_metalink(target_dir, self.config.series)

    def link(self, date, name):
        osextras.symlink_force(date, os.path.join(self.publish_base, name))

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
                    matches = True
                elif (self.config.project == "ubuntu-touch" and
                      arch == "armhf" and
                      ("-armel+" in image_base or "-armhf+" in image_base)):
                    matches = True
                else:
                    matches = False
                if matches:
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
                        osextras.symlink_force(source, target)
            for date in existing.values():
                publish_date = os.path.join(self.publish_base, date)
                if publish_date not in self.checksum_dirs:
                    self.checksum_dirs.append(publish_date)
            self.polish_directory("current")

    def current_uses_trigger(self, arch):
        """Find out whether the "current" symlink is trigger-controlled."""
        current_triggers_path = os.path.join(
            self.config.root, "production", "current-triggers")
        if not os.path.exists(current_triggers_path):
            return False
        want_project_bits = [self.project]
        if self.config.subproject:
            want_project_bits.append(self.config.subproject)
        if self.config["UBUNTU_DEFAULTS_LOCALE"]:
            want_project_bits.append(self.config["UBUNTU_DEFAULTS_LOCALE"])
        want_project = "-".join(want_project_bits)
        with open(current_triggers_path) as current_triggers:
            for line in current_triggers:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    project, image_type, series, arches = line.split(None, 3)
                    arches = arches.split()
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
        """Return a tuple of the QA tracker product for an image and the
        tracker target instance to use, or None.

        Any changes here must be coordinated with the tracker
        (iso.qa.ubuntu.com), since we can only return products that exist
        there and they are not necessarily consistently named.
        """

        product_list = os.path.join(self.config.root, "etc", "qa-products")
        with open(product_list, "r") as qaproducts:
            for line in qaproducts:
                if line.startswith("#"):
                    continue

                try:
                    entry_qaproduct, entry_project, entry_image_type, \
                        entry_publish_type, entry_arch, entry_qatarget = \
                        re.sub("\t+", "\t", line).strip().split("\t")
                except ValueError:
                    continue

                if project and entry_project != project:
                    continue

                if image_type and entry_image_type != image_type:
                    continue

                if publish_type and entry_publish_type != publish_type:
                    continue

                if arch and entry_arch != arch:
                    continue

                return (entry_qaproduct, entry_qatarget)

    def cdimage_project(self, qaproduct, qatarget):
        """Return a tuple of project, image_type, publish_type and arch
        for the provided QA tracker product and QA tracker target instance
        or None.

        This is the opposite of qa_product.
        """

        product_list = os.path.join(self.config.root, "etc", "qa-products")
        with open(product_list, "r") as qaproducts:
            for line in qaproducts:
                if line.startswith("#"):
                    continue

                try:
                    entry_qaproduct, entry_project, entry_image_type, \
                        entry_publish_type, entry_arch, entry_qatarget = \
                        re.sub("\t+", "\t", line).strip().split("\t")
                except ValueError:
                    continue

                if entry_qaproduct == qaproduct and entry_qatarget == qatarget:
                    return (entry_project, entry_image_type,
                            entry_publish_type, entry_arch)

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

            target = "%s-%s" % (product[1], dist)

            # Try to figure out the path to the OVERSIZED indicator for the
            # build.
            iso_path_bits = [self.tree.project_base]
            if image_series is not None:
                iso_path_bits.append(image_series)
            iso_path_bits.extend([image_type, date, base])
            iso_path = os.path.join(*iso_path_bits)
            if not os.path.isdir(os.path.dirname(iso_path)):
                raise Exception(
                    "Cannot post images from nonexistent directory: '%s'" %
                    os.path.dirname(iso_path))
            note = ""
            if os.path.exists("%s.OVERSIZED" % iso_path):
                note = (
                    "<strong>WARNING: This image is OVERSIZED. This should "
                    "never happen during milestone testing.</strong>")

            if tracker is None or tracker.target != target:
                tracker = ISOTracker(target=target)
            try:
                tracker.post_build(product[0], date, note=note)
            except Exception:
                traceback.print_exc()

    def publish(self, date):
        self.new_publish_dir(date)
        published = []
        self.checksum_dirs = []
        if self.config.project == "livecd-base":
            for arch in self.config.cpuarches:
                published.extend(list(self.publish_livecd_base(arch, date)))
        elif self.config.subproject == "wubi":
            for arch in self.config.arches:
                published.extend(list(self.publish_wubi(arch, date)))
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
        if current_arches:
            self.mark_current(date, current_arches)
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
            trace_dir = os.path.join(self.tree.directory, ".trace")
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
            "Purging %s images older than %d %s ..." %
            (project_image_type, days, "day" if days == 1 else "days"))
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
    def project_base(self):
        return self.directory

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
    def image_type_dir(self):
        return os.path.join(
            self.config.series, self.image_type.replace("_", "/"))

    def size_limit(self, arch):
        if self.publish_type == "dvd":
            # http://en.wikipedia.org/wiki/DVD_plus_RW
            return 4700372992
        else:
            # In the New World Order, we like round numbers, plus add
            # another 50MB for Chinese localisation overhead.
            return 850000000


class ReleaseTreeMixin:
    """Additional methods for trees containing released images."""

    def tree_suffix(self, source):
        # Publish ports/daily to ports/releases/..., etc.
        ubuntu_projects = (
            "ubuntu-server", "ubuntu-netbook", "ubuntu-mid", "ubuntu-headless")
        if "/" in source:
            project, tail = source.split("/", 1)
            if project in ubuntu_projects:
                if "/" in tail:
                    return "/%s" % tail.split("/", 1)[0]
                else:
                    return ""
            else:
                return "/%s" % source.split("/", 1)[0]
        else:
            return ""

    def publish_target(self, source):
        return self.project_base + self.tree_suffix(source)


class FullReleaseTree(DailyTree, ReleaseTreeMixin):
    """A publication tree containing released images.

    The full tree contains everything except the releases that are in the
    simple tree (so in practice it contains alpha/beta releases), and has a
    more complicated structure that ordinary users ultimately shouldn't have
    to pay too much attention to.

    See also `SimpleReleaseTree`.
    """

    def get_publisher(self, image_type, official, status=None, dry_run=False):
        return FullReleasePublisher(
            self, image_type, official, status=status, dry_run=dry_run)


class ChinaReleaseTree(ChinaDailyTree, ReleaseTreeMixin):
    def get_publisher(self, image_type, official, status=None, dry_run=False):
        return FullReleasePublisher(
            self, image_type, official, status=status, dry_run=dry_run)


class SimpleReleaseTree(Tree, ReleaseTreeMixin):
    """A publication tree containing a few important releases.

    The simple tree is intended for smaller mirrors and for ease of use by
    naïve end users.  It contains a pool of images and a tree per release of
    symlinks into that pool with filenames that include the status of the
    image.

    See also `FullReleaseTree`.
    """

    def __init__(self, config, directory=None):
        if directory is None:
            directory = os.path.join(config.root, "www", "simple")
        super(SimpleReleaseTree, self).__init__(config, directory)

    def get_publisher(self, image_type, official, status=None, dry_run=False):
        return SimpleReleasePublisher(
            self, image_type, official, status=status, dry_run=dry_run)

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


class TorrentTree(Tree, ReleaseTreeMixin):
    """A publication tree containing images for use by a BitTorrent tracker."""

    def __init__(self, config, directory=None):
        if directory is None:
            directory = os.path.join(config.root, "www", "torrent")
        super(TorrentTree, self).__init__(config, directory)


class PublishReleaseException(Exception):
    pass


class ReleasePublisher(Publisher):
    """An object that can publish releases of images.

    Releases are always copies of a nominated daily build.
    """

    torrent_tracker = "http://torrent.ubuntu.com:6969/announce"
    ipv6_torrent_tracker = "http://ipv6.torrent.ubuntu.com:6969/announce"

    def __init__(self, tree, image_type, official, status=None, dry_run=False):
        super(ReleasePublisher, self).__init__(tree, image_type)
        self.official = official
        self.status = status if status else "release"
        self.dry_run = dry_run

    def daily_dir(self, source, date, publish_type):
        daily_tree = Tree.get_daily(self.config)
        daily_dir = os.path.join(daily_tree.project_base, source, date)
        if not os.path.isdir(daily_dir) and "/" in date:
            daily_dir = os.path.join(daily_tree.directory, date)
        if publish_type == "src":
            daily_dir = os.path.join(daily_dir, "source")
        return daily_dir

    def daily_base(self, source, date, publish_type, arch):
        series = self.config["DIST"]
        daily_dir = self.daily_dir(source, date, publish_type)
        if publish_type in ("netbook", "mid") and series <= "intrepid":
            return os.path.join(
                daily_dir, "%s-%s" % (self.project, publish_type))
        elif publish_type == "wubi":
            return os.path.join(daily_dir, arch)
        else:
            return os.path.join(
                daily_dir, "%s-%s-%s" % (series, publish_type, arch))

    def target_dir(self, source, date, publish_type):
        raise NotImplementedError

    def version_link(self, source):
        raise NotImplementedError

    def pool_dir(self, source):
        raise NotImplementedError

    def torrent_dir(self, source, publish_type):
        raise NotImplementedError

    def make_torrent(self, path):
        if not self.dry_run:
            logger.info("Creating torrent for %s ..." % path)
        osextras.unlink_force("%s.torrent" % path)
        command = ["btmakemetafile", self.torrent_tracker]
        if isinstance(self.tree, SimpleReleaseTree):
            # N.B.: Only the bittornado version of btmakemetafile has
            # the --announce_list flag.
            command.extend([
                "--announce_list",
                "%s|%s" % (
                    self.torrent_tracker, self.ipv6_torrent_tracker),
            ])
        command.extend([
            "--comment",
            "%s CD %s" % (self.config.capproject, self.tree.site_name),
            path,
        ])
        if self.dry_run:
            logger.info(
                " ".join(osextras.shell_escape(arg) for arg in command))
        else:
            with open("/dev/null", "w") as devnull:
                subprocess.check_call(command, stdout=devnull)

    def make_torrents(self, directory, prefix):
        images = []
        for entry in osextras.listdir_force(directory):
            if not entry.endswith(".iso") and not entry.endswith(".img"):
                continue
            if (entry.startswith("%s-" % prefix) or
                    entry == "%s.iso" % prefix or
                    entry == "%s.img" % prefix):
                images.append(entry)

        for image in sorted(images):
            self.make_torrent(os.path.join(directory, image))

    @property
    def version(self):
        series = self.config["DIST"]
        return getattr(series, "pointversion", series.version)

    @property
    def metalink_version(self):
        if self.project == "ubuntu":
            return self.version
        else:
            return os.path.join(self.project, self.version)

    def publish_release_prefixes(self):
        # "beta-2" should end up in directories named "beta-2", but with
        # filenames including "beta2" (otherwise we get hyphen overload).
        if self.status.startswith("release"):
            filestatus = ""
        else:
            filestatus = self.status.replace("-", "")

        if self.official in ("yes", "poolonly", "named"):
            prefix = "%s-%s" % (self.project, self.version)
        else:
            prefix = self.config.series

        prefix_status = prefix
        if filestatus:
            prefix_status += "-%s" % filestatus
        if self.official == "named":
            prefix = prefix_status

        return prefix, prefix_status

    def do(self, msg, func, *args, **kwargs):
        if self.dry_run:
            logger.info(msg)
        else:
            func(*args, **kwargs)

    def remove_checksum(self, directory, name):
        if self.dry_run:
            logger.info("checksum-remove --no-sign %s %s" % (directory, name))
        else:
            with ChecksumFileSet(self.config, directory, sign=False) as files:
                files.remove(name)

    def copy(self, source, target):
        self.do("cp -a %s %s" % (source, target), shutil.copy2, source, target)
        self.remove_checksum(os.path.dirname(target), os.path.basename(target))

    def symlink(self, source, link_name):
        relpath = os.path.relpath(source, os.path.dirname(link_name))
        self.do(
            "ln -sf %s %s" % (relpath, link_name),
            osextras.symlink_force, relpath, link_name)
        self.remove_checksum(
            os.path.dirname(link_name), os.path.basename(link_name))

    def hardlink(self, source, link_name):
        self.do(
            "ln -f %s %s" % (source, link_name),
            osextras.link_force, source, link_name)

    def remove(self, path):
        self.do("rm -f %s" % path, osextras.unlink_force, path)

    def remove_tree(self, path):
        self.do("rm -rf %s" % path, shutil.rmtree, path)

    def copy_jigdo(self, source, target):
        if self.dry_run:
            logger.info("Would fix up jigdo file")
            return
        source_pat = "=%s" % os.path.basename(source).rsplit(".", 1)[0]
        target_pat = "=%s" % os.path.basename(target).rsplit(".", 1)[0]
        with open(source) as sf, open(target, "w") as tf:
            for line in sf:
                tf.write(line.replace(source_pat, target_pat))

    def mkemptydir(self, path):
        if self.dry_run:
            logger.info("rm -rf %s" % path)
            logger.info("mkdir -p %s" % path)
        else:
            osextras.mkemptydir(path)

    def checksum_directory(self, dirs, map_expr=None):
        self.do(
            "checksum-directory %s%s" % (
                "--map %s " % map_expr if map_expr else "",
                " ".join(dirs)),
            checksum_directory,
            self.config, dirs[0], old_directories=dirs, map_expr=map_expr)

    def metalink_checksum_directory(self, dirs):
        self.do(
            "checksum-directory --metalink %s" % " ".join(dirs),
            metalink_checksum_directory,
            self.config, dirs[0], old_directories=dirs)

    def want_manifest(self, publish_type, path):
        if publish_type in (
            "live", "desktop", "netbook", "mid", "moblin-remix",
            "uec", "server-uec", "core", "wubi",
        ):
            return True
        elif publish_type == "dvd" and os.path.exists(path):
            # DVDs are allowed to not have .manifest files, but may have
            # them depending on configuration.
            return True
        else:
            return False

    def want_torrent(self, publish_type):
        raise NotImplementedError

    def want_metalink(self, publish_type):
        # TODO: maybe others?  metalink is only supported for Wubi
        if publish_type in (
            "netbook", "mid", "moblin-remix", "uec", "server-uec",
        ):
            return False
        elif publish_type.startswith("preinstalled-"):
            return False
        else:
            return True

    def publish_release_arch(self, source, date, publish_type, arch):
        """Publish release images for a single architecture."""
        logger.info("Copying %s-%s image ..." % (publish_type, arch))

        base = self.daily_base(source, date, publish_type, arch)
        prefix, prefix_status = self.publish_release_prefixes()
        base_plain = "%s-%s-%s" % (prefix, publish_type, arch)
        base_status = "%s-%s-%s" % (prefix_status, publish_type, arch)

        def daily(ext, sep="."):
            return "%s%s%s" % (base, sep, ext)

        def pool(ext, sep="."):
            return os.path.join(
                self.pool_dir(source), "%s%s%s" % (base_status, sep, ext))

        def dist(ext, sep="."):
            return os.path.join(
                self.target_dir(source, date, publish_type),
                "%s%s%s" % (base_status, sep, ext))

        def full(ext, sep="."):
            return os.path.join(
                self.target_dir(source, date, publish_type),
                "%s%s%s" % (base_plain, sep, ext))

        def torrent(ext, sep="."):
            torrent_dir = self.torrent_dir(source, publish_type)
            if self.want_dist:
                return os.path.join(
                    torrent_dir, "%s%s%s" % (base_status, sep, ext))
            else:
                assert self.want_full
                return os.path.join(
                    torrent_dir, "%s%s%s" % (base_plain, sep, ext))

        for ext in "iso", "img", "img.gz", "tar.gz", "img.tar.gz", "tar.xz":
            if os.path.exists(daily(ext)):
                break
        else:
            return

        # Copy, to make sure we have a canonical version of this.
        for ext in (
            "iso", "list", "img", "img.gz", "tar.gz", "img.tar.gz", "tar.xz",
            "bootimg", "bootimg-maguro", "bootimg-mako", "bootimg-grouper",
            "bootimg-manta"
        ):
            if not os.path.exists(daily(ext)):
                continue
            if self.want_pool:
                self.copy(daily(ext), pool(ext))
            if self.want_dist:
                self.symlink(pool(ext), dist(ext))
            if self.want_full:
                self.copy(daily(ext), full(ext))

        for ext in (
            "initrd-ec2", "initrd-virtual", "vmlinuz-ec2", "vmlinuz-virtual",
        ):
            if not os.path.exists(daily(ext, "-")):
                continue
            if self.want_pool:
                self.copy(daily(ext, "-"), pool(ext, "-"))
            if self.want_dist:
                self.symlink(pool(ext, "-"), dist(ext, "-"))
            if self.want_full:
                self.copy(daily(ext, "-"), full(ext, "-"))

        for ext in ("kernel-info.txt", ):
            if not os.path.exists(daily(ext, "-")):
                continue
            if self.want_dist:
                self.copy(daily(ext, "-"), dist(ext, "-"))
            if self.want_full:
                self.copy(daily(ext, "-"), full(ext, "-"))

        if publish_type in (
            "install", "alternate", "server", "serveraddon", "addon", "src",
        ):
            if (os.path.exists(daily("jigdo")) and
                    os.path.exists(daily("template"))):
                if self.want_pool:
                    self.copy(daily("template"), pool("template"))
                    self.copy_jigdo(daily("jigdo"), pool("jigdo"))
                if self.want_dist:
                    self.symlink(pool("template"), dist("template"))
                    self.symlink(pool("jigdo"), dist("jigdo"))
                if self.want_full:
                    self.copy(daily("template"), full("template"))
                    self.copy_jigdo(daily("jigdo"), full("jigdo"))
            else:
                if self.want_pool:
                    self.remove(pool("template"))
                    self.remove(pool("jigdo"))
                if self.want_dist:
                    self.remove(dist("template"))
                    self.remove(dist("jigdo"))
                if self.want_full:
                    self.remove(full("template"))
                    self.remove(full("jigdo"))

        if self.want_manifest(publish_type, daily("manifest")):
            # Copy, to make sure we have a canonical version of this.
            if self.want_pool:
                self.copy(daily("manifest"), pool("manifest"))
            if self.want_dist:
                self.symlink(pool("manifest"), dist("manifest"))
            if self.want_full:
                self.copy(daily("manifest"), full("manifest"))

        for ext in "iso", "img", "img.gz", "tar.gz":
            zsyncext = "%s.zsync" % ext
            if not os.path.exists(daily(zsyncext)):
                continue
            if self.want_pool:
                if osextras.find_on_path("zsyncmake"):
                    logger.info("Making %s zsync metafile ..." % arch)
                    self.remove(pool(zsyncext))
                    zsyncmake(
                        pool(ext), pool(zsyncext), os.path.basename(pool(ext)),
                        dry_run=self.dry_run)
            elif self.want_full and self.official == "named":
                if osextras.find_on_path("zsyncmake"):
                    logger.info("Making %s zsync metafile ..." % arch)
                    self.remove(full(zsyncext))
                    zsyncmake(
                        full(ext), full(zsyncext), os.path.basename(full(ext)),
                        dry_run=self.dry_run)
            elif self.want_full:
                self.copy(daily(zsyncext), full(zsyncext))
            if self.want_dist:
                self.symlink(pool(zsyncext), dist(zsyncext))

        if self.want_torrent(publish_type):
            # Create and publish torrents.
            assert self.want_dist != self.want_full
            for ext in "iso", "img":
                torrentext = "%s.torrent" % ext
                if self.want_dist:
                    if os.path.exists(dist(ext)):
                        self.make_torrent(dist(ext))
                    if os.path.exists(pool(ext)):
                        self.hardlink(pool(ext), torrent(ext))
                        self.hardlink(dist(torrentext), torrent(torrentext))
                else:
                    if os.path.exists(full(ext)):
                        self.make_torrent(full(ext))
                    if os.path.exists(full(ext)):
                        self.hardlink(full(ext), torrent(ext))
                        self.hardlink(full(torrentext), torrent(torrentext))

    def publish_release(self, source, date, publish_type):
        """Publish a daily build as a release."""
        series = self.config["DIST"]
        arches = self.config.arches
        prefix, prefix_status = self.publish_release_prefixes()

        # Do what I mean.
        if source.endswith("/source"):
            source = source[:-len("/source")]

        if not series.is_latest:
            if source == "ubuntu-server/daily":
                source = os.path.join("ubuntu-server", series.name, "daily")
            else:
                source = os.path.join(series.name, source)

        daily_dir = self.daily_dir(source, date, publish_type)
        target_dir = self.target_dir(source, date, publish_type)
        if not self.want_full:
            pool_dir = self.pool_dir(source)

        if publish_type == "src":
            # Perverse, but works.
            arches = self.find_source_images(daily_dir, series.name)
            # Sanity-check.
            if not arches:
                raise PublishReleaseException(
                    "No source daily for %s on %s!" % (series, date))

        # Override the architecture list for these types unconditionally.
        # TODO: should reset default-arches for the source project instead
        if (publish_type in ("netbook", "moblin-remix") and
                not [arch for arch in arches if arch.startswith("armel")]):
            arches = ["i386"]
        elif publish_type == "mid":
            arches = ["lpia"]

        # Sanity-check.
        if publish_type not in ("netbook", "mid", "src"):
            for arch in arches:
                paths = []
                for ext in "iso", "img", "img.gz", "img.tar.gz", "tar.gz":
                    paths.append(os.path.join(
                        daily_dir,
                        "%s-%s-%s.%s" % (series, publish_type, arch, ext)))
                paths.append(os.path.join(daily_dir, "%s.tar.xz" % arch))
                for path in paths:
                    if os.path.exists(path):
                        break
                else:
                    raise PublishReleaseException(
                        "No daily for %s %s on %s!" % (series, arch, date))

                oversized_path = os.path.join(
                    daily_dir,
                    "%s-%s-%s.OVERSIZED" % (series, publish_type, arch))
                if os.path.exists(oversized_path):
                    yesno = input(
                        "Daily for %s %s on %s is oversized!  "
                        "Continue? [yN] " % (series, arch, date))
                    if not yesno.lower().startswith("y"):
                        sys.exit(1)

        if self.want_pool:
            self.do("mkdir -p %s" % pool_dir, osextras.ensuredir, pool_dir)
        if self.want_dist or self.want_full:
            self.do("mkdir -p %s" % target_dir, osextras.ensuredir, target_dir)
            version_link = self.version_link(source)
            if not os.path.islink(version_link):
                self.do(
                    "ln -ns %s %s" % (series, version_link),
                    os.symlink, series.name, version_link)
        if self.want_dist and not self.config["CDIMAGE_NO_PURGE"]:
            entries = osextras.listdir_force(target_dir)
            for entry in entries:
                if not entry.startswith("%s-%s-" % (prefix, publish_type)):
                    continue
                entry_path = os.path.join(target_dir, entry)
                if os.path.islink(entry_path):
                    self.remove(entry_path)

        if self.want_torrent(publish_type):
            # Prepare torrent trees for publication.
            torrent_dir = self.torrent_dir(source, publish_type)
            if self.want_dist:
                if not self.config["CDIMAGE_NO_PURGE"]:
                    self.mkemptydir(torrent_dir)
            if self.want_full:
                torrent_releases_dir = os.path.dirname(
                    os.path.dirname(torrent_dir))
                for entry in osextras.listdir_force(torrent_releases_dir):
                    entry_path = os.path.join(torrent_releases_dir, entry)
                    if entry != self.status and os.path.isdir(entry_path):
                        self.remove_tree(entry_path)
                self.mkemptydir(torrent_dir)

        logger.info("Constructing release trees ...")
        for arch in arches:
            self.publish_release_arch(source, date, publish_type, arch)

        # There can only be one set of images per release in the per-release
        # tree, so if we're publishing there then we can now safely clean up
        # previous images for that release.
        if self.want_dist and not self.config["CDIMAGE_NO_PURGE"]:
            for purge_dir in target_dir, pool_dir:
                for entry in os.listdir(purge_dir):
                    if not entry.startswith("%s-" % prefix):
                        continue
                    # TODO: This test is wrong, but cumbersome to fix.  For
                    # example, consider the existence of
                    # ubuntu-13.04-beta2-preinstalled-desktop-armhf+omap4.img
                    # while publishing ubuntu-13.04.
                    if entry.startswith("%s-" % prefix_status):
                        continue
                    entry_path = os.path.join(purge_dir, entry)
                    logger.info("Purging %s" % entry_path)
                    self.remove(entry_path)

        if publish_type in ("uec", "server-uec"):
            for name in (
                "published-ec2-release.txt", "tool-version-info.txt",
                "build-info.txt",
            ):
                path = os.path.join(daily_dir, name)
                if not os.path.exists(path):
                    continue
                if self.want_dist or self.want_full:
                    self.copy(path, os.path.join(target_dir, name))

        if self.want_dist:
            self.do(
                "make-web-indices %s %s" % (target_dir, prefix_status),
                self.make_web_indices, target_dir, prefix_status)
        if self.want_full:
            self.do(
                "make-web-indices %s %s" % (target_dir, prefix),
                self.make_web_indices, target_dir, prefix)

        if self.want_pool:
            logger.info("Checksumming simple tree (pool) ...")
            self.checksum_directory(
                [pool_dir, daily_dir],
                map_expr="s/^%s-/%s-/" % (prefix_status, series))
        if self.want_dist:
            logger.info("Checksumming simple tree (%s) ..." % series)
            self.checksum_directory(
                [target_dir, daily_dir],
                map_expr="s/^%s-/%s-/" % (prefix_status, series))
            if self.want_metalink(publish_type):
                logger.info(
                    "Creating and publishing metalink files for the simple "
                    "tree (%s) ..." % series)
                self.make_metalink(target_dir, self.metalink_version)
        if self.want_full:
            logger.info("Checksumming full tree ...")
            self.checksum_directory(
                [target_dir, daily_dir],
                map_expr="s/^%s-/%s-/" % (prefix, series))
            if self.want_metalink(publish_type):
                logger.info(
                    "Creating and publishing metalink files for the full "
                    "tree ...")
                if self.official == "named":
                    metalink_target_dir = os.path.join(
                        self.tree.publish_target(source), "releases",
                        self.version, self.status)
                else:
                    metalink_target_dir = target_dir
                self.make_metalink(metalink_target_dir, self.version)

        if self.want_dist or self.want_pool:
            if self.dry_run:
                logger.info("site-manifest %s .manifest" % self.tree.directory)
            else:
                manifest_path = os.path.join(self.tree.directory, ".manifest")
                with AtomicFile(manifest_path) as manifest:
                    for line in self.tree.manifest():
                        print(line, file=manifest)
                os.chmod(
                    manifest_path,
                    os.stat(manifest_path).st_mode | stat.S_IWGRP)

                # Create timestamps for this run.
                if self.dry_run:
                    logger.info("Would create trace file")
                else:
                    trace_dir = os.path.join(self.tree.directory, ".trace")
                    osextras.ensuredir(trace_dir)
                    fqdn = socket.getfqdn()
                    with open(os.path.join(trace_dir, fqdn), "w") as trace:
                        subprocess.check_call(["date", "-u"], stdout=trace)

        logger.info(
            "Done!  Remember to sync-mirrors after checking that everything "
            "is OK.")


class FullReleasePublisher(ReleasePublisher):
    """An object that can publish releases in a "full" layout.

    This layout is used in the directory trees managed by DailyTree and
    ChinaDailyTree.
    """

    def __init__(self, *args, **kwargs):
        super(FullReleasePublisher, self).__init__(*args, **kwargs)
        assert self.official in ("named", "no")
        assert not isinstance(self.tree, SimpleReleaseTree)

    @property
    def want_dist(self):
        return False

    @property
    def want_pool(self):
        return False

    @property
    def want_full(self):
        return True

    def target_dir(self, source, date, publish_type):
        target_dir = os.path.join(
            self.tree.publish_target(source), "releases", self.config.series,
            self.status)
        if date.endswith("/unpacked"):
            target_dir = os.path.join(target_dir, "unpacked")
        if publish_type == "src":
            target_dir = os.path.join(target_dir, "source")
        return target_dir

    def version_link(self, source):
        return os.path.join(
            self.tree.publish_target(source), "releases", self.version)

    def torrent_dir(self, source, publish_type):
        torrent_tree = TorrentTree(self.config)
        return os.path.join(
            torrent_tree.publish_target(source), "releases",
            self.config.series, self.status, publish_type)

    def want_torrent(self, publish_type):
        return publish_type not in ("src", "uec", "server-uec")


class SimpleReleasePublisher(ReleasePublisher):
    """An object that can publish releases to a SimpleReleaseTree."""

    def __init__(self, *args, **kwargs):
        super(SimpleReleasePublisher, self).__init__(*args, **kwargs)
        assert self.official in ("yes", "poolonly")
        assert isinstance(self.tree, SimpleReleaseTree)

    @property
    def want_dist(self):
        return self.official == "yes"

    @property
    def want_pool(self):
        return True

    @property
    def want_full(self):
        return False

    def target_dir(self, source, date, publish_type):
        target_dir = os.path.join(
            self.tree.publish_target(source), self.config.series)
        if publish_type == "src":
            target_dir = os.path.join(target_dir, "source")
        return target_dir

    def version_link(self, source):
        return os.path.join(self.tree.publish_target(source), self.version)

    def pool_dir(self, source):
        return os.path.join(self.tree.publish_target(source), ".pool")

    def torrent_dir(self, source, publish_type):
        torrent_tree = TorrentTree(self.config)
        return os.path.join(
            torrent_tree.publish_target(source), "simple", self.config.series,
            publish_type)

    def want_torrent(self, publish_type):
        if self.want_dist:
            return publish_type not in ("src", "uec", "server-uec")
        else:
            return False
