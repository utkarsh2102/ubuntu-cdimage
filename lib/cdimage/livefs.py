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

"""Live filesystems."""

from __future__ import print_function

__metaclass__ = type

from contextlib import closing
import io
import os
import re
import subprocess
from textwrap import dedent
import time
try:
    from urllib.error import URLError
    from urllib.request import urlopen
except ImportError:
    from urllib2 import URLError, urlopen

from cdimage import osextras
from cdimage.config import Series
from cdimage.log import logger
from cdimage.mail import get_notify_addresses, send_mail


class UnknownArchitecture(Exception):
    pass


class NoLiveItem(Exception):
    pass


class UnknownLiveItem(Exception):
    pass


class NoFilesystemImages(Exception):
    pass


def split_arch(arch):
    arch_bits = arch.split("+", 1)
    if len(arch_bits) == 1:
        arch_bits.append("")
    cpuarch, subarch = arch_bits

    if cpuarch == "amd64" and subarch == "mac":
        # Use normal amd64 live image on amd64+mac.
        subarch = ""

    return cpuarch, subarch


def live_builder(config, arch):
    cpuarch, subarch = split_arch(arch)
    project = config.project
    series = config["DIST"]

    if cpuarch == "amd64":
        return "kapok.buildd"
    elif cpuarch == "armel":
        return "celbalrai.buildd"
    elif cpuarch == "armhf":
        # TODO: These builders should be separated out again; or, better,
        # moved into the LP build farm.
        if project == "ubuntu":
            if subarch in ("mx5", "omap", "omap4"):
                return "cadejo.buildd"
        elif project == "ubuntu-server":
            if subarch == "omap":
                return "cadejo.buildd"
            elif subarch == "omap4":
                return "celbalrai.buildd"
        if subarch in ("ac100", "nexus7"):
            return "celbalrai.buildd"
        return "cadejo.buildd"
    elif cpuarch == "hppa":
        return "castilla.buildd"
    elif cpuarch == "i386":
        return "cardamom.buildd"
    elif cpuarch == "ia64":
        return "weddell.buildd"
    elif cpuarch == "lpia":
        if series <= "hardy":
            return "cardamom.buildd"
        else:
            return "concordia.buildd"
    elif cpuarch == "powerpc":
        return "royal.buildd"
    elif cpuarch == "sparc":
        return "vivies.buildd"
    else:
        raise UnknownArchitecture(
            "No live filesystem builder known for %s" % arch)


def live_build_options(config, arch):
    options = []

    cpuarch, subarch = split_arch(arch)
    if (cpuarch in ("armel", "armhf") and
            config.image_type == "daily-preinstalled"):
        if subarch in ("mx5", "omap", "omap4"):
            options.extend(["-f", "ext4"])
        elif subarch in ("ac100", "nexus7"):
            options.extend(["-f", "plain"])

    if config.project == "ubuntu-core":
        options.extend(["-f", "plain"])

    if config.subproject == "wubi":
        if config["DIST"] >= "quantal":
            # TODO: Turn this back on once Wubi's resize2fs supports it.
            #options.extend(["-f", "ext4"])
            options.extend(["-f", "ext3"])
        else:
            options.extend(["-f", "ext3"])

    return options


def live_project(config, arch):
    project = config.project
    series = config["DIST"]

    if project == "livecd-base":
        liveproject = "base"
    elif project == "tocd3.1":
        liveproject = "tocd"
    else:
        liveproject = project

    cpuarch, subarch = split_arch(arch)
    if cpuarch == "lpia" and series <= "hardy":
        liveproject = "%s-lpia" % liveproject

    if config["CDIMAGE_DVD"]:
        if ((project in ("ubuntu", "kubuntu") and series >= "hardy") or
                (project == "edubuntu" and series >= "karmic") or
                (project == "ubuntustudio" and series >= "precise")):
            liveproject += "-dvd"

    return liveproject


def live_build_command(config, arch):
    command = [
        "ssh", "-n", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
        "buildd@%s" % live_builder(config, arch),
        "/home/buildd/bin/BuildLiveCD",
    ]

    if config["UBUNTU_DEFAULTS_LOCALE"]:
        command.extend(["-u", config["UBUNTU_DEFAULTS_LOCALE"]])
    elif config["DIST"] >= "oneiric":
        command.append("-l")

    command.extend(live_build_options(config, arch))

    cpuarch, subarch = split_arch(arch)
    if cpuarch:
        command.extend(["-A", cpuarch])
    if subarch:
        command.extend(["-s", subarch])

    if config["PROPOSED"]:
        command.append("-p")
    if config.series:
        command.extend(["-d", config.series])

    if config.subproject:
        command.extend(["-r", config.subproject])
    command.append(live_project(config, arch))

    return command


