# -*- coding: utf-8 -*-

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

import errno
import io
from itertools import count
from optparse import OptionParser
import os
import re
try:
    from shlex import quote as shell_quote
except ImportError:
    from pipes import quote as shell_quote
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
from textwrap import dedent
import time
import traceback

from cdimage.atomicfile import AtomicFile
from cdimage.checksums import (
    ChecksumFileSet,
    checksum_directory,
)
from cdimage.config import Series
from cdimage.log import logger, reset_logging
from cdimage.mirror import trigger_mirrors
from cdimage import osextras
from cdimage.project import setenv_for_project
from cdimage.metadata import generate_ubuntu_core_image_lxd_metadata

__metaclass__ = type


if sys.version < "3":
    input = raw_input


# TODO: This should be in a configuration file.
projects = [
    "edubuntu",
    "kubuntu",
    "kubuntu-active",
    "kubuntu-netbook",
    "lubuntu",
    "lubuntu-next",
    "ubuntu",
    "ubuntu-budgie",
    "ubuntu-mate",
    "ubuntu-unity",
    "ubuntucinnamon",
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


def rewrite_and_unpack_tarball(dry_run, source_path, target_path, iso_url):
    logger.info(
        "Rewriting %s to %s with iso_url=%s",
        source_path, target_path, iso_url)
    if dry_run:
        return
    iso_url_b = iso_url.encode('utf-8')
    netboot_dir = os.path.join(os.path.dirname(target_path), 'netboot')
    osextras.ensuredir(netboot_dir)

    with tarfile.open(source_path) as inf:
        with tarfile.open(target_path, 'w:gz') as outf:
            for ti in inf:
                if ti.name.endswith('.in'):
                    new_ti = inf.getmember(ti.name)
                    new_ti.name = ti.name[:-3]
                    content = inf.extractfile(ti).read()
                    content = content.replace(b"#ISOURL#", iso_url_b)
                    new_ti.size = len(content)
                    with open(
                            os.path.join(netboot_dir, new_ti.name),
                            'wb') as fp:
                        fp.write(content)
                    outf.addfile(new_ti, io.BytesIO(content))
                else:
                    inf.extract(ti, netboot_dir)
                    outf.addfile(ti, inf.extractfile(ti))


class Tree:
    """A publication tree."""

    @staticmethod
    def get_daily(config, directory=None):
        cls = DailyTree
        return cls(config, directory=directory)

    @staticmethod
    def get_release(config, official, directory=None):
        if official in ("yes", "poolonly"):
            cls = SimpleReleaseTree
        elif official in ("named", "no", "inteliot"):
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

    def url_for_path(self, path):
        """Return the public URL for the file at `path`.

        `path` must be under self.directory.
        """
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
                path.endswith(".img.gz") or path.endswith(".img.xz") or
                path.endswith(".tar.gz") or path.endswith(".tar.xz") or
                path.endswith(".wsl")):
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
            if self.project == "ubuntu-server":
                return "preinstalled-server"
            elif self.project == "ubuntu-core":
                return "preinstalled-core"
            else:
                return "preinstalled-desktop"
        elif self.image_type == "daily-minimal":
            return "minimal"
        elif self.image_type.endswith("-live"):
            if self.project == "edubuntu":
                return "desktop"
            elif self.project == "kubuntu-netbook":
                return "netbook"
            elif self.project == "ubuntu-server":
                return "live-server"
            elif self.project == "ubuntu-core-installer":
                return "ubuntu-core-installer"
            elif self.project in ("ubuntu-core", "ubuntu-appliance"):
                return "live-core"
            elif self.project == "ubuntu-core-desktop":
                return "live-core-desktop"
            elif self.project == "ubuntu-mini-iso":
                return "mini-iso"
            elif self.project == "ubuntu-wsl":
                return "wsl"
            else:
                return "desktop"
        elif self.image_type.endswith("_dvd") or self.image_type == "dvd":
            return "dvd"
        elif self.image_type == "daily-canary":
            return "desktop-canary"
        elif self.image_type == "daily-legacy":
            return "desktop-legacy"
        else:
            if self.project == "edubuntu":
                return "addon"
            elif self.project == "ubuntu-server":
                if self.config["DIST"] >= "focal":
                    return "legacy-server"
                else:
                    return "server"
            elif self.project == "ubuntu-base":
                return "base"
            else:
                return "alternate"

    # Keep this in sync with publish_type above.
    @staticmethod
    def _guess_image_type(publish_type):
        if publish_type.startswith("preinstalled-"):
            return "daily-preinstalled"
        elif publish_type == "mini-iso":
            return "daily-live"
        elif publish_type in (
                "desktop", "live", "netbook",
                "live-core", "live-core-desktop",
                "live-server", "ubuntu-core-installer",
                "wsl"):
            return "daily-live"
        elif publish_type == "minimal":
            return "daily-minimal"
        elif publish_type == "dvd":
            return "dvd"
        elif publish_type in (
                "addon", "alternate", "base", "install", "server",
                "legacy-server"):
            return "daily"
        elif publish_type == "desktop-canary":
            return "daily-canary"
        elif publish_type == "desktop-legacy":
            return "daily-legacy"
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
        vanilla = "https://assets.ubuntu.com/v1/" + \
                  "vanilla-framework-version-1.8.0.min.css"
        if self.project == "kubuntu":
            return [vanilla, "//cdimage.ubuntu.com/include/kubuntu/style.css"]
        if self.project in ("lubuntu", "lubuntu-next"):
            return [vanilla, "//cdimage.ubuntu.com/include/lubuntu/style.css"]
        if self.project == "xubuntu":
            return [vanilla, "//cdimage.ubuntu.com/include/xubuntu/style.css"]
        else:
            return [vanilla]

    def cdtypestr(self, publish_type, image_format):
        if image_format in ("tar.gz", "tar.xz", "custom.tar.gz"):
            cd = "filesystem archive"
        else:
            cd = "image"

        if publish_type == "live":
            return "live %s" % cd
        elif publish_type == "live-core":
            return "Ubuntu Core %s" % cd
        elif publish_type == "live-core-desktop":
            return "Ubuntu Core Desktop %s" % cd
        elif publish_type == "ubuntu-core-installer":
            return "Ubuntu Core Installer %s" % cd
        elif publish_type == "desktop":
            return "desktop %s" % cd
        elif publish_type == "desktop-canary":
            return "canary desktop %s" % cd
        elif publish_type == "desktop-legacy":
            return "legacy desktop %s" % cd
        elif publish_type == "install":
            return "install %s" % cd
        elif publish_type == "alternate":
            return "alternate install %s" % cd
        elif publish_type == "minimal":
            return "minimal %s" % cd
        elif publish_type in ("server", "live-server"):
            if self.project == "edubuntu":
                return "classroom server %s" % cd
            else:
                return "server install %s" % cd
        elif publish_type == "netboot":
            return "netboot tarball"
        elif publish_type == "mini-iso":
            return "mini ISO"
        elif publish_type == "wsl":
            return "WSL image"
        elif publish_type == "legacy-server":
            return "legacy server install %s" % cd
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
        elif publish_type == "active":
            return "preview active image"
        elif publish_type in ("server-uec", "uec"):
            return "UEC image"
        elif publish_type == "preinstalled-desktop":
            return "preinstalled desktop %s" % cd
        elif publish_type == "preinstalled-server":
            return "preinstalled server %s" % cd
        elif publish_type == "preinstalled-netbook":
            return "preinstalled netbook %s" % cd
        elif publish_type == "preinstalled-active":
            return "preview preinstalled active image"
        elif publish_type == "preinstalled-core":
            return "preinstalled core image"
        elif publish_type == "wubi":
            return "Wubi %s" % cd
        else:
            raise WebIndicesException("Unknown image type %s!" % publish_type)

    def cdtypedesc(self, publish_type, image_format):
        capproject = self.config.capproject
        series = self.config["DIST"]

        if self.project == "xubuntu" and series < "focal":
            desktop_ram = 192
        else:
            desktop_ram = 1024

        if image_format in ("tar.gz", "tar.xz", "custom.tar.gz"):
            cd = "filesystem archive"
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
        elif publish_type in ("desktop", "desktop-canary", "desktop-legacy"):
            sentences.append(
                "The desktop %s allows you to try %s without changing your "
                "computer at all, and at your option to install it "
                "permanently later." % (cd, capproject))
            if self.project != "edubuntu" and not self.prefmsg_emitted:
                sentences.append(
                    "This type of %s is what most people will want to use." %
                    cd)
                self.prefmsg_emitted = True
            if publish_type == "desktop-canary":
                sentences.append(
                    "This type of %s is experimental." %
                    cd)
            if publish_type == "desktop-legacy":
                sentences.append(
                    "This type of %s uses the legacy installer." %
                    cd)
            sentences.append(desktop_req)
            if self.project == "edubuntu":
                sentences.append(
                    "You can install additional educational programs using "
                    "the classroom server add-on %s." % cd)
        elif publish_type == "install":
            sentences.append(
                "The install %s allows you to install %s permanently on a "
                "computer." % (cd, capproject))
        elif publish_type == "netboot":
            sentences.append(
                "The netboot tarball contains files needed to boot the %s "
                "installer over the network." % (capproject,))
        elif publish_type == "mini-iso":
            sentences.append(
                "The mini ISO image is a small ISO image that can be used "
                "to choose which other Ubuntu image to download and install.")
        elif publish_type == "wsl":
            sentences.append(
                "The WSL image is the root filesystem to be installed and "
                "launched by the Windows Subsystem for Linux.")
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
        elif publish_type in ("server", "live-server", "legacy-server"):
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
        elif publish_type.startswith("preinstalled-"):
            sentences.append(
                "The %s %s allows you to unpack a preinstalled version of %s "
                "onto a target device." % (publish_type, cd, capproject))
        elif publish_type in ("ubuntu-core", "preinstalled-core"):
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
        elif publish_type == "ubuntu-core-desktop":
            sentences.append(
                "Experimental Ubuntu Core Desktop installer images.")
        elif publish_type == "ubuntu-core-installer":
            sentences.append(
                "Installer for Ubuntu Core.")
        elif publish_type == "ubuntu-appliance":
            sentences.append(
                "An Ubuntu Appliance turns a computer into a specialised "
                "appliance for home or work. It is a system disk image for a "
                "PC or Raspberry Pi, built for security and simplicity.")
            sentences.append(
                "Ubuntu Appliances have strong privacy policies and long term "
                "security maintenance guarantees. They are published by "
                "companies and open source communities, who follow the Ubuntu "
                "code of conduct and appliance guidelines, together with "
                "Canonical, the publisher of Ubuntu.")
            link = Link(
                "https://ubuntu.com/appliance", "Ubuntu Appliance page",
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
        "arm64": "64-bit ARM (ARMv8/AArch64)",
        "arm64+x13s": "Lenovo X13s Gen 1",
        "arm64+raspi": "Raspberry Pi Generic (64-bit ARM)",
        "arm64+raspi3": "Raspberry Pi 3 (64-bit ARM)",
        "arm64+largemem": "64-bit ARM with a 64k page-size kernel",
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
        "armhf+raspi": "Raspberry Pi Generic (Hard-Float)",
        "armhf+raspi2": "Raspberry Pi 2",
        "armhf+raspi3": "Raspberry Pi 3 (Hard-Float)",
        "i386": "32-bit PC (i386)",
        "ppc64el": "PowerPC64 Little-Endian",
        "riscv64": "RISC-V",
        "riscv64+unleashed": "RISC-V for SiFive HiFive Unleashed",
        "riscv64+unmatched": "RISC-V for SiFive HiFive Unmatched",
        "riscv64+visionfive": "RISC-V for StarFive VisionFive",
        "riscv64+visionfive2": "RISC-V for StarFive VisionFive 2",
        "riscv64+milkvmars": "RISC-V for Milk-V Mars",
        "riscv64+jh7110": "RISC-V for JH7110 boards",
        "riscv64+pic64gx": "RISC-V for Microchip PIC64GX",
        "riscv64+nezha": "RISC-V for Allwinner Nezha",
        "riscv64+licheerv": "RISC-V for Sipeed LicheeRV Dock",
        "riscv64+icicle": "RISC-V for Microchip Polarfire Icicle Kit",
        "s390x": "IBM System z",
    }

    def archdesc(self, arch, publish_type):
        series = self.config["DIST"]
        sentences = []
        if arch == "amd64":
            sentences.append(
                "Choose this if you have a computer based on the AMD64 or "
                "EM64T architecture (e.g., Athlon64, Opteron, EM64T "
                "Xeon, Core 2).")
            if 'i386' in self.config.arches:
                sentences.append(
                    "If you have a non-64-bit processor made by AMD, or if "
                    "you need full support for 32-bit code, use the i386 "
                    "images instead.")
            else:
                sentences.append("Choose this if you are at all unsure.")
        elif arch == "arm64":
            sentences.append("For 64-bit ARMv8 processors and above.")
        elif arch == "arm64+x13s":
            sentences.append("For Lenovo X13s Gen 1.")
        elif arch == "armhf+raspi2":
            sentences.append("For Raspberry Pi 2 boards.")
        elif arch in ("arm64+raspi", "armhf+raspi",
                      "arm64+raspi3", "armhf+raspi3"):
            sentences.append("For modern Raspberry Pi boards (Pi 3, 4, 5, "
                             "CM4, and Zero 2 W).")
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
        elif arch == "i386":
            sentences.append("For almost all PCs.")
            sentences.append(
                "This includes most machines with Intel/AMD/etc type "
                "processors and almost all computers that run Microsoft "
                "Windows, as well as newer Apple Macintosh systems based on "
                "Intel processors.")
        elif arch == "ppc64el":
            if series >= "jammy":
                sentences.append(
                    "For POWER9 and POWER10 Little-Endian systems.")
            else:
                sentences.append(
                    "For POWER8 and POWER9 Little-Endian systems, especially "
                    "the \"LC\" Linux-only servers.")
        elif arch == "riscv64+unleashed":
            sentences.append(
                "For RISC-V computers, with support for SiFive HiFive "
                "Unleashed and QEMU.")
        elif arch == "riscv64+unmatched":
            sentences.append(
                "For RISC-V computers, with support for SiFive HiFive "
                "Unmatched.")
        elif arch == "riscv64+visionfive":
            sentences.append(
                "For RISC-V computers, with support for StarFive VisionFive")
        elif arch == "riscv64+visionfive2":
            sentences.append(
                "For RISC-V computers, with support for StarFive VisionFive 2")
        elif arch == "riscv64+milkvmars":
            sentences.append(
                "For RISC-V computers, with support for Milk-V Mars")
        elif arch == "riscv64+jh7110":
            sentences.append(
                "For RISC-V computers, with support for JH7110 boards")
        elif arch == "riscv64+pic64gx":
            sentences.append(
                "For RISC-V computers, with support for Microchip PIC64GX")
        elif arch == "riscv64+nezha":
            sentences.append(
                "For RISC-V computers, with support for Allwinner Nezha")
        elif arch == "riscv64+licheerv":
            sentences.append(
                "For RISC-V computers, with support for Sipeed LicheeRV Dock")
        elif arch == "riscv64+icicle":
            sentences.append(
                "For RISC-V computers, with support for Microchip Polarfire "
                "Icicle Kit")
        elif arch == "riscv64":
            sentences.append(
                "For RISC-V computers. Requires copying your own first "
                "stage bootloader (like u-boot) and relevant DTBs onto the "
                "image before usage on real hardware (like the SiFive HiFive "
                "Unmatched).")
            if publish_type.startswith("preinstalled-"):
                sentences.append(
                    "Usable on RISC-V QEMU.")
        elif arch == "s390x":
            sentences.append(
                "For IBM System z series mainframes, such as IBM LinuxONE.")
        else:
            raise WebIndicesException("Unknown architecture %s!" % arch)
        return "  ".join(sentences)

    def maybe_oversized(self, status, path, publish_type):
        if status != "daily" or not os.path.exists(path):
            return

        usb_projects = (
            "kubuntu", "kubuntu-active",
            "ubuntu-mate",
            )

        yield "<br>"
        sentences = []
        if publish_type == "dvd" or self.project == "ubuntustudio":
            sentences.append(
                "Warning: This image is oversized (which is a bug) and will "
                "not fit onto a single-sided single-layer DVD.")
            sentences.append(
                "However, you may still test it using a larger USB drive or a "
                "virtual machine.")
        elif self.project in ("kubuntu",
                              "ubuntu-mate",
                              "ubuntu-budgie",
                              "xubuntu"):
            sentences.append(
                "Warning: This image is oversized (which is a bug) and will "
                "not fit onto a 2GB USB stick.")
            sentences.append(
                "However, you may still test it using a DVD, a larger USB "
                "drive, or a virtual machine.")
        elif (self.project in usb_projects or
                self.project in ("xubuntu", "ubuntu-gnome")):
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
        elif extension in ("img.gz", "img.xz"):
            return "preinstalled SD Card image"
        elif extension == "iso":
            return "standard download"
        elif extension == "wsl":
            return "standard download"
        elif extension.endswith(".torrent"):
            return "%s download" % Link(
                "https://help.ubuntu.com/community/BitTorrent", "BitTorrent")
        elif extension == "list":
            return "file listing"
        elif extension == "manifest":
            return "contents of live filesystem"
        elif extension == "manifest-desktop":
            return "contents of desktop part of live filesystem"
        elif extension == "manifest-remove":
            return "packages to remove from live filesystem on installation"
        elif extension == "manifest-minimal-remove":
            return "packages to remove from live filesystem on " + \
                   " installation when performing a minimal install"
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
        elif extension in ("tar.gz", "custom.tar.gz"):
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
        series = self.config["DIST"]

        if self.project in ("ubuntu-core", "ubuntu-core-desktop",
                            "ubuntu-appliance"):
            channel = self.config.get("CHANNEL", "edge")
            heading = "%s %s (%s)" % (
                self.config.capproject, self.config.core_series, channel)
        elif self.project == "ubuntu-core-installer":
            heading = "Ubuntu Core %s Installer" % (self.config.core_series,)
        else:
            heading = "%s %s (%s)" % (
                self.config.capproject, series.displayversion(self.project),
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

    def find_images(self, directory, prefix, publish_type):
        images = []
        prefix_type = "%s-%s" % (prefix, publish_type)
        for entry in os.listdir(directory):
            if entry in ("%s.img" % prefix_type, "%s.img.xz" % prefix_type):
                images.append(entry)
            elif publish_type == "wubi" and entry.endswith(".tar.xz"):
                # Wubi images are just "ARCH.tar.xz", with no prefix.
                images.append(entry)
            elif entry.startswith("%s-" % prefix_type):
                if (entry.endswith(".list") or
                        entry.endswith(".img.gz") or
                        entry.endswith(".tar.gz") or
                        entry.endswith(".wsl") or
                        entry.endswith(".img.xz")):
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
        prefixes = [base_prefix]
        if base_prefix.count(".") >= 2:
            # point release - need the base version too
            prefixes.append(base_prefix.rsplit(".", 1)[0])

        all_publish_types = (
            "live", "desktop",
            "live-server",
            "netboot",
            "legacy-server",
            "mini-iso", "wsl",
            "server", "install", "alternate",
            "serveraddon", "addon",
            "dvd",
            "src",
            "netbook", "mobile", "active",
            "uec", "server-uec",
            "preinstalled-desktop", "preinstalled-netbook",
            "preinstalled-mobile", "preinstalled-active",
            "preinstalled-server",
            "preinstalled-core", "wubi",
            "live-core",
            "live-core-desktop",
            "ubuntu-core-installer",
            "desktop-canary",
            "desktop-legacy",
        )

        all_arches = (
            "amd64", "amd64+mac",
            "i386",
            "armel", "armel+dove", "armel+imx51", "armel+omap", "armel+omap4",
            "armel+ac100", "armel+mx5",
            "armhf", "armhf+omap", "armhf+omap4", "armhf+ac100", "armhf+mx5",
            "armhf+nexus7", "armhf+raspi", "armhf+raspi2", "armhf+raspi3",
            "arm64", "arm64+raspi", "arm64+raspi3", "arm64+x13s",
            "ppc64el",
            "riscv64", "riscv64+unleashed", "riscv64+unmatched",
            "riscv64+visionfive", "riscv64+visionfive2", "riscv64+nezha",
            "riscv64+licheerv", "riscv64+icicle", "riscv64+milkvmars",
            "riscv64+pic64gx", "riscv64+jh7110",
            "s390x",
        )

        self.prefmsg_emitted = False

        header_path = os.path.join(directory, "HEADER.html")
        footer_path = os.path.join(directory, "FOOTER.html")
        htaccess_path = os.path.join(directory, ".htaccess")
        reldir = os.path.realpath(directory)
        if 'simple' in reldir.split('/'):
            site = 'releases.ubuntu.com'
            suburl = reldir.split('simple/')[-1]
        else:
            site = 'cdimage.ubuntu.com'
            suburl = reldir.split('full/')[-1]

        with AtomicFile(header_path) as header, \
                AtomicFile(footer_path) as footer, \
                AtomicFile(htaccess_path) as htaccess:
            heading = self.web_heading(base_prefix)
            print(
                dedent("""\
        <!doctype html>
        <html lang="en">
        <head>
        <title>%s</title>
        <meta charset="UTF-8" />
        <meta name="description" content="CD images for %s" />
        <meta name="author" content="Canonical" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="canonical" href="https://%s/%s">
        <!-- Main style sheets for CSS2 capable browsers -->
        <style type="text/css" media="screen">""")
                % (heading, heading, site, suburl),
                file=header)
            assets = 'https://assets.ubuntu.com/v1/'
            print(dedent("""\
                    .p-strip--image {
                        background-image: url('"""
                         + assets + """775cc62b-vanilla-grad-background.png');
                        background-position: 75% 25%;
                    }

                    .p-table-wrapper {
                        overflow-x: scroll;
                    }

                    table {
                        min-width: 984px;
                        width: 100%;
                    }

                    th[colspan="5"],
                    th[colspan="4"] {
                        display: none;
                    }

                    th:first-child,
                    td:first-child {
                        vertical-align: inherit;
                        width: 5%;
                    }

                    th:nth-of-type(2),
                    td:nth-of-type(2) {
                        width: 20em;
                    }

                    th:nth-of-type(3),
                    td:nth-of-type(3) {
                        width: 12em;
                    }

                    th:nth-of-type(4),
                    td:nth-of-type(4) {
                        width: 6em;
                    }

                    th:nth-of-type(5),
                    td:nth-of-type(5) {
                    }
                </style>
                """), file=header)
            for css in self.cssincludes():
                print(
                    '<link rel="stylesheet" type="text/css" '
                    'href="%s">' % css, file=header)
            if self.project == "kubuntu":
                print(
                    '<link rel="shortcut icon" type="image/x-icon" '
                    'href="//cdimage.ubuntu.com/include/kubuntu/'
                    'images/favicon.ico">', file=header)
                header_href = 'https://kubuntu.org/'
            elif self.project in ("lubuntu", "lubuntu-next"):
                print(
                    '<link rel="icon" type="image/png" '
                    'href="//cdimage.ubuntu.com/include/lubuntu/'
                    'favicon.png">', file=header)
                header_href = 'https://lubuntu.me/'
            elif self.project == "xubuntu":
                print(
                    '<link rel="icon" type="image/png" '
                    'href="//cdimage.ubuntu.com/include/xubuntu/'
                    'favicon.png">', file=header)
                header_href = 'https://xubuntu.org/'
            else:
                header_href = 'http://www.ubuntu.com/'

            print(dedent("""\
            </head>
            <body>
                <header id="navigation" class="p-navigation">
                    <div class="row">
                      <div class="p-navigation__banner">
                        <div class="p-navigation__logo">
                          <a class="p-navigation__link" href="/">
                          <img class="p-navigation__image"
                           src=" """ + assets
                  + """411e1474-releases-lockup.svg"
                           alt="">
                          </a>
                        </div>
                      </div>
                      <nav class="p-navigation__nav" role="menubar">
                        <span class="u-off-screen">
                          <a href="#pageWrapper">Jump to main content</a>
                        </span>
                      </nav>
                    </div>
                </header>
                <section class="p-strip--image is-dark">
                    <div class="row">
                        <div id="header"><a href="%s"></a></div>
                        <h1 class="u-no-margin--bottom">%s</h1>
                    </div>
                </section>
                <div id="pageWrapper" class="p-strip">
                    <div class="row">
                        <div id="main">
            """) % (header_href, heading), file=header)

            mirrors_url = "http://www.ubuntu.com/getubuntu/downloadmirrors"
            if ("full" in reldir.split(os.pardir) and
                    "-alpha-" not in base_prefix and
                    base_prefix != self.config.series):
                if self.project in ("ubuntu", "ubuntu-server", "ubuntu-wsl"):
                    url = "http://releases.ubuntu.com/"
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

            found_publish_types = set()

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
                        "iso", "img", "img.gz", "img.xz", "img.tar.gz",
                        "tar.gz", "tar.xz", "custom.tar.gz", "wsl",
                    ):
                        paths = []
                        if image_format == "img" or image_format == "img.xz":
                            base = os.path.join(
                                directory,
                                "%s-%s" % (prefix, publish_type))
                            path = "%s.%s" % (base, image_format)
                            if os.path.exists(path):
                                paths.append((path, None, base))
                        elif (image_format == "tar.xz" and
                              # skip source images explicitly, which are
                              # always .iso and have bodged arches
                              publish_type != "src"):
                            for arch in arches:
                                base = os.path.join(directory, arch)
                                path = "%s.%s" % (base, image_format)
                                if os.path.exists(path):
                                    paths.append((path, arch, base))
                        for arch in arches:
                            base = os.path.join(
                                directory,
                                "%s-%s-%s" % (prefix, publish_type, arch))
                            path = "%s.%s" % (base, image_format)
                            if os.path.exists(path):
                                paths.append((path, arch, base))
                        if not paths:
                            continue

                        found_publish_types.add(publish_type)

                        print('<div class="row p-divider">'
                              + '<div class="p-card">',
                              file=header)
                        cdtypestr = self.cdtypestr(publish_type, image_format)
                        print('<div class="col-6 p-divider__block">',
                              file=header)
                        print(
                            "<h3>%s</h3>" % self.titlecase(cdtypestr),
                            file=header)
                        print(file=header)
                        for tag in self.cdtypedesc(publish_type, image_format):
                            print(tag, file=header)
                            print(file=header)

                        print(file=header)

                        print('</div>', file=header)
                        print('<div class="col-6 p-divider__block">',
                              file=header)

                        for path, arch, base in paths:
                            if arch is None:
                                raise WebIndicesException(
                                    "Unknown image type %s!" %
                                    publish_type)
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
                                    "<a href=\"%s\">%s</a>" %
                                    (os.path.basename(path), imagestr),
                                    file=header)
                            elif os.path.exists("%s.torrent" % path):
                                print(
                                    "<a href=\"%s.torrent\">%s</a> "
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
                                print("<p>%s</p>" % desc, file=header)
                                print(file=header)

                            if arch is None:
                                htaccess_extensions = ("img", "manifest")
                            else:
                                htaccess_extensions = (
                                    "img.gz.torrent", "img.gz.zsync", "img.gz",
                                    "img.xz", "img.tar.gz", "img.torrent",
                                    "img.zsync", "img", "iso.torrent",
                                    "iso.zsync", "iso", "list",
                                    "manifest", "manifest-desktop",
                                    "manifest-remove",
                                    "manifest-minimal-remove",
                                    "tar.gz", "tar.gz.zsync",
                                    "bootimg", "tar.xz", "custom.tar.gz",
                                    "wsl",
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
                            if status == "release":
                                for extension in (
                                    "iso", "img", "img.gz", "img.xz",
                                ):
                                    extpath = "%s.%s" % (base, extension)
                                    if not os.path.exists(extpath):
                                        continue
                                    absdir = directory
                                    if not os.path.isabs(absdir):
                                        absdir = os.path.abspath(absdir)
                                    relbase = os.path.relpath(
                                        absdir, self.tree.directory)
                                    relbase = os.path.join("/", relbase)
                                    relpath = os.path.join(
                                        relbase, os.path.basename(extpath))
                                    if base_prefix.count(".") >= 2:
                                        latest_prefix = base_prefix.rsplit(
                                            ".", 1)[0]
                                    else:
                                        latest_prefix = base_prefix
                                    latest_prefix = \
                                        "%s-latest" % latest_prefix
                                    latest_path = os.path.join(
                                        relbase,
                                        "%s-%s-%s.%s" % (
                                            latest_prefix, publish_type,
                                            arch, extension))
                                    print(
                                        "RedirectPermanent %s %s" % (
                                            latest_path, relpath),
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
                        print('</div>', file=header)
                        print('</div></div>', file=header)

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

            if ([entry for entry in os.listdir(directory)
                 if "-arm" in entry]) and found_publish_types != set(["wsl"]):
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

            print("<div class='p-table-wrapper'>", file=header)

            print(
                dedent("""\
         </div></div></div></div>
         <footer class="p-footer">
           <div class="row">
             <p><small>&copy; 2018 Canonical Ltd. Ubuntu and Canonical
               are registered trademarks of Canonical Ltd.</small></p>
             <nav class="p-footer__nav">
               <ul class="p-footer__links">
                 <li class="p-footer__item">
                   <a class="p-footer__link"
                    href="https://www.ubuntu.com/legal"><small>Legal
                    information</small></a>
                 </li>
                 <li class="p-footer__item">
                   <a class="p-footer__link"
                    href="https://bugs.launchpad.net/ubuntu-cdimage/+filebug">
                    <small>Report a bug on this site</small></a>
                 </li>
               </ul>
               <span class="u-off-screen">
                 <a href="#">Go to the top of the page</a>
               </span>
             </nav>
           </div>
         </footer>
         </body></html>"""),
                file=footer)

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
                "published-ec2-daily.txt published-ec2-release.txt "
                ".*.tar.gz",
                file=htaccess)
            print(
                "IndexOptions NameWidth=* DescriptionWidth=* "
                "SuppressHTMLPreamble FancyIndexing "
                "IconHeight=22 IconWidth=22 HTMLTable",
                file=htaccess)
            for icon, patterns in (
                ("folder.png", "^^DIRECTORY^^"),
                ("iso.png", ".iso"),
                ("img.png", ".img .img.xz .tar.gz .tar.xz .wsl"),
                ("list.png", (
                    ".list .manifest .html .zsync "
                    "SHA256SUMS SHA256SUMS.gpg")),
                ("torrent.png", ".torrent"),
            ):
                print(
                    "AddIcon %s%s %s" % (cdicons, icon, patterns),
                    file=htaccess)

            for extension in (
                "img.gz.torrent", "img.gz", "img.torrent", "img",
                "iso.torrent", "iso", "list", "manifest",
                "manifest-desktop", "manifest-remove",
                "manifest-minimal-remove",
            ):
                mimetype = self.mimetypestr(extension)
                if (mimetype and
                        self.find_any_with_extension(directory, extension)):
                    print(
                        "AddType %s .%s" % (mimetype, extension),
                        file=htaccess)

    def refresh_simplestreams(self):
        """For the publisher cycle, refresh the corresponding sstreams."""
        if self.config.get("SIMPLESTREAMS") == "0":
            # SimpleStreams are enabled by default now.
            return
        logger.info("Refreshing simplestreams...")

        from cdimage.simplestreams import SimpleStreams
        sstreams = SimpleStreams.get_simplestreams(self.config, self)
        sstreams.generate()


class DailyTree(Tree):
    """A publication tree containing daily builds."""

    def __init__(self, config, directory=None):
        if directory is None:
            # Strip trailing slash to not to confuse cdimage.
            directory = os.path.join(config.root, "www", "full",
                                     config.subtree).rstrip('/')
        super(DailyTree, self).__init__(config, directory)

    def name_to_series(self, name):
        """Return the series for a file basename."""
        dist = name.split("-")[0]
        return Series.find_by_name(dist)

    @property
    def site_name(self):
        return "cdimage.ubuntu.com"

    def url_for_path(self, path):
        logger.info(
            "url_for_path(%s), self.directory = %s", path, self.directory)
        if not path.startswith(self.directory):
            raise Exception(
                "url_for_path(%r) did not start with self.directory (%r)"
                % (path, self.directory))
        url_path = path[len(self.directory):].lstrip('/')
        return "https://%s/%s" % (self.site_name, url_path)

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
            self.config.root, "scratch", self.config.subtree, self.project,
            self.config.full_series, self.image_type, "debian-cd", arch)

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
        if (self.config.project in ("ubuntu-core", "ubuntu-core-desktop",
                                    "ubuntu-appliance") and
                self.image_type == 'daily-live'):
            channel = self.config.get("CHANNEL", "edge")
            return os.path.join(self.config.core_series, channel)
        image_type_dir = self.image_type.replace("_", "/")
        if (self.config.distribution != "ubuntu" or
                not self.config["DIST"].is_latest):
            image_type_dir = os.path.join(
                self.config.full_series, image_type_dir)
        return image_type_dir

    @property
    def publish_base(self):
        return os.path.join(self.tree.project_base, self.image_type_dir)

    def size_limit(self, arch):
        if self.project == "edubuntu":
            if self.config["DIST"] >= "oracular":
                # Per IRC discussions on #ubuntu-release 2024-10-07
                return int(6.9 * 1000 * 1000 * 1000)
            else:
                return int(6.4 * 1000 * 1000 * 1000)
        if self.project == "ubuntustudio":
            if self.config["DIST"] >= "oracular":
                # Per IRC discussions on #ubuntu-release 2024-10-22
                return int(6.8 * 1000 * 1000 * 1000)
            else:
                # the 24.04 LTS image was bigger than 24.10's
                return int(7.7 * 1000 * 1000 * 1000)
        elif self.project in ("kubuntu", "kubuntu-active"):
            # Per Matrix discussions 2024-10-07
            if self.config["DIST"] >= "oracular":
                return int(4.7 * 1000 * 1000 * 1000)
            # Per IRC discussions on #ubuntu-release 2023-11-27
            else:
                return int(4.6 * 1000 * 1000 * 1000)
        elif self.project == "ubuntukylin":
            if self.config["DIST"] >= "mantic":
                # 2023-10-08, mentioned on #ubuntu-flavors
                return int(5.5 * 1000 * 1000 * 1000)
            if self.config["DIST"] >= "jammy":
                # Per IRC discussions on #ubuntu-flavors on the 2020-10-08
                return int(4 * 1024 * 1024 * 1024)
        elif self.project == "ubuntu":
            if self.config["DIST"] >= "oracular":
                # 2024-10-04, vorlon set to match actual image size after
                # optimizations
                return int(6.1 * 1000 * 1000 * 1000)
            else:
                # 2025-01-28, per Mattermost discussions
                return int(6.4 * 1000 * 1000 * 1000)
        elif self.project == "ubuntu-mate":
            if self.config["DIST"] >= "noble":
                return int(5 * 1000 * 1000 * 1000)
            else:
                return int(4 * 1000 * 1000 * 1000)
        elif (self.project == "ubuntu-budgie" and
              self.config["DIST"] >= "focal"):
            # Per IRC discussions on #ubuntu-flavors on the 2020-10-05
            return int(4 * 1024 * 1024 * 1024)
        elif self.project == "xubuntu" and self.config["DIST"] >= "noble":
            # Per Matrix discussions on #flavors:ubuntu.com 2025-01-27
            return int(4.3 * 1000 * 1000 * 1000)
        elif self.project == "xubuntu" and self.config["DIST"] >= "jammy":
            # Per IRC discussions on #ubuntu-flavors 2024-02-15
            return int(3 * 1000 * 1000 * 1000)
        elif self.project in ("ubuntu-budgie", "xubuntu",
                              "ubuntu-mate"):
            # https://lists.ubuntu.com/archives/ubuntu-release/2016-May/003744.html
            # https://irclogs.ubuntu.com/2019/02/17/%23ubuntu-release.html#t03:04
            return int(2 * 1000 * 1000 * 1000)
        elif self.project == "ubuntu-unity":
            # Per IRC discussions on #ubuntu-release 2023-09-26
            return int(3.6 * 1000 * 1000 * 1000)
        elif self.project == "lubuntu":
            # As of Noble Beta Freeze, the Noble ISO is at 3301478400
            # bytes while the Jammy ISO is at 3087104000 bytes. This is
            # an increase of 7.028%. The increase between Focal and
            # Jammy is 53.778%, but that can be partially attributed to
            # the switch from the Firefox deb to snap.
            #
            # Between Noble and R cycle, we expect a +5% increase in ISO size.
            # Adjust the warning accordingly.  -tsimonq2
            if self.config["DIST"] > "noble":
                return int(3.5 * (1000 ** 3))
            # Warn if Noble increases by more than 2% from its Beta Freeze size
            elif self.config["DIST"] == "noble":
                return int(3.4 * (1000 ** 3))
            elif self.config["DIST"] >= "jammy":
                # Per IRC discussions on #ubuntu-release 2023-11-13
                return int(3.1 * (1000 ** 3))
            else:
                return int(2.0 * (1000 ** 3))
        elif self.project == "ubuntu-server":
            if self.config["DIST"] >= "noble":
                # As of today (2025-01-25) the riscv64 noble images reach
                # 3.41 GB. Let's use a limit that fits on a 4 GB USB stick.
                return int(3.6 * 1000 * 1000 * 1000)
            elif self.config["DIST"] >= "jammy":
                # Our images have been >2GB for quite some time now, and nobody
                # complained. Looks like 4GB USB sticks are common enough.
                # The next limit not to cross are the 4.7GB of a standard
                # single-side DVD.
                #
                # As of today (2023-09-18), the mantic-live-server-amd64.iso
                # image is ~2.8GB big. Let's set the new limit to a +20% of
                # that for now.
                return int(3.3 * 1000 * 1000 * 1000)
            elif self.config["DIST"] >= "focal":
                # Requested by paride via MP.
                return int(1.5 * 1000 * 1000 * 1000)
            else:
                # email with powersj, 20200108
                return int(1.2 * 1000 * 1000 * 1000)
        elif self.project == "ubuntucinnamon":
            # 2025-01-28, skia to match actual size for Noble .2
            return int(5.5 * 1000 * 1000 * 1000)
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
                    if name.endswith('.metalink'):
                        continue
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
        output = output.rstrip(" ")

        if output.startswith("ISO 9660 CD-ROM filesystem data "):
            return "iso"
        elif output.startswith(("x86 boot sector")):
            return "img"
        elif output.startswith(("gzip compressed data", "XZ compressed data")):
            if output.startswith("gzip compressed data"):
                compressed_extension = "gz"
            if output.startswith("XZ compressed data"):
                compressed_extension = "xz"
            with open("%s.type" % source_prefix) as compressed_type:
                real_output = compressed_type.readline().rstrip("\n")
            if real_output.startswith("ISO 9660 CD-ROM filesystem data "):
                return "iso.%s" % compressed_extension
            elif real_output.startswith(("x86 boot sector")):
                return "img.%s" % compressed_extension
            elif real_output.startswith("tar archive"):
                return "tar.%s" % compressed_extension
            elif real_output.startswith("WSL"):
                return "wsl"
            else:
                logger.warning(
                    "Unknown compressed file type '%s'; assuming .img.%s" %
                    (real_output, compressed_extension))
                return "img.%s" % compressed_extension
        else:
            logger.warning("Unknown file type '%s'; assuming .iso" % output)
            return "iso"

    def publish_netboot(self, arch, image_path):
        # Publishing a netboot tarball is a bit more complicated than
        # just copying it into place, as we also unpack it into a
        # netboot/ directory and replace rewrite any foo.cfg.in files
        # referencing #ISOURL# to foo.cfg referencing the actual URL
        # of the image.
        #
        # We also save a copy of the netboot tarball so we can rewrite
        # it again during release publication.
        tarname = "%s-netboot-%s.tar.gz" % (self.config.series, arch)
        source_path = os.path.join(self.image_output(arch), tarname)
        if not os.path.exists(source_path):
            return

        save_target_path = os.path.join(
            os.path.dirname(image_path), '.' + tarname)
        target_path = os.path.join(os.path.dirname(image_path), tarname)

        shutil.move(source_path, save_target_path)

        rewrite_and_unpack_tarball(
            False, save_target_path, target_path,
            self.tree.url_for_path(image_path))

    def publish_binary(self, publish_type, arch, date):
        in_prefix = "%s-%s-%s" % (self.config.series, publish_type, arch)
        if publish_type == "live-core":
            out_prefix = "ubuntu-core-%s-%s" % (self.config.core_series, arch)
        elif publish_type == "live-core-desktop":
            out_prefix = "ubuntu-core-desktop-%s-%s" % (
                self.config.core_series, arch)
        else:
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
        target_path = "%s.%s" % (target_prefix, extension)
        shutil.move(
            "%s.%s" % (source_prefix, self.source_extension),
            target_path)
        self.publish_netboot(arch, target_path)
        if os.path.exists("%s.list" % source_prefix):
            shutil.move("%s.list" % source_prefix, "%s.list" % target_prefix)
        self.checksum_dirs.append(source_dir)
        with ChecksumFileSet(
                self.config, target_dir, sign=False) as checksum_files:
            checksum_files.remove("%s.%s" % (out_prefix, extension))

        # Live filesystem manifests
        if os.path.exists("%s.manifest" % source_prefix):
            logger.info("Publishing %s live manifest ..." % arch)
            shutil.move(
                "%s.manifest" % source_prefix, "%s.manifest" % target_prefix)
        else:
            osextras.unlink_force("%s.manifest" % target_prefix)

        osextras.unlink_force("%s.squashfs" % target_prefix)

        if os.path.exists("%s.custom.tar.gz" % source_prefix):
            logger.info("Publishing %s custom tarball ..." % arch)
            shutil.move(
                "%s.custom.tar.gz" % source_prefix,
                "%s.custom.tar.gz" % target_prefix)

        if os.path.exists("%s.device.tar.gz" % source_prefix):
            logger.info("Publishing %s device tarball ..." % arch)
            shutil.move(
                "%s.device.tar.gz" % source_prefix,
                "%s.device.tar.gz" % target_prefix)

            for devarch in ("azure", "plano", "raspi2"):
                if os.path.exists("%s.%s.device.tar.gz" % (source_prefix,
                                                           devarch)):
                    logger.info("Publishing %s %s device tarball ..." %
                                (arch, devarch))
                    shutil.move(
                        "%s.%s.device.tar.gz" % (source_prefix, devarch),
                        "%s.%s.device.tar.gz" % (target_prefix, devarch))

        # os snap packages
        if os.path.exists("%s.os.snap" % source_prefix):
            logger.info("Publishing %s os snap package ..." % arch)
            shutil.move(
                "%s.os.snap" % source_prefix,
                "%s.os.snap" % target_prefix)

        # kernel snap packages
        if os.path.exists("%s.kernel.snap" % source_prefix):
            logger.info("Publishing %s kernel snap package ..." % arch)
            shutil.move(
                "%s.kernel.snap" % source_prefix,
                "%s.kernel.snap" % target_prefix)

            for devarch in ("dragonboard", "raspi2"):
                if os.path.exists("%s.%s.kernel.snap" % (source_prefix,
                                                         devarch)):
                    logger.info("Publishing %s %s kernel snap package ..." %
                                (arch, devarch))
                    shutil.move(
                        "%s.%s.kernel.snap" % (source_prefix, devarch),
                        "%s.%s.kernel.snap" % (target_prefix, devarch))

        # snappy model assertions
        if os.path.exists("%s.model-assertion" % source_prefix):
            logger.info("Publishing %s model assertion ..." % arch)
            shutil.move(
                "%s.model-assertion" % source_prefix,
                "%s.model-assertion" % target_prefix)

        # appliance qcow2 images (for LXD/multipass consumption)
        if os.path.exists("%s.qcow2" % source_prefix):
            logger.info("Publishing %s qcow2 image ..." % arch)
            shutil.move(
                "%s.qcow2" % source_prefix,
                "%s.qcow2" % target_prefix)

        # zsync metafiles
        if osextras.find_on_path("zsyncmake") and publish_type != "wsl":
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

        yield os.path.join(self.project, self.image_type_dir, in_prefix)

    def publish_livecd_base(self, arch, date):
        source_dir = os.path.join(
            self.config.root, "scratch", self.config.subtree, self.project,
            self.config.full_series, self.image_type, "live")
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
        if os.path.exists("%s.manifest-minimal-remove" % source_prefix):
            shutil.copy2(
                "%s.manifest-minimal-remove" % source_prefix,
                "%s.manifest-minimal-remove" % target_prefix)
        elif os.path.exists("%s.manifest-desktop" % source_prefix):
            shutil.copy2(
                "%s.manifest-desktop" % source_prefix,
                "%s.manifest-desktop" % target_prefix)

        yield os.path.join("livecd-base", self.image_type_dir, arch)

    def publish_wubi(self, arch, date):
        source_dir = os.path.join(
            self.config.root, "scratch", self.config.subtree, self.project,
            self.config.full_series, self.image_type, "live")
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

            # zsync metafiles
            if osextras.find_on_path("zsyncmake"):
                logger.info("Making source %d zsync metafile ..." % i)
                osextras.unlink_force("%s.iso.zsync" % target_prefix)
                zsyncmake(
                    "%s.iso" % target_prefix, "%s.iso.zsync" % target_prefix,
                    "%s.iso" % out_prefix)

            yield os.path.join(
                self.project, self.image_type, "%s-src" % self.config.series)

    def create_publish_info_file(self, date):
        """Create a .publish_info file with the publisher timestamps."""
        publish_dir = os.path.join(self.publish_base, date)
        if os.path.islink(publish_dir):
            return

        publish_dates = {}
        for entry in self.published_images(date):
            entry_path = os.path.join(publish_dir, entry)
            if os.path.islink(entry_path):
                publish_date = os.path.basename(
                    os.path.dirname(
                        os.path.realpath(entry_path)))
            else:
                publish_date = date
            publish_dates[entry] = publish_date

        if publish_dates:
            # Only create the .publish_info file when there was actually
            # anything publishable.
            with open(os.path.join(publish_dir, ".publish_info"), "w") as fd:
                for entry, d in publish_dates.items():
                    fd.write("%s %s\n" % (entry, d))

    def polish_directory(self, date):
        """Apply various bits of polish to a published directory."""
        target_dir = os.path.join(self.publish_base, date)

        checksum_directory(
            self.config, target_dir, old_directories=self.checksum_dirs,
            map_expr=r"s/\.\(img\|img\.gz\|iso\|iso\.gz\|tar\.gz\)$/.raw/")
        if self.config.project != "livecd-base":
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

        # Now, populate the .publish_info file with datestamps of published
        # binaries.
        self.create_publish_info_file(date)

    def link(self, date, name):
        osextras.symlink_force(date, os.path.join(self.publish_base, name))

    def published_images(self, date):
        """Return all the images published at a particular date (or alias)."""
        images = set()
        publish_dir = os.path.join(self.publish_base, date)
        for entry in osextras.listdir_force(publish_dir):
            entry_path = os.path.join(publish_dir, entry)
            if not self.tree.manifest_file_allowed(entry_path):
                continue
            if (entry.startswith("%s-" % self.config.series) or
                (self.config.subproject == "wubi" and
                 entry.endswith(".tar.xz")) or
                (self.config.project in ("ubuntu-core", "ubuntu-core-desktop",
                                         "ubuntu-appliance") and
                 self.image_type == "daily-live" and
                 entry.endswith(".img.xz"))):
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
                elif self.config.subproject == "wubi" and image_base == arch:
                    matches = True
                else:
                    matches = False
                if matches:
                    changed.add(image)
                    existing[image] = date
                    break

        # Update the list of tested images
        with open(os.path.join(self.publish_base,
                               date, ".marked_good"), "a+") as fd:
            fd.seek(0)
            current_entries = fd.read().split("\n")
            for entry in [image for image, image_date in existing.items()
                          if image_date == date]:
                if entry not in current_entries:
                    fd.write("%s\n" % entry)

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
                if not self.config.match_series(series):
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

                if project and entry_project.split("/", 1)[0] != project:
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

    def generate_lxd_metadata(self, date):
        """For the publisher cycle, generate the corresponding LXD metadata."""
        if self.config.project != "ubuntu-core":
            return
        if self.config.get("LXD_METADATA") == "0":
            logger.info("Skipping LXD metadata generation")
            return

        publish_date = os.path.join(self.publish_base, date)
        if not os.path.exists(publish_date):
            return

        logger.info("Generating LXD metadata for ubuntu-core %s ..." % date)
        for entry in os.listdir(publish_date):
            if not entry.endswith(".img.xz"):
                continue
            entry_path = os.path.join(publish_date, entry)
            try:
                generate_ubuntu_core_image_lxd_metadata(entry_path)
            except Exception as e:
                logger.error("Failed to generate LXD metadata for %s: %s" %
                             (entry_path, e))

    def post_qa(self, date, images):
        """Post a list of images to the QA tracker."""
        try:
            from isotracker import ISOTracker
        except ImportError:
            return

        tracker = None

        for image in images:
            image_bits = image.split("/")
            if len(image_bits) == 3:
                project, image_type, base = image_bits
                image_distribution = None
                image_series = None
            elif len(image_bits) == 4:
                project, image_series, image_type, base = image_bits
                image_distribution = None
            else:
                project, image_distribution, image_series, image_type, base = (
                    image_bits)
            base_match = re.match(r"(.*?)-(.*)-(.*)", base)
            if not base_match:
                continue
            dist, publish_type, arch = base_match.groups()
            product = self.qa_product(project, image_type, publish_type, arch)
            if product is None:
                logger.warning(
                    "No iso.qa.ubuntu.com product found for '%s', '%s', '%s', "
                    "'%s' (image: %s); skipping.",
                    project,
                    image_type,
                    publish_type,
                    arch,
                    image
                )
                continue

            # For Ubuntu Core projects we have a seperate set of milestones
            if project in ("ubuntu-core", "ubuntu-core-desktop",
                           "ubuntu-appliance"):
                # ...image_series in this case is 18, 20, 22 etc.
                dist = image_series
            target = "%s-%s" % (product[1], dist)

            # Try to figure out the path to the OVERSIZED indicator for the
            # build.
            iso_path_bits = [self.tree.project_base]
            if image_series is not None:
                if image_distribution is not None:
                    iso_path_bits.append(image_distribution)
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

            try:
                if tracker is None or tracker.target != target:
                    tracker = ISOTracker(target=target)
                tracker.post_build(product[0], date, note=note)
            except Exception:
                traceback.print_exc()

    def publish(self, date):
        if self.config.subtree:
            logger.info("Publishing for subtree '%s'" % self.config.subtree)
        self.new_publish_dir(date)
        published = []
        self.checksum_dirs = []
        if self.config.project == "livecd-base":
            for arch in self.config.cpuarches:
                published.extend(list(self.publish_livecd_base(arch, date)))
        elif self.config.subproject == "wubi":
            for arch in self.config.arches:
                published.extend(list(self.publish_wubi(arch, date)))
        else:
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

        target_report = os.path.join(self.publish_base, date, "report.html")
        osextras.unlink_force(target_report)

        self.generate_lxd_metadata(date)

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

    def get_purge_data(self, key, purge_type):
        path = os.path.join(self.config.root, "etc", purge_type)
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

    def purge(self, days=None, count=None):
        project_image_type = "%s/%s" % (self.project, self.image_type)

        if days is None:
            days = self.get_purge_data(self.project, "purge-days")
        if days is None:
            days = self.get_purge_data(project_image_type, "purge-days")
        if days is None:
            days = self.get_purge_data(self.image_type, "purge-days")

        if count is None:
            count = self.get_purge_data(self.project, "purge-count")
        if count is None:
            count = self.get_purge_data(project_image_type, "purge-count")
        if count is None:
            count = self.get_purge_data(self.image_type, "purge-count")

        if not days and not count:
            logger.info("Not purging images for %s" % project_image_type)
            return
        elif days and count:
            raise Exception("Both purge-days and purge-count are defined for "
                            "%s. Such scenario is currently unsupported." %
                            project_image_type)

        image_count = 0
        oldest = 0

        if days:
            logger.info(
                "Purging %s images older than %d %s ..." %
                (project_image_type, days, "day" if days == 1 else "days"))
            oldest = int(time.strftime(
                "%Y%m%d", time.gmtime(time.time() - 60 * 60 * 24 * days)))
        elif count:
            logger.info(
                "Purging %s images to leave only the latest %d %s ..." %
                (project_image_type, count,
                 "image" if count == 1 else "images"))

        to_purge = []
        publish_pending = os.path.join(self.publish_base, "pending")
        publish_current = os.path.join(self.publish_base, "current")
        publish_manual = os.path.join(self.publish_base, "manual")

        for entry in sorted(
                osextras.listdir_force(self.publish_base), reverse=True):
            entry_path = os.path.join(self.publish_base, entry)

            # Directory?
            if not os.path.isdir(entry_path):
                continue

            # Numeric directory?
            if not entry[0].isdigit():
                continue

            image_count += 1

            # Older than cut-off date?
            # Did we leave enough images already?
            # In the case where both cut-off date and image count have been
            # defined, we purge anything that doesn't satisfy both of the above
            # conditions at once
            if ((not days or oldest <= int(entry.split(".", 1)[0])) and
                    (not count or image_count <= count)):
                continue

            # Pointed to by "pending" or "current" symlink?
            if (os.path.islink(publish_pending) and
                    os.readlink(publish_pending) == entry):
                continue
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
            # Experimentally, we also support manually 'preserving' certain
            # images by using a 'manual' symlink to a published image set.
            if (os.path.islink(publish_manual) and
                    os.path.normpath(os.readlink(publish_manual)) == entry):
                continue

            to_purge.append((entry, entry_path))

        for entry, entry_path in to_purge:
            if self.config["DEBUG"] or self.config["CDIMAGE_NOPURGE"]:
                logger.info(
                    "Would purge %s/%s/%s" %
                    (self.project, self.image_type_dir, entry))
            else:
                logger.info(
                    "Purging %s/%s/%s" %
                    (self.project, self.image_type_dir, entry))
                if os.path.islink(entry_path):
                    osextras.unlink_force(entry_path)
                else:
                    shutil.rmtree(entry_path)


class ReleaseTreeMixin:
    """Additional methods for trees containing released images."""

    def tree_suffix(self, source):
        # Publish ports/daily to ports/releases/..., etc.
        ubuntu_projects = ("ubuntu-server", )
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
        if self.config.image_type == 'legacy-server':
            return self.project_base.replace('server', 'legacy-server')
        return self.project_base


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

    def url_for_path(self, path):
        series = self.config["DIST"]
        version = getattr(series, "pointversion", series.version)
        basename = os.path.basename(path)
        return "https://%s/%s/%s" % (self.site_name, version, basename)

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

    torrent_tracker = "https://torrent.ubuntu.com/announce"
    ipv6_torrent_tracker = "https://ipv6.torrent.ubuntu.com/announce"

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
        if publish_type == "wubi":
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
        command = ["mktorrent", "-a", self.torrent_tracker]
        if isinstance(self.tree, SimpleReleaseTree):
            command.extend(["-a", self.ipv6_torrent_tracker])
        command.extend([
            "--comment",
            "%s CD %s" % (self.config.capproject, self.tree.site_name),
            "--output",
            "%s.torrent" % path,
            path,
        ])
        if self.dry_run:
            logger.info(" ".join(shell_quote(arg) for arg in command))
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
    def full_version(self):
        if self.config.distribution == "ubuntu":
            return self.version
        else:
            return os.path.join(self.config.distribution, self.version)

    def publish_release_prefixes(self):
        # "beta-2" should end up in directories named "beta-2", but with
        # filenames including "beta2" (otherwise we get hyphen overload).
        if self.status.startswith("release"):
            filestatus = ""
        else:
            filestatus = self.status.replace("-", "")

        if self.official in ("yes", "poolonly", "named", "inteliot"):
            project = self.project
            version = self.version
            if project in ["ubuntu-server", "ubuntu-wsl"]:
                project = "ubuntu"
            # For intel-iot image publishing, we do not use pointversion
            # as the product does not follow the regular Ubuntu release
            # cadence.
            if self.official == "inteliot":
                version = self.config["DIST"].version
            prefix = "%s-%s" % (project, version)
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
        try:
            self.do("rm -rf %s" % path, shutil.rmtree, path)
        except OSError:
            pass

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

    def want_manifest(self, publish_type, path):
        if publish_type in (
            "live", "desktop", "desktop-canary", "desktop-legacy", "netbook",
            "uec", "server-uec", "core", "wubi", "server", "live-server",
            "legacy-server", "wsl",
        ):
            return True
        elif publish_type.startswith("preinstalled") and os.path.exists(path):
            return True
        elif publish_type == "dvd" and os.path.exists(path):
            # DVDs are allowed to not have .manifest files, but may have
            # them depending on configuration.
            return True
        else:
            return False

    def want_torrent(self, publish_type):
        raise NotImplementedError

    def publish_release_netboot(self, daily_dir, prefix, arch, image_path):
        """Publish release images for a single architecture."""
        source_tarname = ".%s-netboot-%s.tar.gz" % (self.config.series, arch)
        source_tarpath = os.path.join(daily_dir, source_tarname)

        if not os.path.exists(source_tarpath):
            return

        logger.info("Copying netboot-%s image ..." % (arch, ))

        target_tarname = "%s-netboot-%s.tar.gz" % (prefix, arch)
        target_tarpath = os.path.join(
            os.path.dirname(image_path), target_tarname)

        rewrite_and_unpack_tarball(
            self.dry_run, source_tarpath, target_tarpath,
            self.tree.url_for_path(image_path))

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

        main_img = None

        for ext in ("iso", "img", "img.gz", "img.xz", "tar.gz", "img.tar.gz",
                    "tar.xz", "wsl"):
            if os.path.exists(daily(ext)):
                main_img = daily(ext)
                break
        else:
            return

        # Copy, to make sure we have a canonical version of this.
        artifacts = ["iso", "list", "img", "img.gz", "img.xz", "tar.gz",
                     "img.tar.gz", "tar.xz", "bootimg", "custom.tar.gz",
                     "device.tar.gz", "azure.device.tar.gz", "wsl"]
        for ext in artifacts:
            if not os.path.exists(daily(ext)):
                continue
            if self.want_pool:
                self.hardlink(os.path.realpath(daily(ext)), pool(ext))
            if self.want_dist:
                self.symlink(pool(ext), dist(ext))
                if daily(ext) == main_img:
                    self.publish_release_netboot(
                        os.path.dirname(daily(ext)),
                        prefix_status,
                        arch,
                        dist(ext))
            if self.want_full:
                self.hardlink(os.path.realpath(daily(ext)), full(ext))
                if daily(ext) == main_img:
                    self.publish_release_netboot(
                        os.path.dirname(daily(ext)),
                        prefix,
                        arch,
                        full(ext))

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

        if self.want_manifest(publish_type, daily("manifest")):
            # Copy, to make sure we have a canonical version of this.
            if self.want_pool:
                self.copy(daily("manifest"), pool("manifest"))
            if self.want_dist:
                self.symlink(pool("manifest"), dist("manifest"))
            if self.want_full:
                self.copy(daily("manifest"), full("manifest"))

        for ext in "iso", "img", "img.gz", "img.xz", "tar.gz":
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
            elif self.want_full and self.official in ("named", "inteliot"):
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
        self.config["IMAGE_TYPE"] = publish_type
        prefix, prefix_status = self.publish_release_prefixes()

        # Do what I mean.
        if source.endswith("/source"):
            source = source[:-len("/source")]

        if series.distribution != "ubuntu" or not series.is_latest:
            # TODO does this need "legacy" handling?
            if source == "ubuntu-server/daily":
                source = os.path.join(
                    "ubuntu-server", series.full_name, "daily")
            elif source == "ubuntu-server/daily-live":
                source = os.path.join(
                    "ubuntu-server", series.full_name, "daily-live")
            elif source == "ubuntu-server/daily-preinstalled":
                source = os.path.join(
                    "ubuntu-server", series.full_name, "daily-preinstalled")
            elif source == "ubuntu-wsl/daily-live":
                source = os.path.join(
                    "ubuntu-wsl", series.full_name, "daily-live")
            else:
                source = os.path.join(series.full_name, source)

        daily_dir = self.daily_dir(source, date, publish_type)
        target_dir = self.target_dir(source, date, publish_type)
        if not self.want_full:
            pool_dir = self.pool_dir(source)

        if publish_type == "src":
            # Perverse, but works.
            arches = self.find_source_images(daily_dir, series.name)
            # Coherence-check.
            if not arches:
                raise PublishReleaseException(
                    "No source daily for %s on %s!" % (series, date))

        # Override the architecture list for these types unconditionally.
        # TODO: should reset default-arches for the source project instead
        if (publish_type == "netbook" and
                not [arch for arch in arches if arch.startswith("armel")]):
            arches = ["i386"]

        # Coherence-check.
        if publish_type not in ("netbook", "src"):
            for arch in arches:
                paths = []
                for ext in ("iso", "img", "img.gz", "img.xz", "img.tar.gz",
                            "tar.gz", "wsl"):
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
            if series.name != series.version:
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
            if not self.config["CDIMAGE_NO_PURGE"]:
                if self.want_dist:
                    self.remove_tree(torrent_dir)
                if self.want_full:
                    torrent_releases_dir = os.path.dirname(
                        os.path.dirname(torrent_dir))
                    for entry in osextras.listdir_force(torrent_releases_dir):
                        entry_path = os.path.join(torrent_releases_dir, entry)
                        if entry != self.status and os.path.isdir(entry_path):
                            self.remove_tree(entry_path)
                    self.remove_tree(torrent_dir)
            os.makedirs(torrent_dir, exist_ok=True)

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
        if self.want_full:
            logger.info("Checksumming full tree ...")
            self.checksum_directory(
                [target_dir, daily_dir],
                map_expr="s/^%s-/%s-/" % (prefix, series))

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

        self.refresh_simplestreams()

        logger.info(
            "Done!  Remember to sync-mirrors after checking that everything "
            "is OK.")


class FullReleasePublisher(ReleasePublisher):
    """An object that can publish releases in a "full" layout.

    This layout is used in the directory trees managed by DailyTree.
    """

    def __init__(self, *args, **kwargs):
        super(FullReleasePublisher, self).__init__(*args, **kwargs)
        assert self.official in ("named", "no", "inteliot")
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
            self.tree.publish_target(source), "releases",
            self.config.full_series, self.status)
        if self.official == "inteliot":
            target_dir = os.path.join(target_dir, "inteliot")
        if date.endswith("/unpacked"):
            target_dir = os.path.join(target_dir, "unpacked")
        if publish_type == "src":
            target_dir = os.path.join(target_dir, "source")
        return target_dir

    def version_link(self, source):
        return os.path.join(
            self.tree.publish_target(source), "releases", self.full_version)

    def torrent_dir(self, source, publish_type):
        torrent_tree = TorrentTree(self.config)
        return os.path.join(
            torrent_tree.publish_target(source), "releases",
            self.config.full_series, self.status, publish_type)

    def want_torrent(self, publish_type):
        if self.official == "inteliot":
            return False
        else:
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
            self.tree.publish_target(source), self.config.full_series)
        if publish_type == "src":
            target_dir = os.path.join(target_dir, "source")
        return target_dir

    def version_link(self, source):
        return os.path.join(
            self.tree.publish_target(source), self.full_version)

    def pool_dir(self, source):
        return os.path.join(self.tree.publish_target(source), ".pool")

    def torrent_dir(self, source, publish_type):
        torrent_tree = TorrentTree(self.config)
        return os.path.join(
            torrent_tree.publish_target(source), "simple",
            self.config.full_series, publish_type)

    def want_torrent(self, publish_type):
        if self.want_dist:
            return publish_type not in ("src", "uec", "server-uec")
        else:
            return False
