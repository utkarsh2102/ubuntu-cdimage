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
    live_output_directory,
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


def _dpkg_field(path, field):
    return subprocess.check_output(
        ["dpkg", "-f", path, field], universal_newlines=True).rstrip("\n")


def _find_endswith(path, suffixes):
    for dirpath, _, filenames in os.walk(path):
        for filename in filenames:
            for suffix in suffixes:
                if filename.endswith(suffix):
                    yield dirpath, filename
                    break


def build_britney(config):
    update_out = os.path.join(config.root, "britney", "update_out")
    if os.path.isfile(os.path.join(update_out, "Makefile")):
        log_marker("Building britney")
        subprocess.check_call(["make", "-C", update_out])


class UnknownLocale(Exception):
    pass


def build_ubuntu_defaults_locale(config, builds):
    locale = config["UBUNTU_DEFAULTS_LOCALE"]
    if locale != "zh_CN":
        raise UnknownLocale(
            "UBUNTU_DEFAULTS_LOCALE='%s' not currently supported!" % locale)

    series = config["DIST"]
    log_marker("Downloading live filesystem images")
    download_live_filesystems(config, builds)
    scratch = live_output_directory(config)
    for entry in os.listdir(scratch):
        if "." in entry:
            os.rename(
                os.path.join(scratch, entry),
                os.path.join(scratch, "%s-desktop-%s" % (series, entry)))
    pi_makelist = os.path.join(
        config.root, "debian-cd", "tools", "pi-makelist")
    for entry in os.listdir(scratch):
        if entry.endswith(".iso"):
            entry_path = os.path.join(scratch, entry)
            list_path = "%s.list" % entry_path.rsplit(".", 1)[0]
            with open(list_path, "w") as list_file:
                subprocess.check_call(
                    [pi_makelist, entry_path], stdout=list_file)