# TODO: This is only used for logging, so it might be worth unifying with
# live_build_notify_failure.
def live_build_full_name(config, arch):
    bits = [config.project]
    if config.subproject:
        bits.append(config.subproject)
    cpuarch, subarch = split_arch(arch)
    bits.append(cpuarch)
    if subarch:
        bits.append(subarch)
    return "-".join(bits)


def live_build_notify_failure(config, arch):
    if config["DEBUG"]:
        return

    project = config.project
    recipients = get_notify_addresses(config, project)
    if not recipients:
        return

    livefs_id_bits = [project]
    if config.subproject:
        livefs_id_bits.append(config.subproject)
    cpuarch, subarch = split_arch(arch)
    if subarch:
        livefs_id_bits.append(subarch)
    if config["UBUNTU_DEFAULTS_LOCALE"]:
        livefs_id_bits.append(config["UBUNTU_DEFAULTS_LOCALE"])
    livefs_id = "-".join(livefs_id_bits)

    # TODO: We have to guess the datestamp, which is unreliable.  We could
    # do better by listing the remote directory.
    datestamp = time.strftime("%Y%m%d")
    log_url = "http://%s/~buildd/LiveCD/%s/%s/latest/livecd-%s-%s.out" % (
        live_builder(config, arch), config.series, livefs_id,
        datestamp, cpuarch)
    try:
        with closing(urlopen(log_url)) as f:
            body = f.read()
    except URLError:
        body = b""
    subject = "LiveFS %s%s/%s/%s failed to build on %s" % (
        "(built by %s) " % config["SUDO_USER"] if config["SUDO_USER"] else "",
        livefs_id, config.series, arch, datestamp)
    send_mail(subject, "buildlive", recipients, body)


def run_live_builds(config):
    builds = {}
    for arch in config.arches:
        if arch == "amd64+mac":
            # Use normal amd64 live image on amd64+mac.
            continue
        full_name = live_build_full_name(config, arch)
        machine = live_builder(config, arch)
        timestamp = time.strftime("%F %T")
        logger.info(
            "%s on %s starting at %s" % (full_name, machine, timestamp))
        proc = subprocess.Popen(live_build_command(config, arch))
        builds[proc.pid] = (proc, arch, full_name, machine)

    success = False
    while builds:
        pid, status = os.wait()
        if pid not in builds:
            continue
        proc, arch, full_name, machine = builds.pop(pid)
        timestamp = time.strftime("%F %T")
        text_status = "success" if status == 0 else "failed"
        logger.info("%s on %s finished at %s (%s)" % (
            full_name, machine, timestamp, text_status))
        if status == 0:
            success = True
        else:
            live_build_notify_failure(config, arch)

    if not success:
        logger.error("No live filesystem builds succeeded.")
    return success


def livecd_base(config, arch):
    if config["LIVECD_BASE"]:
        return config["LIVECD_BASE"]

    cpuarch, subarch = split_arch(arch)
    series = config["DIST"]

    if config["LIVECD"]:
        root = config["LIVECD"]
    else:
        root = "http://%s/~buildd/LiveCD" % live_builder(config, arch)

    liveproject = live_project(config, arch)
    if config["SUBPROJECT"]:
        liveproject += "-%s" % config["SUBPROJECT"]
    if subarch:
        liveproject += "-%s" % subarch
    if config["UBUNTU_DEFAULTS_LOCALE"]:
        liveproject += "-%s" % config["UBUNTU_DEFAULTS_LOCALE"]

    return "%s/%s/%s/current" % (root, series, liveproject)


