#! /usr/bin/python

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

"""Unit tests for cdimage.build."""

from __future__ import print_function

from functools import partial
import optparse
import os
import signal
import stat
import sys
from textwrap import dedent
import time
import traceback

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage import osextras
from cdimage.build import (
    build_britney,
    build_image_set,
    build_image_set_locked,
    build_livecd_base,
    configure_for_project,
    configure_splash,
    fix_permissions,
    lock_build_image_set,
    log_marker,
    notify_failure,
    open_log,
    run_debian_cd,
    want_live_builds,
)
from cdimage.config import Config
from cdimage.log import logger
from cdimage.mail import text_file_type
from cdimage.tests.helpers import TestCase, mkfile, touch, StubAptStateManager
from cdimage.tests.test_livefs import MockLiveFSBuild

__metaclass__ = type


def mock_builds_for_config(config):
    return {arch: MockLiveFSBuild() for arch in config.arches}


class TestBuildLiveCDBase(TestCase):
    def setUp(self):
        super(TestBuildLiveCDBase, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.config["CDIMAGE_LIVE"] = "1"
        with mkfile(os.path.join(
                self.temp_dir, "production", "livefs-builders")) as f:
            print("* * * mock-builder", file=f)
        mock_gmtime = mock.patch("time.gmtime", return_value=time.gmtime(0))
        mock_gmtime.start()
        self.addCleanup(mock_gmtime.stop)
        self.epoch_date = "Thu Jan  1 00:00:00 UTC 1970"

    @mock.patch("cdimage.sign.sign_cdimage")
    @mock.patch("cdimage.osextras.fetch")
    def test_livecd_base(self, mock_fetch, mock_sign):
        def fetch_side_effect(config, source, target):
            tail = os.path.basename(target).split(".", 1)[1]
            if tail in ("manifest", "squashfs"):
                touch(target)
            else:
                raise osextras.FetchError

        def sign_side_effect(config, target):
            tail = os.path.basename(target).split(".", 1)[1]
            if tail in ("manifest", "squashfs"):
                touch(target + ".gpg")
            else:
                return False

        mock_fetch.side_effect = fetch_side_effect
        mock_sign.side_effect = sign_side_effect
        self.config["PROJECT"] = "livecd-base"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "livecd-base"
        self.config["ARCHES"] = "amd64"
        self.capture_logging()
        build_livecd_base(self.config, mock_builds_for_config(self.config))
        self.assertLogEqual([
            "===== Downloading live filesystem images =====",
            self.epoch_date,
        ])
        live_dir = os.path.join(
            self.temp_dir, "scratch", "livecd-base", "bionic", "livecd-base",
            "live")
        self.assertTrue(os.path.isdir(live_dir))
        self.assertCountEqual(
            ["amd64.manifest", "amd64.squashfs", "amd64.squashfs.gpg"],
            os.listdir(live_dir))

    @mock.patch("cdimage.osextras.fetch")
    def test_ubuntu_base(self, mock_fetch):
        def fetch_side_effect(config, source, target):
            if (target.endswith(".manifest") or
                    target.endswith(".rootfs.tar.gz")):
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["PROJECT"] = "ubuntu-base"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64"
        self.capture_logging()
        build_livecd_base(self.config, mock_builds_for_config(self.config))
        self.assertLogEqual([
            "===== Downloading live filesystem images =====",
            self.epoch_date,
            "===== Copying images to debian-cd output directory =====",
            self.epoch_date,
        ])
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu-base", "bionic", "daily",
            "debian-cd", "amd64")
        self.assertTrue(os.path.isdir(output_dir))
        self.assertCountEqual([
            "bionic-base-amd64.manifest",
            "bionic-base-amd64.raw",
            "bionic-base-amd64.type",
        ], os.listdir(output_dir))
        with open(os.path.join(output_dir, "bionic-base-amd64.type")) as f:
            self.assertEqual("tar archive\n", f.read())

    @mock.patch("cdimage.osextras.fetch")
    def test_ubuntu_server_preinstalled_raspi2(self, mock_fetch):
        def fetch_side_effect(config, source, target):
            if (target.endswith(".manifest") or
                    target.endswith(".disk1.img.xz")):
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["CDIMAGE_PREINSTALLED"] = "1"
        self.config["PROJECT"] = "ubuntu-server"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily-preinstalled"
        self.config["ARCHES"] = "armhf+raspi2"
        self.capture_logging()
        build_livecd_base(self.config, mock_builds_for_config(self.config))
        self.assertLogEqual([
            "===== Downloading live filesystem images =====",
            self.epoch_date,
            "===== Copying images to debian-cd output directory =====",
            self.epoch_date,
        ])
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu-server", "bionic",
            "daily-preinstalled", "debian-cd", "armhf+raspi2")
        self.assertTrue(os.path.isdir(output_dir))
        self.assertCountEqual([
            "bionic-preinstalled-server-armhf+raspi2.manifest",
            "bionic-preinstalled-server-armhf+raspi2.raw",
            "bionic-preinstalled-server-armhf+raspi2.type",
        ], os.listdir(output_dir))

    @mock.patch("cdimage.osextras.fetch")
    def test_ubuntu_core_raspi3(self, mock_fetch):
        def fetch_side_effect(config, source, target):
            if (target.endswith(".model-assertion") or
                    target.endswith(".manifest") or
                    target.endswith(".img.xz")):
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["CDIMAGE_LIVE"] = "1"
        self.config["PROJECT"] = "ubuntu-core"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.config["ARCHES"] = "armhf+raspi3"
        self.capture_logging()
        build_livecd_base(self.config, mock_builds_for_config(self.config))
        self.assertLogEqual([
            "===== Downloading live filesystem images =====",
            self.epoch_date,
            "===== Copying images to debian-cd output directory =====",
            self.epoch_date,
        ])
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu-core", "bionic",
            "daily-live", "live")
        self.assertTrue(os.path.isdir(output_dir))
        self.assertCountEqual([
            "armhf+raspi3.img.xz",
            "armhf+raspi3.model-assertion",
            "armhf+raspi3.manifest",
        ], os.listdir(output_dir))

    @mock.patch("cdimage.osextras.fetch")
    def test_ubuntu_appliance_raspi(self, mock_fetch):
        def fetch_side_effect(config, source, target):
            if (target.endswith(".model-assertion") or
                    target.endswith(".manifest") or
                    target.endswith(".img.xz")):
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["CDIMAGE_LIVE"] = "1"
        self.config["PROJECT"] = "ubuntu-appliance"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.config["ARCHES"] = "armhf+raspi"
        self.capture_logging()
        build_livecd_base(self.config, mock_builds_for_config(self.config))
        self.assertLogEqual([
            "===== Downloading live filesystem images =====",
            self.epoch_date,
            "===== Copying images to debian-cd output directory =====",
            self.epoch_date,
        ])
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu-appliance", "bionic",
            "daily-live", "live")
        self.assertTrue(os.path.isdir(output_dir))
        self.assertCountEqual([
            "armhf+raspi.img.xz",
            "armhf+raspi.model-assertion",
            "armhf+raspi.manifest",
        ], os.listdir(output_dir))

    @mock.patch("cdimage.osextras.fetch")
    def test_ubuntu_appliance_amd64(self, mock_fetch):
        def fetch_side_effect(config, source, target):
            if (target.endswith(".model-assertion") or
                    target.endswith(".manifest") or
                    target.endswith(".img.xz") or
                    target.endswith(".qcow2")):
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["CDIMAGE_LIVE"] = "1"
        self.config["PROJECT"] = "ubuntu-appliance"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.config["ARCHES"] = "amd64"
        self.capture_logging()
        build_livecd_base(self.config, mock_builds_for_config(self.config))
        self.assertLogEqual([
            "===== Downloading live filesystem images =====",
            self.epoch_date,
            "===== Copying images to debian-cd output directory =====",
            self.epoch_date,
        ])
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu-appliance", "bionic",
            "daily-live", "live")
        self.assertTrue(os.path.isdir(output_dir))
        self.assertCountEqual([
            "amd64.img.xz",
            "amd64.model-assertion",
            "amd64.manifest",
            "amd64.qcow2",
        ], os.listdir(output_dir))


