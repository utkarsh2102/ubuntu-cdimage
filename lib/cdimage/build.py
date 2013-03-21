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

__metaclass__ = type

import contextlib
import gzip
import os
import shutil
import stat
import subprocess
import sys
import time
import traceback

from cdimage import osextras
from cdimage.build_id import next_build_id
from cdimage.check_installable import check_installable
from cdimage.germinate import Germination
from cdimage.livefs import download_live_filesystems, live_output_directory
from cdimage.log import logger, reset_logging
from cdimage.mail import get_notify_addresses, send_mail
from cdimage.mirror import find_mirror, trigger_mirrors
from cdimage.semaphore import Semaphore
from cdimage.tree import Publisher, Tree


@contextlib.contextmanager
def lock_build_image_set(config):
    project = config.project
    if config["UBUNTU_DEFAULTS_LOCALE"] == "zh_CN":
        project = "ubuntu-chinese-edition"
    lock_path = os.path.join(
        config.root, "etc",
        ".lock-build-image-set-%s-%s-%s" % (
            project, config.series, config.image_type))
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
    series = config["DIST"]
    if project == "gobuntu":
        config["CDIMAGE_ONLYFREE"] = "1"
    elif project == "edubuntu":
        if series >= "karmic":
            config["CDIMAGE_UNSUPPORTED"] = "1"
    elif project == "xubuntu":
        if series >= "hardy":
            config["CDIMAGE_UNSUPPORTED"] = "1"
    elif project == "kubuntu":
        if series >= "quantal":
            config["CDIMAGE_UNSUPPORTED"] = "1"
    elif project in (
        "kubuntu-active",
        "ubuntustudio",
        "mythbuntu",
        "lubuntu",
        "ubuntukylin",
        "ubuntu-gnome",
        "ubuntu-moblin-remix",
        "ubuntu-mid",
    ):
        config["CDIMAGE_UNSUPPORTED"] = "1"

    if config["CDIMAGE_INSTALL"]:
        config["CDIMAGE_INSTALL_BASE"] = "1"


