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

from contextlib import closing
import fnmatch
from gzip import GzipFile
import io
import os
import re
import subprocess
from textwrap import dedent
import time
try:
    from urllib.error import URLError
    from urllib.parse import unquote
    from urllib.request import urlopen
except ImportError:
    from urllib2 import URLError, unquote, urlopen

from cdimage import osextras, sign
from cdimage.config import Touch
from cdimage.launchpad import get_launchpad
from cdimage.log import logger
from cdimage.mail import get_notify_addresses, send_mail
from cdimage.tracker import tracker_set_rebuild_status

__metaclass__ = type


class UnknownArchitecture(Exception):
    pass


class UnknownLiveItem(Exception):
    pass


class NoFilesystemImages(Exception):
    pass


class LiveBuildsFailed(Exception):
    pass


class UnknownLaunchpadLiveFS(Exception):
    pass


class MissingLaunchpadLiveFS(Exception):
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

    path = os.path.join(config.root, "production", "livefs-builders")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    f_project, f_series, f_arch, builder = line.split(None, 3)
                except ValueError:
                    continue
                if not fnmatch.fnmatchcase(project, f_project):
                    continue
                if not config.match_series(f_series):
                    continue
                if "+" in f_arch:
                    want_arch = arch
                else:
                    want_arch = cpuarch
                if not fnmatch.fnmatchcase(want_arch, f_arch):
                    continue
                return builder

    raise UnknownArchitecture("No live filesystem builder known for %s" % arch)


def live_build_options(config, arch):
    options = []

    cpuarch, subarch = split_arch(arch)
    if (cpuarch in ("armel", "armhf") and
            config.image_type == "daily-preinstalled"):
        if subarch in ("mx5", "omap", "omap4"):
            options.extend(["-f", "ext4"])
        elif subarch in ("ac100", "nexus7"):
            options.extend(["-f", "plain"])

    if (config.project in ("ubuntu-base", "ubuntu-core", "ubuntu-touch",
                           "ubuntu-touch-custom") or
        (config.project == "ubuntu-desktop-next" and
         config.subproject == "system-image")):
        options.extend(["-f", "plain"])

    if config.subproject == "wubi":
        if config["DIST"] >= "quantal":
            # TODO: Turn this back on once Wubi's resize2fs supports it.
            # options.extend(["-f", "ext4"])
            options.extend(["-f", "ext3"])
        else:
            options.extend(["-f", "ext3"])

    return options


def live_project(config, arch):
    project = config.project

    if project == "livecd-base":
        liveproject = "base"
    elif project == "ubuntu-touch-custom":
        liveproject = "ubuntu-touch"
    elif (project == "ubuntu-server" and
          config.image_type == "daily-preinstalled"):
        liveproject = "ubuntu-cpc"
    else:
        liveproject = project

    cpuarch, subarch = split_arch(arch)

    if config["CDIMAGE_DVD"]:
        if project in ("ubuntu", "kubuntu", "edubuntu", "ubuntustudio"):
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

    if config.get("PROPOSED", "0") not in ("", "0"):
        command.append("-p")
    if config.series:
        command.extend(["-d", config.series])

    if config.subproject:
        command.extend(["-r", config.subproject])
    command.append(live_project(config, arch))

    return command


def live_build_lp_kwargs(config, lp, lp_livefs, arch):
    cpuarch, subarch = split_arch(arch)
    kwargs = {}
    metadata_override = {}

    lp_ds = lp_livefs.distro_series
    if config["EXTRA_PPAS"]:
        ppa = config["EXTRA_PPAS"].split()[0]
        ppa = ppa.split(":", 1)[0]
        ppa_owner_name, ppa_name = ppa.split("/", 1)
        ppa = lp.people[ppa_owner_name].getPPAByName(name=ppa_name)
        kwargs["archive"] = ppa
    else:
        kwargs["archive"] = lp_ds.main_archive
    kwargs["distro_arch_series"] = lp_ds.getDistroArchSeries(archtag=cpuarch)
    if subarch:
        kwargs["unique_key"] = subarch
        metadata_override["subarch"] = subarch

    if config.get("PROPOSED", "0") not in ("", "0"):
        kwargs["pocket"] = "Proposed"
        metadata_override["proposed"] = True
    elif config["DIST"].is_latest:
        kwargs["pocket"] = "Release"
    else:
        kwargs["pocket"] = "Updates"

    if config["EXTRA_PPAS"]:
        metadata_override["extra_ppas"] = config["EXTRA_PPAS"].split()

    if config["CHANNEL"]:
        try:
            kwargs["unique_key"] += "_" + config["CHANNEL"]
        except KeyError:
            kwargs["unique_key"] = config["CHANNEL"]

        metadata_override["channel"] = config["CHANNEL"]

    if metadata_override:
        kwargs["metadata_override"] = metadata_override

    return kwargs


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


