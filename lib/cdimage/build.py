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

"""Image building."""

from __future__ import print_function

import contextlib
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
import traceback

from cdimage import osextras
from cdimage.build_id import next_build_id
from cdimage.germinate import Germination
from cdimage.livefs import (
    LiveBuildsFailed,
    download_live_filesystems,
    run_live_builds,
)
from cdimage.log import logger, reset_logging
from cdimage.mail import get_notify_addresses, send_mail
from cdimage.mirror import AptStateManager, trigger_mirrors
from cdimage.tracker import tracker_set_rebuild_status
from cdimage.tree import Publisher, Tree

__metaclass__ = type


@contextlib.contextmanager
def lock_build_image_set(config):
    if config.distribution == "ubuntu":
        full_series = config.series
    else:
        full_series = "%s-%s" % (config.distribution, config.series)
    project = config.project
    if config.subtree:
        project = "%s-%s" % (config.subtree.replace("/", "-"), project)
    lock_path = os.path.join(
        config.root, "etc",
        ".lock-build-image-set-%s-%s-%s" % (
            project, full_series, config.image_type))
    try:
        subprocess.check_call(["lockfile", "-l", "7200", "-r", "0", lock_path])
    except subprocess.CalledProcessError:
        logger.error("Another image set is already building!")
        raise
    try:
        yield
    finally:
        osextras.unlink_force(lock_path)


def configure_for_project(config):
    project = config.project
    if project in (
        "edubuntu",
        "kubuntu",
        "ubuntustudio",
        "lubuntu",
        "ubuntukylin",
        "ubuntu-gnome",
        "ubuntu-budgie",
        "ubuntu-mate",
        "ubuntu-unity",
        "ubuntucinnamon",
        "xubuntu",
    ):
        config["CDIMAGE_UNSUPPORTED"] = "1"


def open_log(config):
    if config["DEBUG"] or config["CDIMAGE_NOLOG"]:
        return None

    log_path = os.path.join(
        config.root, "log", config.subtree, config.project, config.full_series,
        "%s-%s.log" % (config.image_type, config["CDIMAGE_DATE"]))
    osextras.ensuredir(os.path.dirname(log_path))
    log = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
    os.dup2(log, 1)
    os.dup2(log, 2)
    os.close(log)
    sys.stdout = os.fdopen(1, "w", 1)
    sys.stderr = os.fdopen(2, "w", 1)
    reset_logging()
    # Since we now know we aren't going to be spamming the terminal, it's
    # safe to crank up debian-cd's verbosity so that the logs are most
    # useful.
    config["VERBOSE"] = "3"
    return log_path


def log_marker(message):
    logger.info("===== %s =====" % message)
    logger.info(time.strftime("%a %b %e %H:%M:%S UTC %Y", time.gmtime()))


def want_live_builds(options):
    return options is not None and getattr(options, "live", False)


def copy_artifact(
        config,
        arch: str,
        publish_type: str,
        suffix: str,
        *,
        target_suffix: None | str = None,
        ftype: None | str = None,
        missing_ok: bool = False,
        ) -> bool:
    """Copy an artifact from the "live" directory to the "output" directory.

    The artifact is expected to be named "{arch}.{suffix}" and the file
    will be named "{series}-{publish_type}-{arch}.{target_suffix}" in
    the output directory.

    Returns True if a file was copied, False if the source was not found
    (and missing_ok is true) and raises FileNotFoundError if the source
    was not found (and missing_ok is false).
    """
    if target_suffix is None:
        target_suffix = suffix
    scratch_dir = os.path.join(
        config.root, "scratch", config.subtree, config.project,
        config.full_series, config.image_type)
    output_dir = os.path.join(scratch_dir, "debian-cd", arch)
    output_basename = f"{config.series}-{publish_type}-{arch}"
    src = os.path.join(scratch_dir, "live", f"{arch}.{suffix}")
    if os.path.exists(src):
        osextras.ensuredir(output_dir)
        shutil.copy2(
            src,
            os.path.join(output_dir, f"{output_basename}.{target_suffix}"))
    elif not missing_ok:
        raise FileNotFoundError(src)
    else:
        return False
    if ftype is not None:
        ftype_path = os.path.join(output_dir, f"{output_basename}.type")
        with open(ftype_path, "w") as f:
            print(ftype, file=f)
    return True