class TestBuildImageSet(TestCase):
    def setUp(self):
        super(TestBuildImageSet, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.expected_sync_lock = os.path.join(
            self.temp_dir, "etc", ".lock-archive-sync")
        mock_gmtime = mock.patch("time.gmtime", return_value=time.gmtime(0))
        mock_gmtime.start()
        self.addCleanup(mock_gmtime.stop)
        self.epoch_date = "Thu Jan  1 00:00:00 UTC 1970"

    @mock.patch("subprocess.check_call")
    @mock.patch("cdimage.osextras.unlink_force")
    def test_lock_build_image_set(self, mock_unlink_force, mock_check_call):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        expected_lock_path = os.path.join(
            self.temp_dir, "etc", ".lock-build-image-set-ubuntu-bionic-daily")
        self.assertFalse(os.path.exists(expected_lock_path))
        with lock_build_image_set(self.config):
            mock_check_call.assert_called_once_with([
                "lockfile", "-l", "7200", "-r", "0", expected_lock_path])
            self.assertEqual(0, mock_unlink_force.call_count)
        mock_unlink_force.assert_called_once_with(expected_lock_path)

    @mock.patch("subprocess.check_call")
    @mock.patch("cdimage.osextras.unlink_force")
    def test_lock_build_image_set_subtree(
            self, mock_unlink_force, mock_check_call):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config.subtree = "test/subtree"
        expected_lock_path = os.path.join(
            self.temp_dir, "etc",
            ".lock-build-image-set-test-subtree-ubuntu-bionic-daily")
        self.assertFalse(os.path.exists(expected_lock_path))
        with lock_build_image_set(self.config):
            mock_check_call.assert_called_once_with([
                "lockfile", "-l", "7200", "-r", "0", expected_lock_path])
            self.assertEqual(0, mock_unlink_force.call_count)
        mock_unlink_force.assert_called_once_with(expected_lock_path)

    def test_configure_onlyfree_unsupported(self):
        for project, series, onlyfree, unsupported in (
            ("ubuntu", "bionic", False, False),
            ("edubuntu", "lunar", False, True),
            ("xubuntu", "bionic", False, True),
            ("kubuntu", "bionic", False, True),
            ("ubuntustudio", "bionic", False, True),
            ("lubuntu", "bionic", False, True),
            ("ubuntukylin", "bionic", False, True),
            ("ubuntu-gnome", "bionic", False, True),
            ("ubuntu-budgie", "bionic", False, True),
            ("ubuntu-mate", "bionic", False, True),
        ):
            config = Config(read=False)
            config["PROJECT"] = project
            config["DIST"] = series
            configure_for_project(config)
            if onlyfree:
                self.assertEqual("1", config["CDIMAGE_ONLYFREE"])
            else:
                self.assertNotIn("CDIMAGE_ONLYFREE", config)
            if unsupported:
                self.assertEqual("1", config["CDIMAGE_UNSUPPORTED"])
            else:
                self.assertNotIn("CDIMAGE_UNSUPPORTED", config)

    @mock.patch("os.open")
    def test_open_log_debug(self, mock_open):
        self.config["DEBUG"] = "1"
        self.assertIsNone(open_log(self.config))
        self.assertEqual(0, mock_open.call_count)

    def test_open_log_writes_log(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["CDIMAGE_DATE"] = "20130224"
        pid = os.fork()
        if pid == 0:  # child
            log_path = open_log(self.config)
            print("Log path: %s" % log_path)
            print("VERBOSE: %s" % self.config["VERBOSE"])
            sys.stdout.flush()
            print("Standard error", file=sys.stderr)
            sys.stderr.flush()
            os._exit(0)
        else:  # parent
            self.wait_for_pid(pid, 0)
            expected_log_path = os.path.join(
                self.temp_dir, "log", "ubuntu", "bionic", "daily-20130224.log")
            self.assertTrue(os.path.exists(expected_log_path))
            with open(expected_log_path) as log:
                self.assertEqual([
                    "Log path: %s" % expected_log_path,
                    "VERBOSE: 3",
                    "Standard error",
                ], log.read().splitlines())

    def test_log_marker(self):
        self.capture_logging()
        log_marker("Testing")
        self.assertLogEqual(["===== Testing =====", self.epoch_date])

    def test_want_live_builds_no_options(self):
        self.assertFalse(want_live_builds(None))

    def test_want_live_builds_irrelevant_options(self):
        self.assertFalse(want_live_builds(optparse.Values()))

    def test_want_live_builds_option_false(self):
        options = optparse.Values({"live": False})
        self.assertFalse(want_live_builds(options))

    def test_want_live_builds_option_true(self):
        options = optparse.Values({"live": True})
        self.assertTrue(want_live_builds(options))

    @mock.patch("subprocess.check_call")
    def test_build_britney_no_makefile(self, mock_check_call):
        self.capture_logging()
        build_britney(self.config)
        self.assertLogEqual([])
        self.assertEqual(0, mock_check_call.call_count)

    @mock.patch("subprocess.check_call")
    def test_build_britney_with_makefile(self, mock_check_call):
        path = os.path.join(self.temp_dir, "britney", "update_out", "Makefile")
        touch(path)
        self.capture_logging()
        build_britney(self.config)
        self.assertLogEqual(["===== Building britney =====", self.epoch_date])
        mock_check_call.assert_called_once_with(
            ["make", "-C", os.path.dirname(path)])

    def test_configure_splash(self):
        data_dir = os.path.join(self.temp_dir, "debian-cd", "data", "bionic")
        for key, extension in (
            ("SPLASHRLE", "rle"),
            ("GFXSPLASH", "pcx"),
            ("SPLASHPNG", "png"),
        ):
            for project_specific in True, False:
                config = Config(read=False)
                config.root = self.temp_dir
                config["PROJECT"] = "kubuntu"
                config["DIST"] = "bionic"
                path = os.path.join(
                    data_dir, "%s.%s" % (
                        "kubuntu" if project_specific else "splash",
                        extension))
                touch(path)
                configure_splash(config)
                self.assertEqual(path, config[key])
                osextras.unlink_force(path)

    @mock.patch("subprocess.call", return_value=0)
    def test_run_debian_cd(self, mock_call):
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["ARCHES"] = "amd64 arm64"
        self.config.set_default_cpuarches()
        self.capture_logging()
        run_debian_cd(self.config, StubAptStateManager())
        self.assertLogEqual([
            "===== Building Ubuntu daily CDs =====",
            self.epoch_date,
        ])
        expected_cwd = os.path.join(self.temp_dir, "debian-cd")
        mock_call.assert_called_once_with(
            ["./build_all.sh"], cwd=expected_cwd, env=mock.ANY)
        env = mock_call.call_args.kwargs["env"]
        self.assertEqual("amd64/apt.conf", env["APT_CONFIG_amd64"])
        self.assertEqual("arm64/apt.conf", env["APT_CONFIG_arm64"])

    @mock.patch("subprocess.call", return_value=0)
    def test_run_debian_cd_for_core(self, mock_call):
        self.config["CAPPROJECT"] = "Ubuntu Core Desktop"
        self.config["PROJECT"] = "ubuntu-core-desktop"
        self.config["ARCHES"] = "amd64"
        self.config["DIST"] = "jammy"
        self.config.set_default_cpuarches()
        self.capture_logging()
        run_debian_cd(self.config, StubAptStateManager())
        self.assertLogEqual([
            "===== Building Ubuntu Core Desktop daily CDs =====",
            self.epoch_date,
        ])
        expected_cwd = os.path.join(self.temp_dir, "debian-cd")
        mock_call.assert_called_once_with(
            ["./build_all.sh"], cwd=expected_cwd, env=mock.ANY)
        env = mock_call.call_args.kwargs["env"]
        self.assertEqual("22", env["CDIMAGE_CORE_SERIES"])

    @mock.patch("subprocess.call", return_value=0)
    def test_run_debian_cd_reexports_config(self, mock_call):
        # We need to re-export configuration to debian-cd even if we didn't
        # get it in our environment, since debian-cd won't read etc/config
        # for itself.
        with mkfile(os.path.join(self.temp_dir, "etc", "config")) as f:
            print(dedent("""\
                #! /bin/sh
                PROJECT=ubuntu
                CAPPROJECT=Ubuntu
                ARCHES="amd64 arm64"
                """), file=f)
        os.environ["CDIMAGE_ROOT"] = self.temp_dir
        config = Config()
        self.capture_logging()
        run_debian_cd(config, StubAptStateManager())
        self.assertLogEqual([
            "===== Building Ubuntu daily CDs =====",
            self.epoch_date,
        ])
        expected_cwd = os.path.join(self.temp_dir, "debian-cd")
        mock_call.assert_called_once_with(
            ["./build_all.sh"], cwd=expected_cwd, env=mock.ANY)
        self.assertEqual(
            "amd64 arm64", mock_call.call_args[1]["env"]["ARCHES"])

    def test_fix_permissions(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        scratch_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "bionic", "daily")
        subdir = os.path.join(scratch_dir, "x")
        dir_one = os.path.join(subdir, "1")
        file_two = os.path.join(subdir, "2")
        file_three = os.path.join(subdir, "3")
        osextras.ensuredir(dir_one)
        touch(file_two)
        touch(file_three)
        for path, perm in (
            (scratch_dir, 0o755),
            (subdir, 0o2775),
            (dir_one, 0o700),
            (file_two, 0o664),
            (file_three, 0o600),
        ):
            os.chmod(path, perm)
        fix_permissions(self.config)
        for path, perm in (
            (scratch_dir, 0o2775),
            (subdir, 0o2775),
            (dir_one, 0o2770),
            (file_two, 0o664),
            (file_three, 0o660),
        ):
            self.assertEqual(perm, stat.S_IMODE(os.stat(path).st_mode))

    @mock.patch("cdimage.build.get_notify_addresses")
    def test_notify_failure_debug(self, mock_notify_addresses):
        self.config["DEBUG"] = "1"
        notify_failure(self.config, None)
        self.assertEqual(0, mock_notify_addresses.call_count)

    @mock.patch("cdimage.build.send_mail")
    def test_notify_failure_no_recipients(self, mock_send_mail):
        self.config["DIST"] = "bionic"
        notify_failure(self.config, None)
        self.assertEqual(0, mock_send_mail.call_count)

    @mock.patch("cdimage.build.send_mail")
    def test_notify_failure_no_log(self, mock_send_mail):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["CDIMAGE_DATE"] = "20130225"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path) as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        notify_failure(self.config, None)
        mock_send_mail.assert_called_once_with(
            "CD image ubuntu/bionic/daily failed to build on 20130225",
            "build-image-set", ["foo@example.org"], "")

    @mock.patch("cdimage.build.send_mail")
    def test_notify_failure_log(self, mock_send_mail):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["CDIMAGE_DATE"] = "20130225"
        self.config.subtree = "test"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path) as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        log_path = os.path.join(self.temp_dir, "log")
        with mkfile(log_path) as log:
            print("Log", file=log)
        notify_failure(self.config, log_path)
        mock_send_mail.assert_called_once_with(
            "CD image test/ubuntu/bionic/daily failed to build on 20130225",
            "build-image-set", ["foo@example.org"], mock.ANY)
        self.assertEqual(log_path, mock_send_mail.call_args[0][3].name)

    def send_mail_to_file(self, path, subject, generator, recipients, body,
                          dry_run=False):
        with mkfile(path) as f:
            print("To: %s" % ", ".join(recipients), file=f)
            print("Subject: %s" % subject, file=f)
            print("X-Generated-By: %s" % generator, file=f)
            print("", file=f)
            if isinstance(body, text_file_type):
                for line in body:
                    print(line.rstrip("\n"), file=f)
            else:
                for line in body.splitlines():
                    print(line, file=f)

    @mock.patch("time.strftime", return_value="20130225")
    @mock.patch("cdimage.build.tracker_set_rebuild_status")
    @mock.patch("cdimage.build.is_live_fs_only")
    @mock.patch("cdimage.build.send_mail")
    def test_build_image_set_locked_notifies_on_failure(
            self, mock_send_mail, mock_livefs_only, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["CDIMAGE_DATE"] = "20130225"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path, "w") as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        log_path = os.path.join(
            self.temp_dir, "log", "ubuntu", "bionic", "daily-20130225.log")
        os.makedirs(os.path.join(self.temp_dir, "etc"))

        def force_failure(*args):
            logger.error("Forced image build failure")
            raise Exception("Artificial exception")

        mock_livefs_only.side_effect = force_failure
        mock_send_mail.side_effect = partial(
            self.send_mail_to_file, os.path.join(self.temp_dir, "mail"))
        pid = os.fork()
        if pid == 0:  # child
            original_stderr = os.dup(sys.stderr.fileno())
            try:
                self.assertFalse(build_image_set_locked(self.config, None))
            except AssertionError:
                stderr = os.fdopen(original_stderr, "w", 1)
                try:
                    with open(log_path) as log:
                        stderr.write(log.read())
                except IOError:
                    pass
                traceback.print_exc(file=stderr)
                stderr.flush()
                os._exit(1)
            except Exception:
                os._exit(1)
            os._exit(0)
        else:  # parent
            self.wait_for_pid(pid, 0)
            with open(log_path) as log:
                self.assertEqual(
                    "Forced image build failure\n", log.readline())
                self.assertEqual(
                    "Traceback (most recent call last):\n", log.readline())
                self.assertIn("Exception: Artificial exception", log.read())

    @mock.patch("cdimage.tree.DailyTreePublisher.refresh_simplestreams")
    @mock.patch("subprocess.call", return_value=0)
    @mock.patch("cdimage.build.tracker_set_rebuild_status")
    @mock.patch("cdimage.germinate.GerminateOutput.write_tasks")
    @mock.patch("cdimage.germinate.GerminateOutput.update_tasks")
    @mock.patch("cdimage.tree.DailyTreePublisher.publish")
    @mock.patch("cdimage.tree.DailyTreePublisher.purge")
    def test_build_image_set_locked(
            self, mock_purge, mock_publish, mock_update_tasks,
            mock_write_tasks, mock_tracker_set_rebuild_status,
            mock_call, mock_simple):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64 i386"
        self.config["CPUARCHES"] = "amd64 i386"

        britney_makefile = os.path.join(
            self.temp_dir, "britney", "update_out", "Makefile")
        touch(britney_makefile)
        os.makedirs(os.path.join(self.temp_dir, "etc"))
        germinate_path = os.path.join(
            self.temp_dir, "germinate", "bin", "germinate")
        touch(germinate_path)
        os.chmod(germinate_path, 0o755)
        daily_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "bionic", "daily")
        germinate_output = os.path.join(daily_dir, "germinate")
        apt_config = os.path.join(
            daily_dir, "apt-state", "{ARCH}", "base.conf")
        log_dir = os.path.join(self.temp_dir, "log", "ubuntu", "bionic")

        def side_effect(command, *args, **kwargs):
            if command[0] == germinate_path:
                for arch in self.config.arches:
                    touch(os.path.join(germinate_output, arch, "structure"))

        mock_call.side_effect = side_effect

        pid = os.fork()
        if pid == 0:  # child
            original_stderr = os.dup(sys.stderr.fileno())
            try:
                self.assertTrue(build_image_set_locked(self.config, None))
                date = self.config["CDIMAGE_DATE"]
                debian_cd_dir = os.path.join(self.temp_dir, "debian-cd")

                def germinate_command(arch):
                    return mock.call([
                        germinate_path,
                        "--seed-source", mock.ANY,
                        "--seed-dist", "ubuntu.bionic",
                        "--arch", arch,
                        "--no-rdepends",
                        "--apt-config", apt_config.replace("{ARCH}", arch),
                        "--vcs=git",
                    ], cwd=os.path.join(germinate_output, arch), env=mock.ANY)

                mock_call.assert_has_calls([
                    mock.call([
                        "make", "-C", os.path.dirname(britney_makefile)]),
                    mock.call(["apt-get", "update"], env=mock.ANY),
                    mock.call(["apt-get", "update"], env=mock.ANY),
                    germinate_command("amd64"),
                    germinate_command("i386"),
                    mock.call(
                        ["./build_all.sh"], cwd=debian_cd_dir, env=mock.ANY),
                ])
                mock_tracker_set_rebuild_status.assert_called_once_with(
                    self.config, [0, 1], 2)
                mock_write_tasks.assert_called_once_with()
                mock_update_tasks.assert_called_once_with(date)
                mock_publish.assert_called_once_with(date)
                mock_purge.assert_called_once_with()
            except AssertionError:
                stderr = os.fdopen(original_stderr, "w", 1)
                try:
                    for entry in os.listdir(log_dir):
                        with open(os.path.join(log_dir, entry)) as log:
                            stderr.write(log.read())
                except IOError:
                    pass
                traceback.print_exc(file=stderr)
                stderr.flush()
                os._exit(1)
            except Exception:
                os._exit(1)
            os._exit(0)
        else:  # parent
            self.wait_for_pid(pid, 0)
            self.assertTrue(os.path.isdir(log_dir))
            log_entries = os.listdir(log_dir)
            self.assertEqual(1, len(log_entries))
            log_path = os.path.join(log_dir, log_entries[0])
            with open(log_path) as log:
                self.assertEqual(dedent("""\
                    ===== Building britney =====
                    DATE
                    Setting up apt state for bionic/amd64 ...
                    Setting up apt state for bionic/i386 ...
                    ===== Germinating =====
                    DATE
                    Germinating for bionic/amd64 ...
                    Germinating for bionic/i386 ...
                    ===== Generating new task lists =====
                    DATE
                    ===== Checking for other task changes =====
                    DATE
                    ===== Building Ubuntu daily CDs =====
                    DATE
                    ===== Publishing =====
                    DATE
                    ===== Purging old images =====
                    DATE
                    ===== Handling simplestreams =====
                    DATE
                    ===== Triggering mirrors =====
                    DATE
                    ===== Finished =====
                    DATE
                    """.replace("DATE", self.epoch_date)), log.read())

    @mock.patch(
        "cdimage.build.build_image_set_locked", side_effect=KeyboardInterrupt)
    def test_build_image_set_interrupted(self, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        lock_path = os.path.join(
            self.temp_dir, "etc", ".lock-build-image-set-ubuntu-bionic-daily")
        os.makedirs(os.path.dirname(lock_path))
        self.assertRaises(
            KeyboardInterrupt, build_image_set, self.config, None)
        self.assertFalse(os.path.exists(lock_path))

    @mock.patch("cdimage.build.build_image_set_locked")
    def test_build_image_set_terminated(self, mock_build_image_set_locked):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        lock_path = os.path.join(
            self.temp_dir, "etc", ".lock-build-image-set-ubuntu-bionic-daily")
        os.makedirs(os.path.dirname(lock_path))

        def side_effect(config, options):
            os.kill(os.getpid(), signal.SIGTERM)

        mock_build_image_set_locked.side_effect = side_effect
        pid = os.fork()
        if pid == 0:  # child
            build_image_set(self.config, None)
            os._exit(1)
        else:  # parent
            self.wait_for_pid(pid, signal.SIGTERM)

    @mock.patch("cdimage.build.build_image_set_locked")
    def test_build_image_set(self, mock_build_image_set_locked):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        lock_path = os.path.join(
            self.temp_dir, "etc", ".lock-build-image-set-ubuntu-bionic-daily")
        os.makedirs(os.path.dirname(lock_path))

        def side_effect(config, options):
            self.assertTrue(os.path.exists(lock_path))
            self.assertIsNone(options)

        mock_build_image_set_locked.side_effect = side_effect
        build_image_set(self.config, None)