def live_build_notify_failure(config, arch, lp_build=None):
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

    datestamp = time.strftime("%Y%m%d")
    try:
        if lp_build is not None:
            if lp_build.build_log_url is None:
                raise URLError(
                    "Failed build %s has no build_log_url" % lp_build.web_link)
            with closing(urlopen(lp_build.build_log_url, timeout=30)) as comp:
                with closing(io.BytesIO(comp.read())) as comp_bytes:
                    with closing(GzipFile(fileobj=comp_bytes)) as f:
                        body = f.read()
        else:
            log_url = "http://%s/~buildd/LiveCD/%s/%s/latest/livecd-%s.out" % (
                live_builder(config, arch), config.series, livefs_id, cpuarch)
            with closing(urlopen(log_url, timeout=30)) as f:
                body = f.read()
    except URLError:
        body = b""
    subject = "LiveFS %s%s/%s/%s failed to build on %s" % (
        "(built by %s) " % config["SUDO_USER"] if config["SUDO_USER"] else "",
        livefs_id, config.full_series, arch, datestamp)
    send_mail(subject, "buildlive", recipients, body)


def live_lp_info(config, arch):
    cpuarch, subarch = split_arch(arch)
    want_project_bits = [config.project]
    if config.subproject:
        want_project_bits.append(config.subproject)
    if config["UBUNTU_DEFAULTS_LOCALE"]:
        want_project_bits.append(config["UBUNTU_DEFAULTS_LOCALE"])
    want_project = "-".join(want_project_bits)
    image_type = config.image_type

    path = os.path.join(config.root, "production", "livefs-launchpad")
    if not os.path.exists(path):
        path = os.path.join(config.root, "etc", "livefs-launchpad")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    f_project, f_image_type, f_series, f_arch, lp_info = (
                        line.split(None, 4))
                except ValueError:
                    continue
                if not fnmatch.fnmatchcase(want_project, f_project):
                    continue
                if not fnmatch.fnmatchcase(image_type, f_image_type):
                    continue
                if not config.match_series(f_series):
                    continue
                if "+" in f_arch:
                    want_arch = arch
                else:
                    want_arch = cpuarch
                if not fnmatch.fnmatchcase(want_arch, f_arch):
                    continue
                return lp_info.split("/")

    raise UnknownLaunchpadLiveFS(
        "No Launchpad live filesystem definition known for %s/%s/%s/%s" %
        (want_project, image_type, config.full_series, arch))


def get_lp_livefs(config, arch):
    try:
        lp_info = live_lp_info(config, arch)
    except UnknownLaunchpadLiveFS:
        return None, None
    if len(lp_info) > 2:
        instance, owner, name = lp_info
    else:
        instance = None
        owner, name = lp_info
    lp = get_launchpad(instance)
    lp_owner = lp.people[owner]
    lp_distribution = lp.distributions[config.distribution]
    lp_ds = lp_distribution.getSeries(name_or_version=config.series)
    livefs = lp.livefses.getByName(
        owner=lp_owner, distro_series=lp_ds, name=name)
    if livefs is None:
        raise MissingLaunchpadLiveFS(
            "Live filesystem %s/%s/%s not found on %s" %
            (owner, config.full_series, name, lp._root_uri))
    return lp, livefs