def flavours(config, arch):
    cpuarch, subarch = split_arch(arch)
    project = config.project
    series = config["DIST"]

    if cpuarch == "amd64":
        if series <= "dapper":
            return ["amd64-generic"]
        elif series <= "oneiric":
            return ["generic"]
        else:
            if project == "ubuntustudio":
                return ["lowlatency"]
            else:
                return ["generic"]
    elif cpuarch == "armel":
        if series == "jaunty":
            # We don't have any fallback flavour on armel.
            return ["imx51"]
        else:
            if subarch == "mx5":
                return ["linaro-lt-mx5"]
            else:
                # Assume one kernel flavour for each subarch named like the
                # subarch.
                return [subarch]
    elif cpuarch == "armhf":
        if subarch == "mx5":
            return ["linaro-lt-mx5"]
        else:
            return [subarch]
    elif cpuarch == "hppa":
        return ["hppa32", "hppa64"]
    elif cpuarch == "i386":
        if series <= "dapper":
            return ["i386"]
        elif series <= "oneiric":
            return ["generic"]
        elif series <= "precise":
            if project in ("ubuntu", "edubuntu", "mythbuntu"):
                # lts-quantal
                return ["generic"]
            elif project in ("xubuntu", "lubuntu"):
                # non-PAE
                return ["generic"]
            elif project == "ubuntustudio":
                return ["lowlatency-pae"]
            else:
                return ["generic-pae"]
        else:
            if project == "ubuntustudio":
                return ["lowlatency"]
            else:
                return ["generic"]
    elif cpuarch == "ia64":
        if series <= "dapper":
            return ["itanium-smp", "mckinley-smp"]
        elif series <= "jaunty":
            return ["itanium", "mckinley"]
        else:
            return ["ia64"]
    elif cpuarch == "lpia":
        return ["lpia"]
    elif cpuarch == "powerpc":
        if subarch == "ps3" and series <= "gutsy":
            return ["cell"]
        elif series <= "oneiric":
            return ["powerpc", "powerpc64-smp"]
        elif series <= "quantal":
            return ["powerpc-smp", "powerpc64-smp"]
        else:
            return ["powerpc-smp", "powerpc64-smp",
                    "powerpc-e500", "powerpc-e500mc"]
    elif cpuarch == "sparc":
        return ["sparc64"]
    else:
        raise UnknownArchitecture(
            "No live filesystem source known for %s" % arch)


def live_item_path_winfoss(config, arch):
    # This is a mess of special cases.  Fortunately it is now only of
    # historical interest.
    cpuarch, subarch = split_arch(arch)
    project = config.project
    series = config["DIST"]

    if series == "warty" or cpuarch not in ("amd64", "i386"):
        raise NoLiveItem

    maitri = "http://maitri.ubuntu.com/theopencd"
    henrik = "http://people.canonical.com/~henrik/winfoss"

    if project == "ubuntu":
        if series == "hoary":
            if cpuarch == "i386":
                return "%s/ubuntu/winfoss/latest/Hoary-WinFOSS.tgz" % maitri
            elif cpuarch == "amd64":
                return ("%s/ubuntu/amd64/latest/"
                        "Hoary-WinFOSS-amd64.tgz" % maitri)
        elif series == "breezy":
            return "%s/winfoss/ubuntu/current/Ubuntu-WinFOSS-5.10.tgz" % maitri
        elif series >= "dapper" and series <= "karmic":
            if series > "gutsy":
                series = Series.find_by_name("gutsy")
            return "%s/%s/ubuntu/current/ubuntu-winfoss-%s.tar.gz" % (
                henrik, series, series.version)
    elif project == "kubuntu":
        if series == "hoary" and cpuarch == "i386":
            return ("%s/kubuntu/winfoss/latest/"
                    "Kubuntu-WinFOSS-i386.tgz" % maitri)
        elif series == "breezy":
            if cpuarch == "i386":
                return ("%s/winfoss/kubuntu/current/"
                        "Kubuntu-WinFOSS-5.10.tgz" % maitri)
            elif cpuarch == "amd64":
                return ("%s/winfoss/kubuntu-AMD/current/"
                        "Kubuntu-WinFOSS-5.10-AMD.tgz" % maitri)
        elif series >= "dapper" and series <= "karmic":
            if series > "gutsy":
                series = Series.find_by_name("gutsy")
            return "%s/%s/kubuntu/current/kubuntu-winfoss-%s.tar.gz" % (
                henrik, series, series.version)
    elif project == "edubuntu":
        if series >= "feisty" and series <= "karmic":
            if series > "gutsy":
                series = Series.find_by_name("gutsy")
            return "%s/%s/edubuntu/current/edubuntu-winfoss-%s.tar.gz" % (
                henrik, series, series.version)
    elif project == "tocd3" and cpuarch == "i386":
            return "%s/tocd3/fsm/TOCD3.tgz" % maitri
    elif project == "tocd3.1" and cpuarch == "i386":
            return "%s/winfoss/tocd3.1/current/TOCD-31.tgz" % maitri

    raise NoLiveItem