def open_log(config):
    if config["DEBUG"]:
        return None

    project = config.project
    if config["UBUNTU_DEFAULTS_LOCALE"] == "zh_CN":
        project = "ubuntu-chinese-edition"
    log_path = os.path.join(
        config.root, "log", project, config.series,
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


def sync_local_mirror(config, semaphore_state):
    if config["CDIMAGE_NOSYNC"]:
        return

    capproject = config.capproject
    sync_lock = os.path.join(config.root, "etc", ".lock-archive-sync")
    if semaphore_state == 0:
        log_marker("Syncing %s mirror" % capproject)
        # Acquire lock to allow parallel builds to ensure a consistent
        # archive.
        try:
            subprocess.check_call(["lockfile", "-r", "4", sync_lock])
        except subprocess.CalledProcessError:
            logger.error("Couldn't acquire archive sync lock!")
            raise
        try:
            subprocess.check_call(["anonftpsync"])
        finally:
            osextras.unlink_force(sync_lock)
    else:
        log_marker(
            "Parallel build; waiting for %s mirror to sync" % capproject)
        try:
            subprocess.check_call(["lockfile", "-8", "-r", "450", sync_lock])
        except subprocess.CalledProcessError:
            logger.error("Timed out waiting for archive sync lock!")
            raise
        osextras.unlink_force(sync_lock)


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


def update_local_indices(config):
    packages = os.path.join(config.root, "local", "packages")
    if not os.path.isdir(packages):
        return

    database = os.path.normpath(os.path.join(packages, os.pardir, "database"))
    dists = os.path.join(database, "dists")
    indices = os.path.join(database, "indices")
    pool = os.path.join(packages, "pool", "local")
    osextras.ensuredir(dists)
    osextras.ensuredir(indices)

    for arch in config.cpuarches:
        binary_list_path = os.path.join(
            dists, "%s_local_binary-%s.list" % (config.series, arch))
        di_binary_list_path = os.path.join(
            dists, "%s_local_debian-installer_binary-%s.list" % (
                config.series, arch))
        override_path = os.path.join(
            indices, "override.%s.local.%s" % (config.series, arch))
        di_override_path = os.path.join(
            indices, "override.%s.local.debian-installer.%s" % (
                config.series, arch))

        with open(binary_list_path, "w") as binary_list, \
                open(di_binary_list_path, "w") as di_binary_list, \
                open(override_path, "w") as override, \
                open(di_override_path, "w") as di_override:
            for dirpath, deb in _find_endswith(
                    pool, ["_%s.deb" % arch, "_all.deb"]):
                deb_path = os.path.join(dirpath, deb)
                print(os.path.relpath(deb_path, packages), file=binary_list)
                name = deb.split("_", 1)[0]
                section = _dpkg_field(deb_path, "Section").split("/")[-1]
                priority = _dpkg_field(deb_path, "Priority")
                print(
                    "%s\t%s\tlocal/%s" % (name, priority, section),
                    file=override)

            for dirpath, udeb in _find_endswith(
                    pool, ["_%s.udeb" % arch, "_all.udeb"]):
                udeb_path = os.path.join(dirpath, udeb)
                print(
                    os.path.relpath(udeb_path, packages), file=di_binary_list)
                name = udeb.split("_", 1)[0]
                priority = _dpkg_field(udeb_path, "Priority")
                print(
                    "%s\t%s\tlocal/debian-installer" % (name, priority),
                    file=di_override)

        osextras.ensuredir(os.path.join(
            packages, "dists", config.series, "local", "binary-%s" % arch))
        osextras.ensuredir(os.path.join(
            packages, "dists", config.series, "local", "debian-installer",
            "binary-%s" % arch))

    subprocess.check_call(
        ["apt-ftparchive", "generate", "apt-ftparchive.conf"], cwd=packages)


def build_britney(config):
    update_out = os.path.join(config.root, "britney", "update_out")
    if os.path.isfile(os.path.join(update_out, "Makefile")):
        log_marker("Building britney")
        subprocess.check_call(["make", "-C", update_out])


class UnknownLocale(Exception):
    pass


def build_ubuntu_defaults_locale(config):
    locale = config["UBUNTU_DEFAULTS_LOCALE"]
    if locale != "zh_CN":
        raise UnknownLocale(
            "UBUNTU_DEFAULTS_LOCALE='%s' not currently supported!" % locale)

    series = config["DIST"]
    if series < "oneiric":
        # Original hack: repack an existing image.
        iso = config["ISO"]
        if not iso:
            raise Exception(
                "Pass ISO=<path to Ubuntu image> in the environment.")

        scratch = os.path.join(
            config.root, "scratch", "ubuntu-chinese-edition", series.name)
        bsdtar_tree = os.path.join(scratch, "bsdtar-tree")

        log_marker("Unpacking")
        if os.path.isdir(bsdtar_tree):
            subprocess.check_call(["chmod", "-R", "+w", bsdtar_tree])
        osextras.mkemptydir(bsdtar_tree)
        subprocess.check_call(["bsdtar", "-xf", iso, "-C", bsdtar_tree])
        subprocess.check_call(["chmod", "-R", "+w", bsdtar_tree])

        log_marker("Transforming (robots in disguise)")
        with open(os.path.join(bsdtar_tree, "isolinux", "lang"), "w") as lang:
            print(locale, file=lang)
        subprocess.call([
            "mkisofs",
            "-r", "-V", "Ubuntu Chinese %s i386" % series.version,
            "-o", os.path.join(scratch, os.path.basename(iso)),
            "-cache-inodes", "-J", "-l",
            "-b", "isolinux/isolinux.bin", "-c", "isolinux/boot.cat",
            "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table",
            bsdtar_tree,
        ])

        iso_prefix = iso.rsplit(".", 1)[0]
        scratch_prefix = os.path.join(
            scratch, os.path.basename(iso).rsplit(".", 1)[0])

        for ext in "list", "manifest":
            if os.path.exists("%s.%s" % (iso_prefix, ext)):
                shutil.copy2(
                    "%s.%s" % (iso_prefix, ext),
                    "%s.%s" % (scratch_prefix, ext))
            else:
                osextras.unlink_force("%s.%s" % (scratch_prefix, ext))
    else:
        download_live_filesystems(config)
        scratch = live_output_directory(config)
        for entry in os.listdir(scratch):
            if "." in entry:
                os.rename(
                    os.path.join(scratch, entry),
                    os.path.join(
                        scratch, "%s-desktop-%s" % (series.name, entry)))
        pi_makelist = os.path.join(
            config.root, "debian-cd", "tools", "pi-makelist")
        for entry in os.listdir(scratch):
            if entry.endswith(".iso"):
                entry_path = os.path.join(scratch, entry)
                list_path = "%s.list" % entry_path.rsplit(".", 1)[0]
                with open(list_path, "w") as list_file:
                    subprocess.check_call(
                        [pi_makelist, entry_path], stdout=list_file)


def _debootstrap_script(config):
    if config["DIST"] <= "gutsy":
        return "usr/lib/debootstrap/scripts/%s" % config.series
    else:
        return "usr/share/debootstrap/scripts/%s" % config.series


def extract_debootstrap(config):
    series = config["DIST"]
    output_dir = os.path.join(
        config.root, "scratch", config.project, series.name, config.image_type,
        "debootstrap")

    osextras.ensuredir(output_dir)

    for fullarch in config.arches:
        arch = fullarch.split("+")[0]
        mirror = find_mirror(config, arch)
        # TODO: This might be more sensible with python-debian or python-apt.
        packages_path = os.path.join(
            mirror, "dists", series.name, "main", "debian-installer",
            "binary-%s" % arch, "Packages.gz")
        with gzip.GzipFile(packages_path, "rb") as packages:
            grep_dctrl = subprocess.Popen(
                ["grep-dctrl", "-nsFilename", "-PX", "debootstrap-udeb"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            udeb, _ = grep_dctrl.communicate(packages.read())
        if not isinstance(udeb, str):
            udeb = udeb.decode()
        udeb = udeb.rstrip("\n")
        udeb_path = os.path.join(mirror, udeb)
        if not udeb or not os.path.exists(udeb_path):
            logger.warning(
                "No debootstrap-udeb for %s/%s!" % (config.series, arch))
            continue
        # TODO: With python-debian, we could extract the one file we need
        # directly.
        unpack_dir = os.path.join(output_dir, "unpack-%s" % fullarch)
        try:
            shutil.rmtree(unpack_dir)
        except OSError:
            pass
        subprocess.check_call(["dpkg", "-x", udeb_path, unpack_dir])
        shutil.copy2(
            os.path.join(unpack_dir, _debootstrap_script(config)),
            os.path.join(output_dir, "%s-%s" % (config.series, fullarch)))


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


def run_debian_cd(config):
    log_marker("Building %s daily CDs" % config.capproject)
    debian_cd_dir = os.path.join(config.root, "debian-cd")
    subprocess.call(["./build_all.sh"], cwd=debian_cd_dir, env=config.export())


def fix_permissions(config):
    """Kludge to work around permission-handling problems elsewhere."""
    scratch_dir = os.path.join(
        config.root, "scratch", config.project, config.series,
        config.image_type)
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
    if config["DEBUG"]:
        return

    project = config.project
    if config["UBUNTU_DEFAULTS_LOCALE"] == "zh_CN":
        project = "ubuntu-chinese-edition"
    series = config.series
    image_type = config.image_type
    date = config["CDIMAGE_DATE"]

    recipients = get_notify_addresses(config, project)
    if not recipients:
        return

    try:
        if log_path is None:
            body = ""
        else:
            body = open(log_path)
        send_mail(
            "CD image %s/%s/%s failed to build on %s" % (
                project, series, image_type, date),
            "build-image-set", recipients, body)
    finally:
        if log_path is not None:
            body.close()


def build_image_set_locked(config, semaphore_state):
    image_type = config.image_type
    config["CDIMAGE_DATE"] = date = next_build_id(config, image_type)
    log_path = None

    try:
        configure_for_project(config)
        log_path = open_log(config)

        sync_local_mirror(config, semaphore_state)

        if config["LOCAL"]:
            log_marker("Updating archive of local packages")
            update_local_indices(config)

        build_britney(config)

        log_marker("Extracting debootstrap scripts")
        extract_debootstrap(config)

        if config["UBUNTU_DEFAULTS_LOCALE"]:
            build_ubuntu_defaults_locale(config)
        else:
            if not config["CDIMAGE_PREINSTALLED"]:
                log_marker("Germinating")
                germination = Germination(config)
                germination.run()

                log_marker("Generating new task lists")
                germinate_output = germination.output(config.project)
                germinate_output.write_tasks()

                log_marker("Checking for other task changes")
                germinate_output.update_tasks(date)

            if (config["CDIMAGE_LIVE"] or config["CDIMAGE_SQUASHFS_BASE"] or
                    config["CDIMAGE_PREINSTALLED"]):
                log_marker("Downloading live filesystem images")
                download_live_filesystems(config)

            configure_splash(config)

            run_debian_cd(config)
            fix_permissions(config)

        # Temporarily turned off for live builds.
        if (config["CDIMAGE_INSTALL_BASE"] and
                not config["CDIMAGE_ADDON"] and
                not config["CDIMAGE_PREINSTALLED"]):
            log_marker("Producing installability report")
            check_installable(config)

        if not config["DEBUG"] and not config["CDIMAGE_NOPUBLISH"]:
            log_marker("Publishing")
            tree = Tree.get_daily(config)
            publisher = Publisher.get_daily(tree, image_type)
            publisher.publish(date)

            log_marker("Purging old images")
            publisher.purge()

            log_marker("Triggering mirrors")
            trigger_mirrors(config)

        log_marker("Finished")
        return True
    except Exception:
        for line in traceback.format_exc().splitlines():
            logger.error(line)
        sys.stdout.flush()
        sys.stderr.flush()
        notify_failure(config, log_path)
        return False


def build_image_set(config):
    """Master entry point for building images."""
    semaphore = Semaphore(
        os.path.join(config.root, "etc", ".sem-build-image-set"))
    with lock_build_image_set(config), semaphore.held() as semaphore_state:
        return build_image_set_locked(config, semaphore_state)
