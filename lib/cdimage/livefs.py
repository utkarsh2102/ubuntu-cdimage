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
import subprocess
import time
try:
    from urllib.error import URLError
    from urllib.parse import unquote
    from urllib.request import urlopen
except ImportError:
    from urllib2 import URLError, unquote, urlopen

from cdimage import osextras, sign
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


def split_arch(config, arch):
    # To make sure we're compatible with everything before, we need to do the
    # arch -> livefs_arch mapping here. This way we're consistent with how it
    # worked previously
    live_arch = config.livefs_arch_for_arch(arch)
    arch_bits = live_arch.split("+", 1)
    if len(arch_bits) == 1:
        arch_bits.append("")
    cpuarch, subarch = arch_bits
    return cpuarch, subarch


def live_builder(config, arch):
    cpuarch, subarch = split_arch(config, arch)
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

    cpuarch, subarch = split_arch(config, arch)
    if (cpuarch in ("armel", "armhf") and
            config.image_type == "daily-preinstalled"):
        if subarch in ("mx5", "omap", "omap4"):
            options.extend(["-f", "ext4"])
        elif subarch in ("ac100", "nexus7"):
            options.extend(["-f", "plain"])

    if config.project in ("ubuntu-base", "ubuntu-core"):
        options.extend(["-f", "plain"])

    if config.subproject == "wubi":
        options.extend(["-f", "ext3"])

    return options


def live_project(config, arch):
    return config.livefs_project_for_arch(arch)


def live_build_command(config, arch):
    command = [
        "ssh", "-n", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
        "buildd@%s" % live_builder(config, arch),
        "/home/buildd/bin/BuildLiveCD",
    ]

    command.append("-l")

    command.extend(live_build_options(config, arch))

    cpuarch, subarch = split_arch(config, arch)
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
    cpuarch, subarch = split_arch(config, arch)
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

    if config.get("CHANNEL"):
        try:
            kwargs["unique_key"] += "_" + config["CHANNEL"]
        except KeyError:
            kwargs["unique_key"] = config["CHANNEL"]

        metadata_override["channel"] = config["CHANNEL"]

    if config["CDIMAGE_DATE"]:
        kwargs["version"] = config["CDIMAGE_DATE"]

    if metadata_override:
        kwargs["metadata_override"] = metadata_override

    return kwargs


# TODO: This is only used for logging, so it might be worth unifying with
# live_build_notify_failure.
def live_build_full_name(config, arch):
    bits = [config.project]
    if config.subproject:
        bits.append(config.subproject)
    cpuarch, subarch = split_arch(config, arch)
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
    cpuarch, subarch = split_arch(config, arch)
    if subarch:
        livefs_id_bits.append(subarch)
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
    cpuarch, subarch = split_arch(config, arch)
    want_project_bits = [config.project]
    if config.subproject:
        want_project_bits.append(config.subproject)
    want_project = "-".join(want_project_bits)
    image_type = config.image_type

    # TODO: we should probably deprecate this in favor of
    #  etc/cdimage-to-livecd-rootfs-map
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
            lp_build = None
            if config["CDIMAGE_REUSE_BUILD"]:
                cpuarch, subarch = split_arch(config, arch)
                for build in lp_livefs.builds:
                    try:
                        metadata_subarch = build.metadata_override.get(
                            "subarch", "")
                    except AttributeError:
                        metadata_subarch = ""
                    if (build.distro_arch_series.architecture_tag == cpuarch
                            and metadata_subarch == subarch
                            and build.buildstate == "Successfully built"):
                        logger.info("reusing build %s", build)
                        lp_build = build
                        break
                else:
                    raise Exception("no build found to reuse for %s" % (arch,))
            if lp_build is None:
                lp_kwargs = live_build_lp_kwargs(config, lp, lp_livefs, arch)
                lp_build = lp_livefs.requestBuild(**lp_kwargs)
                logger.info("%s: %s" % (full_name, lp_build.web_link))
            lp_builds.append((lp_build, arch, full_name, machine, None))
        else:
            proc = subprocess.Popen(live_build_command(config, arch))
            builds[proc.pid] = (proc, arch, full_name, machine)

    successful_builds = {}

    def live_build_finished(arch, full_name, machine, status, text_status,
                            lp_build=None):
        timestamp = time.strftime("%F %T")
        logger.info("%s on %s finished at %s (%s)" % (
            full_name, machine, timestamp, text_status))
        if status == 0:
            tracker_set_rebuild_status(config, [0, 1, 2], 3, arch)
            successful_builds[arch] = lp_build
        else:
            tracker_set_rebuild_status(config, [0, 1, 2], 5, arch)
            live_build_notify_failure(config, arch, lp_build=lp_build)

    while builds or lp_builds:
        # Check for non-Launchpad build results.
        for pid, (proc, arch, full_name, machine) in list(builds.items()):
            status = proc.poll()
            if status is not None:
                del builds[pid]
                live_build_finished(
                    arch, full_name, machine, status,
                    "success" if status == 0 else "failed")

        # Check for Launchpad build results.
        pending_lp_builds = []
        for lp_item in lp_builds:
            lp_build, arch, full_name, machine, log_timeout = lp_item
            lp_build.lp_refresh()
            if lp_build.buildstate in (
                    "Needs building", "Currently building",
                    "Gathering build output", "Uploading build"):
                pending_lp_builds.append(lp_item)
            elif lp_build.buildstate == "Successfully built":
                live_build_finished(
                    arch, full_name, machine, 0, lp_build.buildstate,
                    lp_build=lp_build)
            elif (lp_build.build_log_url is None and
                  (log_timeout is None or time.time() < log_timeout)):
                # Wait up to five minutes for Launchpad to fetch the build
                # log from the remote.  We need a timeout since in rare cases
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

    if not successful_builds:
        raise LiveBuildsFailed("No live filesystem builds succeeded.")
    return successful_builds