def build_livecd_base(config, builds):
    log_marker("Downloading live filesystem images")
    download_live_filesystems(config, builds)

    if config.image_type == "daily-preinstalled":
        if config.project == 'ubuntu-server':
            image_type = 'server'
        else:
            image_type = 'desktop'
        log_marker("Copying images to debian-cd output directory")
        scratch_dir = os.path.join(
            config.root, "scratch", config.subtree, config.project,
            config.full_series, config.image_type)
        live_dir = os.path.join(scratch_dir, "live")
        for arch in config.arches:
            output_dir = os.path.join(scratch_dir, "debian-cd", arch)
            osextras.ensuredir(output_dir)
            live_prefix = os.path.join(live_dir, arch)
            rootfs = "%s.img.xz" % (live_prefix)
            # Previously for server images we expected a .disk1.img.xz
            # artifact, so still support it before we migrate all the
            # images to the new format.
            if not os.path.exists(rootfs):
                rootfs = "%s.disk1.img.xz" % (live_prefix)
            output_prefix = os.path.join(output_dir,
                                         "%s-preinstalled-%s-%s" %
                                         (config.series, image_type, arch))
            with open("%s.type" % output_prefix, "w") as f:
                print("EXT4 Filesystem Image", file=f)
            shutil.copy2(rootfs, "%s.raw" % output_prefix)
            shutil.copy2(
                "%s.manifest" % live_prefix, "%s.manifest" % output_prefix)

    if (config.project in ("ubuntu-mini-iso", ) and
            config.image_type == "daily-live"):
        log_marker("Copying mini iso to debian-cd output directory")
        scratch_dir = os.path.join(
            config.root, "scratch", config.subtree, config.project,
            config.full_series, config.image_type)
        live_dir = os.path.join(scratch_dir, "live")
        for arch in config.arches:
            output_dir = os.path.join(scratch_dir, "debian-cd", arch)
            osextras.ensuredir(output_dir)
            live_prefix = os.path.join(live_dir, arch)
            iso = "%s.iso" % (live_prefix)
            output_prefix = os.path.join(output_dir,
                                         "%s-mini-iso-%s" %
                                         (config.series, arch))
            with open("%s.type" % output_prefix, "w") as f:
                print("ISO 9660 CD-ROM filesystem data", file=f)
            shutil.copy2(iso, "%s.raw" % output_prefix)
            # XXX: I don't think we need the manifest for a mini iso
            # shutil.copy2(
            #    "%s.manifest" % live_prefix, "%s.manifest" % output_prefix)

    if (config.project in ("ubuntu-core", "ubuntu-appliance") and
            config.image_type == "daily-live"):
        log_marker("Copying images to debian-cd output directory")
        scratch_dir = os.path.join(
            config.root, "scratch", config.subtree, config.project,
            config.full_series, config.image_type)
        publish_type = "live-core"
        live_dir = os.path.join(scratch_dir, "live")
        for arch in config.arches:
            output_dir = os.path.join(scratch_dir, "debian-cd", arch)
            osextras.ensuredir(output_dir)
            live_prefix = os.path.join(live_dir, arch)
            rootfs = "%s.img.xz" % (live_prefix)
            output_prefix = os.path.join(output_dir,
                                         "%s-%s-%s" %
                                         (config.series, publish_type, arch))
            with open("%s.type" % output_prefix, "w") as f:
                print("Disk Image", file=f)
            shutil.copy2(rootfs, "%s.raw" % output_prefix)
            shutil.copy2(
                "%s.manifest" % live_prefix, "%s.manifest" % output_prefix)
            shutil.copy2(
                "%s.model-assertion" % live_prefix,
                "%s.model-assertion" % output_prefix)
            # qcow2 images for appliances are optional
            live_qcow2 = "%s.qcow2" % live_prefix
            if os.path.exists(live_qcow2):
                shutil.copy2(
                    live_qcow2, "%s.qcow2" % output_prefix)

    if (config.project == "ubuntu-base" or
        (config.project == "ubuntu-core" and
         config.subproject == "system-image")):
        log_marker("Copying images to debian-cd output directory")
        scratch_dir = os.path.join(
            config.root, "scratch", config.subtree, config.project,
            config.full_series, config.image_type)
        live_dir = os.path.join(scratch_dir, "live")
        for arch in config.arches:
            live_prefix = os.path.join(live_dir, arch)
            rootfs = "%s.rootfs.tar.gz" % live_prefix
            if os.path.exists(rootfs):
                output_dir = os.path.join(scratch_dir, "debian-cd", arch)
                osextras.ensuredir(output_dir)
                if config.project == "ubuntu-core":
                    output_prefix = os.path.join(
                        output_dir,
                        "%s-preinstalled-core-%s" % (config.series, arch))
                elif config.project == "ubuntu-base":
                    output_prefix = os.path.join(
                        output_dir, "%s-base-%s" % (config.series, arch))
                shutil.copy2(rootfs, "%s.raw" % output_prefix)
                with open("%s.type" % output_prefix, "w") as f:
                    print("tar archive", file=f)
                shutil.copy2(
                    "%s.manifest" % live_prefix, "%s.manifest" % output_prefix)
                if config.project == "ubuntu-core":
                    for dev in ("azure.device", "device", "raspi2.device",
                                "plano.device"):
                        device = "%s.%s.tar.gz" % (live_prefix, dev)
                        if os.path.exists(device):
                            shutil.copy2(
                                device, "%s.%s.tar.gz" % (output_prefix, dev))
                    for snaptype in ("os", "kernel", "raspi2.kernel",
                                     "dragonboard.kernel"):
                        snap = "%s.%s.snap" % (live_prefix, snaptype)
                        if os.path.exists(snap):
                            shutil.copy2(
                                snap, "%s.%s.snap" % (output_prefix, snaptype))


def copy_netboot_tarballs(config):
    # cp $scratch/$arch.netboot.tar.gz $output/$series-netboot-$arch.tar.gz
    scratch_dir = os.path.join(
        config.root, "scratch", config.subtree, config.project,
        config.full_series, config.image_type)
    for arch in config.arches:
        netboot_path = os.path.join(
            live_output_directory(config),
            '%s.netboot.tar.gz' % (arch,))
        if os.path.exists(netboot_path):
            output_dir = os.path.join(scratch_dir, "debian-cd", arch)
            shutil.copy2(
                netboot_path, os.path.join(
                    output_dir,
                    '%s-netboot-%s.tar.gz' % (config.series, arch)))


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
            live_successful, builds = run_live_builds(config)
            config.limit_arches(live_successful)
        else:
            tracker_set_rebuild_status(config, [0, 1], 2)

        if not is_live_fs_only(config):
            build_britney(config)

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
                # Cannot use apt_state_mgr for germination until
                # https://code.launchpad.net/~mwhudson/germinate/+git/
                #     germinate-1/+merge/456723
                # is merged.
                germination = Germination(config)
                germination.run()

                log_marker("Generating new task lists")
                germinate_output = germination.output()
                germinate_output.write_tasks()

                log_marker("Checking for other task changes")
                germinate_output.update_tasks(date)

            if config["CDIMAGE_LIVE"]:
                log_marker("Downloading live filesystem images")
                download_live_filesystems(config, builds)

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