def run_live_builds(config):
    builds = {}
    lp_builds = []
    for arch in config.arches:
        if arch == "amd64+mac":
            # Use normal amd64 live image on amd64+mac.
            continue
        full_name = live_build_full_name(config, arch)
        timestamp = time.strftime("%F %T")
        lp, lp_livefs = get_lp_livefs(config, arch)
        if lp_livefs is None:
            machine = live_builder(config, arch)
        else:
            machine = "Launchpad"
        logger.info(
            "%s on %s starting at %s" % (full_name, machine, timestamp))
        tracker_set_rebuild_status(config, [0, 1], 2, arch)
        if lp_livefs is not None:
            lp_kwargs = live_build_lp_kwargs(config, lp, lp_livefs, arch)
            lp_build = lp_livefs.requestBuild(**lp_kwargs)
            logger.info("%s: %s" % (full_name, lp_build.web_link))
            lp_builds.append((lp_build, arch, full_name, machine, None))
        else:
            proc = subprocess.Popen(live_build_command(config, arch))
            builds[proc.pid] = (proc, arch, full_name, machine)

    successful = set()

    def live_build_finished(arch, full_name, machine, status, text_status,
                            lp_build=None):
        timestamp = time.strftime("%F %T")
        logger.info("%s on %s finished at %s (%s)" % (
            full_name, machine, timestamp, text_status))
        tracker_set_rebuild_status(config, [0, 1, 2], 3, arch)
        if status == 0:
            successful.add(arch)
            if arch == "amd64" and "amd64+mac" in config.arches:
                successful.add("amd64+mac")
        else:
            live_build_notify_failure(config, arch, lp_build=lp_build)

    while builds or lp_builds:
        # Check for non-Launchpad build results.
        if builds:
            pid, status = os.waitpid(0, os.WNOHANG)
            if pid and pid in builds:
                _, arch, full_name, machine = builds.pop(pid)
                live_build_finished(
                    arch, full_name, machine, status,
                    "success" if status == 0 else "failed")

        # Check for Launchpad build results.
        pending_lp_builds = []
        for lp_item in lp_builds:
            lp_build, arch, full_name, machine, log_timeout = lp_item
            lp_build.lp_refresh()
            if lp_build.buildstate in (
                    "Needs building", "Currently building", "Uploading build"):
                pending_lp_builds.append(lp_item)
            elif lp_build.buildstate == "Successfully built":
                live_build_finished(
                    arch, full_name, machine, 0, lp_build.buildstate,
                    lp_build=lp_build)
            elif (lp_build.build_log_url is None and
                  (log_timeout is None or time.time() < log_timeout)):
                # Wait up to five minutes for Launchpad to fetch the build
                # log from the slave.  We need a timeout since in rare cases
                # this might fail.
                if log_timeout is None:
                    log_timeout = time.time() + 300
                pending_lp_builds.append(
                    (lp_build, arch, full_name, machine, log_timeout))
            else:
                live_build_finished(
                    arch, full_name, machine, 1, lp_build.buildstate,
                    lp_build=lp_build)
        lp_builds = pending_lp_builds

        if lp_builds:
            # Wait a while before polling Launchpad again.  If a
            # non-Launchpad build completes in the meantime, it will
            # interrupt this sleep with SIGCHLD.
            time.sleep(15)

    if not successful:
        raise LiveBuildsFailed("No live filesystem builds succeeded.")
    return successful


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
        if project == "ubuntustudio":
            return ["lowlatency"]
        else:
            return ["generic"]
    elif cpuarch == "arm64":
        return ["generic"]
    elif cpuarch == "armel":
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
        if series <= "precise":
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
        return ["ia64"]
    elif cpuarch == "powerpc":
        if series <= "xenial":
            return ["powerpc-smp", "powerpc64-smp"]
        else:
            return ["powerpc-smp", "generic"]
    elif cpuarch == "ppc64el":
        return ["generic"]
    elif cpuarch == "s390x":
        return ["generic"]
    elif cpuarch == "sparc":
        return ["sparc64"]
    else:
        raise UnknownArchitecture(
            "No live filesystem source known for %s" % arch)