def copy_artifact(
        config,
        arch: str,
        publish_type: str,
        suffix: str,
        *,
        target_suffix: None | str = None,
        ftype: None | str = None,
        missing_ok: bool = False,
        ) -> bool:
    """Copy an artifact from the "live" directory to the "output" directory.

    The artifact is expected to be named "{arch}.{suffix}" and the file
    will be named "{series}-{publish_type}-{arch}.{target_suffix}" in
    the output directory.

    Returns True if a file was copied, False if the source was not found
    (and missing_ok is true) and raises FileNotFoundError if the source
    was not found (and missing_ok is false).
    """
    if target_suffix is None:
        target_suffix = suffix
    scratch_dir = os.path.join(
        config.root, "scratch", config.subtree, config.project,
        config.full_series, config.image_type)
    output_dir = os.path.join(scratch_dir, "debian-cd", arch)
    output_basename = f"{config.series}-{publish_type}-{arch}"
    src = os.path.join(scratch_dir, "live", f"{arch}.{suffix}")
    if os.path.exists(src):
        osextras.ensuredir(output_dir)
        shutil.copy2(
            src,
            os.path.join(output_dir, f"{output_basename}.{target_suffix}"))
    elif not missing_ok:
        raise FileNotFoundError(src)
    else:
        return False
    if ftype is not None:
        ftype_path = os.path.join(output_dir, f"{output_basename}.type")
        with open(ftype_path, "w") as f:
            print(ftype, file=f)
    return True


def build_livecd_base(config, builds):
    """Copy an artifacts from the "live" directory to the "output" directory.

    For installers, we run debian-cd which (amongst other things) takes
    the livefs artifacts from the "live" directory and bundles them up
    into an ISO in the "output" directory, from where the publication
    code in tree.py copies them to the directory tree that will be
    synced to cdimage.ubuntu.com and/or releases.ubuntu.com.

    For non-installer images (which is what this function is handling),
    we just copy the livefs artifacts from directly to the output
    directory, with various silly contortions to fit into the naming
    conventions both for where the livefs artifacts get downloaded to
    and where the publication code expects to find things.
    """
    log_marker("Downloading live filesystem images")
    builds = download_live_filesystems(config, builds)
    config.limit_arches_for_builds(builds)

    if config.image_type == "daily-preinstalled":
        if config.project == 'ubuntu-server':
            publish_type = 'preinstalled-server'
        else:
            publish_type = 'preinstalled-desktop'
        log_marker("Copying images to debian-cd output directory")
        for arch in config.arches:
            for suffix in "img.xz", "disk1.img.xz":
                if copy_artifact(
                        config, arch, publish_type, suffix,
                        target_suffix="raw",
                        ftype="EXT4 Filesystem Image",
                        missing_ok=True):
                    break
            else:
                raise Exception("no rootfs found")
            copy_artifact(config, arch, publish_type, "manifest")

    if (config.project == "ubuntu-mini-iso" and
            config.image_type == "daily-live"):
        log_marker("Copying mini iso to debian-cd output directory")
        publish_type = "mini-iso"
        for arch in config.arches:
            copy_artifact(
                config, arch, publish_type, "iso",
                target_suffix="raw",
                ftype="ISO 9660 CD-ROM filesystem data")
            # XXX: I don't think we need the manifest for a mini iso
            # copy_artifact(arch, "mini-iso", "manifest")

    if (config.project in ("ubuntu-core", "ubuntu-appliance") and
            config.image_type == "daily-live"):
        log_marker("Copying images to debian-cd output directory")
        publish_type = "live-core"
        for arch in config.arches:
            copy_artifact(
                config, arch, publish_type, "img.xz",
                target_suffix="raw",
                ftype="Disk Image")
            copy_artifact(config, arch, publish_type, "manifest")
            copy_artifact(config, arch, publish_type, "model-assertion")
            copy_artifact(
                config, arch, publish_type, "qcow2", missing_ok=True)

    if (config.project == "ubuntu-base" or
        (config.project == "ubuntu-core" and
         config.subproject == "system-image")):
        log_marker("Copying images to debian-cd output directory")
        if config.project == "ubuntu-core":
            publish_type = "preinstalled-core"
        elif config.project == "ubuntu-base":
            publish_type = "base"
        for arch in config.arches:
            found = copy_artifact(
                config, arch, publish_type, "rootfs.tar.gz",
                target_suffix="raw",
                ftype="tar archive",
                missing_ok=True)
            if not found:
                continue
            copy_artifact(config, arch, publish_type, "manifest")
            if config.project != "ubuntu-core":
                continue
            for dev in ("azure.device", "device", "raspi2.device",
                        "plano.device"):
                copy_artifact(
                    config, arch, publish_type, "%s.tar.gz" % (dev,),
                    missing_ok=True)
            for snaptype in ("os", "kernel", "raspi2.kernel",
                             "dragonboard.kernel"):
                copy_artifact(
                    config, arch, publish_type, "%s.snap" % (snaptype,),
                    missing_ok=True)