def live_item_paths(config, arch, item):
    cpuarch, subarch = split_arch(arch)
    project = config.project
    series = config["DIST"]
    root = livecd_base(config, arch)
    liveproject = live_project(config, arch)
    if subarch:
        liveproject_subarch = "%s-%s" % (liveproject, subarch)
    else:
        liveproject_subarch = liveproject

    if item in (
        "cloop", "squashfs", "manifest", "manifest-desktop", "manifest-remove",
        "size", "ext2", "ext3", "ext4", "rootfs.tar.gz", "tar.xz", "iso",
    ):
        if project == "tocd3":
            # auto-purged - reverting to plan B
            yield "/home/cjwatson/tocd3/livecd.tocd3.%s" % item
        elif project == "ubuntu" and series == "breezy":
            # auto-purged - reverting to plan B
            yield "/home/cjwatson/breezy-live/ubuntu/livecd.%s.%s" % (
                arch, item)
        else:
            yield "%s/livecd.%s.%s" % (root, liveproject_subarch, item)
    elif item in ("kernel", "initrd", "bootimg"):
        for flavour in flavours(config, arch):
            yield "%s/livecd.%s.%s-%s" % (
                root, liveproject_subarch, item, flavour)
    elif item == "kernel-efi-signed":
        if series >= "precise" and arch == "amd64":
            for flavour in flavours(config, arch):
                yield "%s/livecd.%s.kernel-%s.efi.signed" % (
                    root, liveproject_subarch, flavour)
        else:
            raise NoLiveItem
    elif item == "winfoss":
        yield live_item_path_winfoss(config, arch)
    elif item == "wubi":
        if (project != "xubuntu" and arch in ("amd64", "i386") and
                series >= "gutsy"):
            yield ("http://people.canonical.com/~evand/wubi/%s/stable" %
                   series.name)
        else:
            raise NoLiveItem
    elif item == "umenu":
        if arch in ("amd64", "i386") and series == "hardy":
            yield "http://people.canonical.com/~evand/umenu/stable"
        else:
            raise NoLiveItem
    elif item == "usb-creator":
        if arch in ("amd64", "i386"):
            yield ("http://people.canonical.com/~evand/usb-creator/%s/"
                   "stable" % series.name)
        else:
            raise NoLiveItem
    elif item == "ltsp-squashfs":
        if arch == "amd64":
            # use i386 LTSP image on amd64 too
            root = livecd_base(config, "i386")
        if arch in ("amd64", "i386"):
            yield "%s/livecd.%s-ltsp.squashfs" % (root, liveproject)
        else:
            raise NoLiveItem
    else:
        raise UnknownLiveItem("Unknown live filesystem item '%s'" % item)


def live_output_directory(config):
    project = config.project
    if config["UBUNTU_DEFAULTS_LOCALE"]:
        project = "-".join([project, config["UBUNTU_DEFAULTS_LOCALE"]])
    return os.path.join(
        config.root, "scratch", project, config.series, config.image_type,
        "live")


def download_live_items(config, arch, item):
    output_dir = live_output_directory(config)
    found = False

    try:
        if item == "server-squashfs":
            original_project = config.project
            try:
                config["PROJECT"] = "ubuntu-server"
                urls = list(live_item_paths(config, arch, "squashfs"))
            finally:
                config["PROJECT"] = original_project
        else:
            urls = list(live_item_paths(config, arch, item))
    except NoLiveItem:
        return False

    if item in ("kernel", "initrd", "bootimg"):
        for url in urls:
            flavour = re.sub(r"^.*?\..*?\..*?-", "", os.path.basename(url))
            target = os.path.join(
                output_dir, "%s.%s-%s" % (arch, item, flavour))
            if osextras.fetch(config, url, target):
                found = True
    elif item == "kernel-efi-signed":
        for url in urls:
            base = os.path.basename(url)
            if base.endswith(".efi.signed"):
                base = base[:-len(".efi.signed")]
            flavour = re.sub(r"^.*?\..*?\..*?-", "", base)
            target = os.path.join(
                output_dir, "%s.kernel-%s.efi.signed" % (arch, flavour))
            if osextras.fetch(config, url, target):
                found = True
    elif item in ("wubi", "umenu", "usb-creator"):
        target = os.path.join(output_dir, "%s.%s.exe" % (arch, item))
        if osextras.fetch(config, urls[0], target):
            found = True
    elif item == "winfoss":
        target = os.path.join(output_dir, "%s.%s.tgz" % (arch, item))
        if osextras.fetch(config, urls[0], target):
            found = True
    else:
        target = os.path.join(output_dir, "%s.%s" % (arch, item))
        if osextras.fetch(config, urls[0], target):
            found = True
    return found


def write_autorun(config, arch, name, label):
    output_dir = live_output_directory(config)
    autorun_path = os.path.join(output_dir, "%s.autorun.inf" % arch)
    with io.open(autorun_path, "w", newline="\r\n") as autorun:
        if str is bytes:
            u = lambda s: unicode(s, "unicode_escape")
        else:
            u = lambda s: s
        print(u(dedent("""\
            [autorun]
            open=%s
            icon=%s,0
            label=%s

            [Content]
            MusicFiles=false
            PictureFiles=false
            VideoFiles=false""")) % (u(name), u(name), u(label)), file=autorun)