def live_item_paths(config, arch, item):
    if item == "ltsp-squashfs" and arch == "amd64":
        # use i386 LTSP image on amd64 too
        arch = "i386"
    cpuarch, subarch = split_arch(arch)
    project = config.project
    series = config["DIST"]
    liveproject = live_project(config, arch)
    if subarch:
        liveproject_subarch = "%s-%s" % (liveproject, subarch)
    else:
        liveproject_subarch = liveproject

    lp, lp_livefs = get_lp_livefs(config, arch)
    if lp_livefs is not None:
        lp_kwargs = live_build_lp_kwargs(config, lp, lp_livefs, arch)
        lp_build = lp_livefs.getLatestBuild(
            lp_kwargs["distro_arch_series"],
            unique_key=lp_kwargs.get("unique_key"))
        lp_urls = list(lp_build.getFileUrls())

        def urls_for(base):
            for url in lp_urls:
                if unquote(os.path.basename(url)) == base:
                    yield url
    else:
        root = livecd_base(config, arch)

        def urls_for(base):
            yield "%s/%s" % (root, base)

    if item in (
        "cloop", "squashfs", "manifest", "manifest-desktop", "manifest-remove",
        "manifest-minimal-remove", "size", "ext2", "ext3", "ext4",
        "rootfs.tar.gz", "custom.tar.gz", "device.tar.gz",
        "azure.device.tar.gz", "raspi2.device.tar.gz", "plano.device.tar.gz",
        "tar.xz", "iso", "os.snap", "kernel.snap", "disk1.img.xz",
        "dragonboard.kernel.snap", "raspi2.kernel.snap", "installer.squashfs",
        "maas-rack.squashfs", "maas-region.squashfs",
        "img.xz", "model-assertion"
    ):
        if item == "ext4" and arch == "armhf+nexus7":
            for url in urls_for(
                    "livecd.%s.%s-nexus7" % (liveproject_subarch, item)):
                yield url
        elif item == "disk1.img.xz":
            for url in urls_for(
                    "livecd.%s.%s" % (liveproject, item)):
                yield url
        else:
            for url in urls_for("livecd.%s.%s" % (liveproject_subarch, item)):
                yield url
    elif item in (
        "kernel", "initrd", "bootimg", "modules.squashfs"
    ):
        our_flavours = flavours(config, arch)
        our_flavours.extend(["%s-hwe" % (f,) for f in our_flavours])
        for flavour in our_flavours:
            base = "livecd.%s.%s-%s" % (liveproject_subarch, item, flavour)
            for url in urls_for(base):
                yield url
    elif item in (
        "boot-%s+%s.img" % (target.ubuntu_arch, target.subarch)
            for target in Touch.list_targets_by_ubuntu_arch(arch)
    ) or item in (
        "recovery-%s+%s.img" % (target.android_arch, target.subarch)
            for target in Touch.list_targets_by_ubuntu_arch(arch)
    ) or item in (
        "system-%s+%s.img" % (target.android_arch, target.subarch)
            for target in Touch.list_targets_by_ubuntu_arch(arch)
    ):
        for flavour in flavours(config, arch):
            base = "livecd.%s.%s" % (liveproject_subarch, item)
            for url in urls_for(base):
                yield url
    elif item == "kernel-efi-signed":
        if series >= "precise" and arch == "amd64":
            for flavour in flavours(config, arch):
                base = "livecd.%s.kernel-%s.efi.signed" % (
                    liveproject_subarch, flavour)
                for url in urls_for(base):
                    yield url
    elif item == "wubi":
        if (project != "xubuntu" and arch in ("amd64", "i386")):
            yield ("http://people.canonical.com/~ubuntu-archive/wubi/%s/"
                   "stable" % series)
    elif item == "usb-creator":
        if arch in ("amd64", "i386"):
            yield ("http://people.canonical.com/~evand/usb-creator/%s/"
                   "stable" % series)
    elif item == "ltsp-squashfs":
        if arch in ("amd64", "i386"):
            for url in urls_for("livecd.%s-ltsp.squashfs" % liveproject):
                yield url
    else:
        raise UnknownLiveItem("Unknown live filesystem item '%s'" % item)