def live_output_directory(config):
    return os.path.join(
        config.root, "scratch", config.subtree, config.project,
        config.full_series, config.image_type, "live")


def live_build_notify_download_failure(config, arch, exc):
    if config["DEBUG"]:
        return

    project = config.project
    recipients = get_notify_addresses(config, project)
    if not recipients:
        return

    livefs_id_bits = [project]
    if config.subproject:
        livefs_id_bits.append(config.subproject)
    cpuarch, subarch = split_arch(config, arch)
    if subarch:
        livefs_id_bits.append(subarch)
    livefs_id = "-".join(livefs_id_bits)

    datestamp = time.strftime("%Y%m%d")
    body = f"""
An artefact failed to download with error:

{exc}
"""
    subject = "LiveFS %s%s/%s/%s failed to download on %s" % (
        "(built by %s) " % config["SUDO_USER"] if config["SUDO_USER"] else "",
        livefs_id, config.full_series, arch, datestamp)
    send_mail(subject, "download_live_filesystems", recipients, body)


def download_livefs_artifacts(config, arch, lp_build, output_dir):
    for uri in lp_build.getFileUrls():
        base = unquote(os.path.basename(uri))
        base = base.split('.', 2)[2]
        ext = base.split('.')[-1]
        if ext in ('full', 'filelist', 'ext2', 'ext3', 'ext4'):
            continue
        if config.project == "ubuntu-mini-iso" and base == "rootfs.tar.gz":
            continue
        target = os.path.join(output_dir, arch + '.' + base)
        osextras.fetch(config, uri, target)
        if target.endswith("squashfs"):
            sign.sign_cdimage(config, target)


def download_live_filesystems(config, builds):
    output_dir = live_output_directory(config)
    osextras.mkemptydir(output_dir)

    if config["CDIMAGE_LOCAL_LIVEFS_ARTIFACTS"]:
        assert len(config.arches) == 1
        arch = config.arches[0]
        if "+" in arch:
            pname = f'{config["PROJECT"]}-{arch.split("+")[1]}'
        else:
            pname = config["PROJECT"]
        artifacts_dir = config["CDIMAGE_LOCAL_LIVEFS_ARTIFACTS"]
        for srcname in os.listdir(artifacts_dir):
            destname = srcname.replace(f"livecd.{pname}.", f"{arch}.")
            srcpath = os.path.join(artifacts_dir, srcname)
            destpath = os.path.join(output_dir, destname)
            logger.info("linking %r to %r", srcpath, destpath)
            os.link(srcpath, destpath)
        return {arch: None}

    successful_builds = {}
    for arch, build in builds.items():
        try:
            download_livefs_artifacts(config, arch, build, output_dir)
        except osextras.FetchError as exc:
            live_build_notify_download_failure(config, arch, exc)
            continue
        successful_builds[arch] = build
    return successful_builds