def download_live_filesystems(config):
    project = config.project
    series = config["DIST"]

    output_dir = live_output_directory(config)
    osextras.mkemptydir(output_dir)

    if (config["CDIMAGE_LIVE"] or config["CDIMAGE_SQUASHFS_BASE"] or
            config["CDIMAGE_PREINSTALLED"]):
        got_image = False
        for arch in config.arches:
            if config["CDIMAGE_PREINSTALLED"]:
                if download_live_items(config, arch, "ext4"):
                    got_image = True
                elif download_live_items(config, arch, "ext3"):
                    got_image = True
                elif download_live_items(config, arch, "ext2"):
                    got_image = True
                elif download_live_items(config, arch, "rootfs.tar.gz"):
                    got_image = True
                else:
                    continue
            elif config["UBUNTU_DEFAULTS_LOCALE"]:
                if download_live_items(config, arch, "iso"):
                    got_image = True
                else:
                    continue
            elif download_live_items(config, arch, "cloop"):
                got_image = True
            elif download_live_items(config, arch, "squashfs"):
                got_image = True
            elif download_live_items(config, arch, "rootfs.tar.gz"):
                got_image = True
            elif download_live_items(config, arch, "tar.xz"):
                got_image = True
            else:
                continue
            if (series >= "dapper" and project != "ubuntu-core" and
                    not config["CDIMAGE_SQUASHFS_BASE"] and
                    config.subproject != "wubi"):
                download_live_items(config, arch, "kernel")
                download_live_items(config, arch, "initrd")
                download_live_items(config, arch, "kernel-efi-signed")
                if config["CDIMAGE_PREINSTALLED"]:
                    download_live_items(config, arch, "bootimg")
            download_live_items(config, arch, "manifest")
            if not download_live_items(config, arch, "manifest-remove"):
                download_live_items(config, arch, "manifest-desktop")
            download_live_items(config, arch, "size")

            if (config["UBUNTU_DEFAULTS_LOCALE"] or
                    config["CDIMAGE_PREINSTALLED"] or
                    config.subproject == "wubi"):
                continue

            if (project not in ("livecd-base", "ubuntu-core",
                                "kubuntu-active") and
                    (project != "edubuntu" or series >= "precise")):
                if series <= "feisty":
                    pass
                elif series <= "intrepid":
                    if config["CDIMAGE_DVD"] != "1":
                        download_live_items(config, arch, "wubi")
                    download_live_items(config, arch, "umenu")
                    umenu_path = os.path.join(
                        output_dir, "%s.umenu.exe" % arch)
                    if os.path.exists(umenu_path):
                        write_autorun(config, arch, "umenu.exe", "Install")
                else:
                    # TODO: We still have to do something about not
                    # including Wubi on the DVDs.
                    download_live_items(config, arch, "wubi")
                    wubi_path = os.path.join(output_dir, "%s.wubi.exe" % arch)
                    if os.path.exists(wubi_path):
                        # Nicely format the distribution name.
                        def upper_first(m):
                            text = m.group(0)
                            return text[0].upper() + text[1:]

                        autorun_project = re.sub(
                            r"(\b[a-z])", upper_first,
                            project.replace("-", " "))
                        write_autorun(
                            config, arch, "wubi.exe",
                            "Install %s" % autorun_project)

            if project not in ("livecd-base", "ubuntu-core", "edubuntu"):
                if (project in ("kubuntu-active", "ubuntu-netbook",
                                "ubuntu-moblin-remix") or
                        config["CDIMAGE_DVD"] or
                        series >= "maverick"):
                    download_live_items(config, arch, "usb-creator")

        if not got_image:
            raise NoFilesystemImages("No filesystem images found.")

    if (project == "edubuntu" and config["CDIMAGE_INSTALL"] and
            series <= "hardy"):
        for cpuarch in config.cpuarches:
            download_live_items(config, arch, "winfoss")

    if project == "edubuntu" and config["CDIMAGE_DVD"] and series >= "lucid":
        for arch in config.arches:
            if arch in ("amd64", "i386"):
                # TODO: Disabled for raring (LP: #1154601)
                #if series >= "raring":
                #    # Fetch the Ubuntu Server squashfs for Edubuntu Server.
                #    download_live_items(config, arch, "server-squashfs")

                # Fetch the i386 LTSP chroot for Edubuntu Terminal Server.
                download_live_items(config, arch, "ltsp-squashfs")