def live_output_directory(config):
    project = config.project
    if config["UBUNTU_DEFAULTS_LOCALE"]:
        project = "-".join([project, config["UBUNTU_DEFAULTS_LOCALE"]])
    return os.path.join(
        config.root, "scratch", project, config.full_series, config.image_type,
        "live")


def download_live_items(config, arch, item):
    output_dir = live_output_directory(config)
    found = False

    if item == "server-squashfs":
        original_project = config.project
        try:
            config["PROJECT"] = "ubuntu-server"
            urls = list(live_item_paths(config, arch, "squashfs"))
        finally:
            config["PROJECT"] = original_project
    else:
        urls = list(live_item_paths(config, arch, item))
    if not urls:
        return False

    if item in (
        "kernel", "initrd", "bootimg"
    ):
        for url in urls:
            flavour = re.sub(
                r"^.*?\..*?\..*?-", "", unquote(os.path.basename(url)))
            target = os.path.join(
                output_dir, "%s.%s-%s" % (arch, item, flavour))
            try:
                osextras.fetch(config, url, target)
                found = True
            except osextras.FetchError:
                pass
    elif item in (
        "boot-%s+%s.img" % (target.ubuntu_arch, target.subarch)
            for target in Touch.list_targets_by_ubuntu_arch(arch)
    ):
        for url in urls:
            target = os.path.join(output_dir, item)
            try:
                osextras.fetch(config, url, target)
                found = True
            except osextras.FetchError:
                pass
    elif item in (
        "modules.squashfs",
    ):
        for url in urls:
            base = unquote(os.path.basename(url))
            base = "%s.%s" % (arch, base.split('.', 2)[2])
            target = os.path.join(output_dir, base)
            try:
                osextras.fetch(config, url, target)
                found = True
            except osextras.FetchError:
                pass
    elif item in (
        "recovery-%s+%s.img" % (target.android_arch, target.subarch)
            for target in Touch.list_targets_by_ubuntu_arch(arch)
    ):
        for url in urls:
            target = os.path.join(output_dir, item)
            try:
                osextras.fetch(config, url, target)
                found = True
            except osextras.FetchError:
                pass
    elif item in (
        "system-%s+%s.img" % (target.android_arch, target.subarch)
            for target in Touch.list_targets_by_ubuntu_arch(arch)
    ):
        for url in urls:
            target = os.path.join(output_dir, item)
            try:
                osextras.fetch(config, url, target)
                found = True
            except osextras.FetchError:
                pass
    elif item == "kernel-efi-signed":
        for url in urls:
            base = unquote(os.path.basename(url))
            if base.endswith(".efi.signed"):
                base = base[:-len(".efi.signed")]
            flavour = re.sub(r"^.*?\..*?\..*?-", "", base)
            target = os.path.join(
                output_dir, "%s.kernel-%s.efi.signed" % (arch, flavour))
            try:
                osextras.fetch(config, url, target)
                found = True
            except osextras.FetchError:
                pass
    elif item in ("wubi", "usb-creator"):
        target = os.path.join(output_dir, "%s.%s.exe" % (arch, item))
        try:
            osextras.fetch(config, urls[0], target)
            found = True
        except osextras.FetchError:
            pass
    else:
        target = os.path.join(output_dir, "%s.%s" % (arch, item))
        try:
            osextras.fetch(config, urls[0], target)
            if item in ["squashfs", "server-squashfs"]:
                sign.sign_cdimage(config, target)
            found = True
        except osextras.FetchError:
            pass
    return found