def copy_netboot_tarballs(config):
    for arch in config.arches:
        copy_artifact(
            config, arch, "netboot", "netboot.tar.gz",
            target_suffix="tar.gz", missing_ok=True)


def configure_splash(config):
    project = config.project
    data_dir = os.path.join(config.root, "debian-cd", "data", config.series)
    for key, extension in (
        ("SPLASHRLE", "rle"),
        ("GFXSPLASH", "pcx"),
        ("SPLASHPNG", "png"),
    ):
        project_image = os.path.join(data_dir, "%s.%s" % (project, extension))
        generic_image = os.path.join(data_dir, "splash.%s" % extension)
        if os.path.exists(project_image):
            config[key] = project_image
        else:
            config[key] = generic_image


def run_debian_cd(config, apt_state_mgr):
    log_marker("Building %s daily CDs" % config.capproject)
    debian_cd_dir = os.path.join(config.root, "debian-cd")
    env = config.export()
    for cpuarch in config.cpuarches:
        env["APT_CONFIG_" + cpuarch] = apt_state_mgr.apt_conf_for_arch(cpuarch)
    # For core image builds, for convenience pass the core series to debian-cd
    # (for the image label to be correct)
    if "ubuntu-core" in config.project:
        env["CDIMAGE_CORE_SERIES"] = config.core_series
    subprocess.call(["./build_all.sh"], cwd=debian_cd_dir, env=env)


def fix_permissions(config):
    """Kludge to work around permission-handling problems elsewhere."""
    scratch_dir = os.path.join(
        config.root, "scratch", config.subtree, config.project,
        config.full_series, config.image_type)
    if not os.path.isdir(scratch_dir):
        return

    def fix_directory(path):
        old_mode = os.stat(path).st_mode
        new_mode = old_mode | stat.S_IRGRP | stat.S_IWGRP
        new_mode |= stat.S_ISGID | stat.S_IXGRP
        if new_mode != old_mode:
            try:
                os.chmod(path, new_mode)
            except OSError:
                pass

    def fix_file(path):
        old_mode = os.stat(path).st_mode
        new_mode = old_mode | stat.S_IRGRP | stat.S_IWGRP
        if new_mode & (stat.S_IXUSR | stat.S_IXOTH):
            new_mode |= stat.S_IXGRP
        if new_mode != old_mode:
            try:
                os.chmod(path, new_mode)
            except OSError:
                pass

    fix_directory(scratch_dir)
    for dirpath, dirnames, filenames in os.walk(scratch_dir):
        for dirname in dirnames:
            fix_directory(os.path.join(dirpath, dirname))
        for filename in filenames:
            fix_file(os.path.join(dirpath, filename))