def write_autorun(config, arch, name, label):
    output_dir = live_output_directory(config)
    autorun_path = os.path.join(output_dir, "%s.autorun.inf" % arch)
    with io.open(autorun_path, "w", newline="\r\n") as autorun:
        if str is bytes:
            def u(s):
                return unicode(s, "unicode_escape")
        else:
            def u(s):
                return s
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
                if project == "ubuntu-server":
                    if download_live_items(config, arch, "disk1.img.xz"):
                        got_image = True
                    else:
                        continue
                elif download_live_items(config, arch, "ext4"):
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
            elif download_live_items(config, arch, "img.xz"):
                got_image = True
            elif download_live_items(config, arch, "cloop"):
                got_image = True
            elif download_live_items(config, arch, "squashfs"):
                download_live_items(config, arch, "installer.squashfs")
                download_live_items(config, arch, "maas-rack.squashfs")
                download_live_items(config, arch, "maas-region.squashfs")
                download_live_items(config, arch, "modules.squashfs")
                got_image = True
            elif download_live_items(config, arch, "rootfs.tar.gz"):
                got_image = True
            elif download_live_items(config, arch, "tar.xz"):
                got_image = True
            else:
                continue
            if (project != "ubuntu-base" and
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
            download_live_items(config, arch, "manifest-minimal-remove")
            download_live_items(config, arch, "size")

            if (config["UBUNTU_DEFAULTS_LOCALE"] or
                    config["CDIMAGE_PREINSTALLED"] or
                    config.subproject == "wubi"):
                continue

            if (project not in ("livecd-base", "ubuntu-base", "ubuntu-core",
                                "kubuntu-active") and
                    (project != "ubuntu-desktop-next" or
                     config.subproject == "system-image") and
                    (project != "edubuntu" or series >= "precise") and
                    (project != "ubuntukylin" or series < "utopic")):
                if series <= "trusty":
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

            if project not in ("livecd-base", "ubuntu-base", "ubuntu-core",
                               "ubuntu-desktop-next", "edubuntu"):
                if (project in ("kubuntu-active", "ubuntu-netbook",
                                "ubuntu-moblin-remix") or
                        config["CDIMAGE_DVD"] or
                        series >= "maverick"):
                    download_live_items(config, arch, "usb-creator")
            if project == "ubuntu-core" and config["CDIMAGE_LIVE"]:
                download_live_items(config, arch, "model-assertion")

        if not got_image:
            raise NoFilesystemImages("No filesystem images found.")

    if config.project in ("ubuntu-touch", "ubuntu-touch-custom"):
        for arch in config.arches:
            for abootimg in (
                "boot-%s+%s.img" % (target.ubuntu_arch, target.subarch)
                    for target in Touch.list_targets_by_ubuntu_arch(arch)
            ):
                download_live_items(
                    config, arch, abootimg
                )
            for recoveryimg in (
                "recovery-%s+%s.img" % (target.android_arch, target.subarch)
                    for target in Touch.list_targets_by_ubuntu_arch(arch)
            ):
                download_live_items(
                    config, arch, recoveryimg
                )
            for systemimg in (
                "system-%s+%s.img" % (target.android_arch, target.subarch)
                    for target in Touch.list_targets_by_ubuntu_arch(arch)
            ):
                download_live_items(
                    config, arch, systemimg
                )
            download_live_items(config, arch, "custom.tar.gz")

    if config.project in ("ubuntu-core", "ubuntu-desktop-next"):
        for arch in config.arches:
            download_live_items(config, arch, "device.tar.gz")

    if config.project == "ubuntu-core":
        for arch in config.arches:
            download_live_items(config, arch, "os.snap")
            download_live_items(config, arch, "kernel.snap")
            if arch == "amd64":
                for devarch in ("azure", "plano"):
                    download_live_items(config, arch, "%s.device.tar.gz" %
                                        devarch)
            if arch == "armhf":
                download_live_items(config, arch, "raspi2.device.tar.gz")
                download_live_items(config, arch, "raspi2.kernel.snap")
            if arch == "arm64":
                download_live_items(config, arch, "dragonboard.kernel.snap")

    if project == "edubuntu" and config["CDIMAGE_DVD"]:
        for arch in config.arches:
            if arch in ("amd64", "i386"):
                # TODO: Disabled for raring (LP: #1154601)
                # if series >= "raring":
                #     # Fetch the Ubuntu Server squashfs for Edubuntu Server.
                #     download_live_items(config, arch, "server-squashfs")

                # Fetch the i386 LTSP chroot for Edubuntu Terminal Server.
                download_live_items(config, arch, "ltsp-squashfs")