def notify_failure(config, log_path):
    if config["DEBUG"] or config["CDIMAGE_NOLOG"]:
        return

    project = config.project
    series = config.full_series
    image_type = config.image_type
    date = config["CDIMAGE_DATE"]
    subtree = "%s/" % config.subtree if config.subtree else ""

    recipients = get_notify_addresses(config, project)
    if not recipients:
        return

    try:
        if log_path is None:
            body = ""
        else:
            body = open(log_path)
        send_mail(
            "CD image %s%s%s/%s/%s failed to build on %s" % (
                ("(built by %s) " % config["SUDO_USER"]
                 if config["SUDO_USER"] else ""),
                subtree, project, series, image_type, date),
            "build-image-set", recipients, body)
    finally:
        if log_path is not None:
            body.close()


def is_live_fs_only(config):
    live_fs_only = False
    if config.project in (
            "livecd-base", "ubuntu-base", "ubuntu-core",
            "ubuntu-appliance"):
        live_fs_only = True
    elif config.image_type == "daily-preinstalled":
        live_fs_only = True
    elif config.project == "ubuntu-mini-iso":
        live_fs_only = True
    elif config.subproject == "wubi":
        live_fs_only = True
    return live_fs_only


def build_image_set_locked(config, options):
    image_type = config.image_type
    config["CDIMAGE_DATE"] = date = next_build_id(config, image_type)
    log_path = None
    builds = None

    try:
        configure_for_project(config)
        log_path = open_log(config)

        if want_live_builds(options):
            log_marker("Building live filesystems")
            builds = run_live_builds(config)
            config.limit_arches_for_builds(builds)
        else:
            tracker_set_rebuild_status(config, [0, 1], 2)

        if is_live_fs_only(config):
            build_livecd_base(config, builds)
        else:
            assert not config["CDIMAGE_PREINSTALLED"]

            apt_state_mgr = AptStateManager(config)
            apt_state_mgr.setup()

            if config.project in (
                    "ubuntu-core-desktop", "ubuntu-core-installer"):
                config["GENERATE_POOL"] = "0"
            else:
                log_marker("Germinating")
                germination = Germination(config, apt_state_mgr=apt_state_mgr)
                germination.run()

                log_marker("Generating new task lists")
                germinate_output = germination.output()
                germinate_output.write_tasks()

                log_marker("Checking for other task changes")
                germinate_output.update_tasks(date)

            if config["CDIMAGE_LIVE"]:
                log_marker("Downloading live filesystem images")
                builds = download_live_filesystems(config, builds)
                config.limit_arches_for_builds(builds)

            configure_splash(config)

            run_debian_cd(config, apt_state_mgr)
            copy_netboot_tarballs(config)
            fix_permissions(config)

        if not config["DEBUG"] and not config["CDIMAGE_NOPUBLISH"]:
            log_marker("Publishing")
            tree = Tree.get_daily(config)
            publisher = Publisher.get_daily(tree, image_type)
            publisher.publish(date)

            log_marker("Purging old images")
            publisher.purge()

            log_marker("Handling simplestreams")
            publisher.refresh_simplestreams()

            log_marker("Triggering mirrors")
            trigger_mirrors(config)

        log_marker("Finished")
        return True
    except Exception as e:
        for line in traceback.format_exc().splitlines():
            logger.error(line)
        sys.stdout.flush()
        sys.stderr.flush()
        if not isinstance(e, LiveBuildsFailed):
            notify_failure(config, log_path)
        return False


class SignalExit(SystemExit):
    """A variant of SystemExit indicating receipt of a signal."""

    def __init__(self, signum):
        self.signum = signum


@contextlib.contextmanager
def handle_signals():
    """Handle some extra signals that cdimage might receive.

    These need to turn into Python exceptions so that we have an opportunity
    to release locks.
    """
    def handler(signum, frame):
        raise SignalExit(signum)

    old_handlers = []
    for signum in signal.SIGQUIT, signal.SIGTERM:
        old_handlers.append((signum, signal.signal(signum, handler)))
    try:
        try:
            yield
        finally:
            for signum, old_handler in old_handlers:
                signal.signal(signum, old_handler)
    except SignalExit as e:
        # In order to get a correct exit code, resend the signal now that
        # we've removed our handlers.
        os.kill(os.getpid(), e.signum)


def build_image_set(config, options):
    """Master entry point for building images."""
    with handle_signals():
        with lock_build_image_set(config):
            return build_image_set_locked(config, options)
