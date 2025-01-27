#! /usr/bin/python

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

"""Unit tests for cdimage.livefs."""

from __future__ import print_function

from collections import defaultdict
from itertools import chain, repeat
import os
import subprocess
from textwrap import dedent
import time
try:
    from urllib.request import urlopen
except ImportError:
    from urllib2 import urlopen
from unittest import skipUnless

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage import osextras
from cdimage.config import Config, all_series
from cdimage.launchpad import get_launchpad, launchpad_available
from cdimage.livefs import (
    LiveBuildsFailed,
    download_live_filesystems,
    live_build_command,
    live_build_full_name,
    live_build_notify_failure,
    live_build_options,
    live_builder,
    live_output_directory,
    live_project,
    livecd_base,
    run_live_builds,
    split_arch,
)
from cdimage.tests.helpers import TestCase, mkfile, touch

__metaclass__ = type


class MockPeople(defaultdict):
    def __missing__(self, key):
        person = mock.MagicMock(name="Person(%s)" % key)
        person.name = key
        self[key] = person
        return person


class MockDistroArchSeries(mock.MagicMock):
    def __init__(self, architecture_tag=None, *args, **kwargs):
        super(MockDistroArchSeries, self).__init__(*args, **kwargs)
        self._architecture_tag = architecture_tag

    @property
    def architecture_tag(self):
        return self._architecture_tag


class MockDistroSeries(mock.MagicMock):
    def getDistroArchSeries(self, archtag=None):
        return MockDistroArchSeries(
            name="DistroArchSeries(%s, %s, %s)" % (
                self.distribution.name, self.name, archtag),
            architecture_tag=archtag)


class MockDistribution(mock.MagicMock):
    def getSeries(self, name_or_version=None, **kwargs):
        distroseries = MockDistroSeries(
            name="MockDistroSeries(%s, %s)" % (self.name, name_or_version))
        distroseries.distribution = self
        return distroseries


class MockDistributions(defaultdict):
    def __missing__(self, key):
        distribution = MockDistribution(name="Distribution(%s)" % key)
        distribution.name = key
        self[key] = distribution
        return distribution


class MockLiveFSBuild(mock.MagicMock):
    def __init__(self, distro_arch_series=None, *args, **kwargs):
        super(MockLiveFSBuild, self).__init__(*args, **kwargs)
        self._buildstates = self._iter_buildstate()
        self._distro_arch_series = distro_arch_series

    def _iter_buildstate(self):
        return repeat("Needs building")

    def lp_refresh(self):
        self.buildstate = next(self._buildstates)

    @property
    def web_link(self):
        return "https://launchpad.example/%s-build" % (
            self._distro_arch_series.architecture_tag)


class MockLiveFS(mock.MagicMock):
    def requestBuild(self, distro_arch_series=None, **kwargs):
        build = MockLiveFSBuild(distro_arch_series=distro_arch_series)
        build.buildstate = "Needs building"
        return build


class MockLiveFSes(mock.MagicMock):
    def getByName(self, owner=None, distro_series=None, name=None, **kwargs):
        return MockLiveFS(
            name="MockLiveFS(%s, %s/%s, %s)" % (
                owner.name, distro_series.distribution.name,
                distro_series.name, name))


class MockLaunchpad(mock.MagicMock):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("name", "Launchpad")
        super(MockLaunchpad, self).__init__(*args, **kwargs)
        self.people = MockPeople()
        self.distributions = MockDistributions()
        self.livefses = MockLiveFSes()


class TestSplitArch(TestCase):
    def test_amd64(self):
        config = Config(read=False)
        self.assertEqual(("amd64", ""), split_arch(config, "amd64"))

    def test_arm64_raspi(self):
        config = Config(read=False)
        self.assertEqual(("arm64", "raspi"), split_arch(config, "arm64+raspi"))

    def test_armhf_omap4(self):
        config = Config(read=False)
        self.assertEqual(("armhf", "omap4"), split_arch(config, "armhf+omap4"))

    def test_i386(self):
        config = Config(read=False)
        self.assertEqual(("i386", ""), split_arch(config, "i386"))


class TestLiveProject(TestCase):
    def assertProjectEqual(self, expected, project, series, arch="i386",
                           **kwargs):
        os.environ["CDIMAGE_ROOT"] = self.use_temp_dir()
        os.environ["ARCHES"] = arch
        etc_dir = os.path.join(self.temp_dir, "etc")
        with mkfile(os.path.join(etc_dir, "config")) as f:
            print(dedent("""\
                #! /bin/sh
                PROJECT=%s
                DIST=%s
                """ % (project, series)), file=f)
        with mkfile(os.path.join(etc_dir, "cdimage-to-livecd-rootfs-map")) \
             as f:
            print("ubuntu-appliance\t*\t*\t*\t*\tubuntu-core\t*\t*\n"
                  "livecd-base\t*\t*\t*\t*\tbase\t*\t*", file=f)
        config = Config()
        for key, value in kwargs.items():
            config[key.upper()] = value
        self.assertEqual(expected, live_project(config, arch))

    def test_project_livecd_base(self):
        self.assertProjectEqual("base", "livecd-base", "dapper")

    def test_ubuntu_appliance(self):
        # We currently only support ubuntu-appliances for bionic (UC18)
        self.assertProjectEqual(
            "ubuntu-core", "ubuntu-appliance", "bionic")


def make_livefs_production_config(config):
    config_path = os.path.join(config.root, "production", "livefs-builders")
    # TODO: This is just a copy of the current production configuration as
    # of 2014-05-09; it's not really in the spirit of unit testing, and we
    # should be writing more specific tests instead.
    with mkfile(config_path) as f:
        print(dedent("""\
            *\t\t*\t\tamd64\t\t\tkapok.buildd
            *\t\t*\t\tarm64\t\t\tmagic.buildd
            *\t\t*\t\tarmel\t\t\tcelbalrai.buildd
            ubuntu-server\t*\t\tarmhf+omap4\t\tcelbalrai.buildd
            *\t\t*\t\tarmhf+ac100\t\tcelbalrai.buildd
            *\t\t*\t\tarmhf+nexus7\t\tcelbalrai.buildd
            *\t\t*\t\tarmhf\t\t\tkishi00.buildd
            *\t\t*\t\ti386\t\t\tcardamom.buildd
            *\t\t*\t\tppc64el\t\t\tfisher01.buildd
            """), file=f)


class TestLiveBuilder(TestCase):
    def setUp(self):
        super(TestLiveBuilder, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        make_livefs_production_config(self.config)

    def assertBuilderEqual(self, expected, arch, series, project=None):
        self.config["DIST"] = series
        if project is not None:
            self.config["PROJECT"] = project
        self.assertEqual(expected, live_builder(self.config, arch))

    def test_amd64(self):
        for series in all_series:
            self.assertBuilderEqual("kapok.buildd", "amd64", series)

    def test_armel(self):
        for series in all_series:
            self.assertBuilderEqual("celbalrai.buildd", "armel", series)

    def test_arm64(self):
        for series in all_series:
            self.assertBuilderEqual("magic.buildd", "arm64", series)

    def test_armhf(self):
        for series in all_series:
            self.assertBuilderEqual(
                "kishi00.buildd", "armhf+mx5", series, project="ubuntu")
            self.assertBuilderEqual(
                "kishi00.buildd", "armhf+omap", series, project="ubuntu")
            self.assertBuilderEqual(
                "kishi00.buildd", "armhf+omap4", series, project="ubuntu")
            self.assertBuilderEqual(
                "kishi00.buildd", "armhf+omap", series,
                project="ubuntu-server")
            self.assertBuilderEqual(
                "celbalrai.buildd", "armhf+omap4", series,
                project="ubuntu-server")
            self.assertBuilderEqual("celbalrai.buildd", "armhf+ac100", series)
            self.assertBuilderEqual("celbalrai.buildd", "armhf+nexus7", series)
            self.assertBuilderEqual(
                "kishi00.buildd", "armhf+somethingelse", series)

    def test_i386(self):
        for series in all_series:
            self.assertBuilderEqual("cardamom.buildd", "i386", series)

    def test_ppc64el(self):
        for series in all_series:
            self.assertBuilderEqual("fisher01.buildd", "ppc64el", series)


class TestLiveBuildOptions(TestCase):
    def setUp(self):
        super(TestLiveBuildOptions, self).setUp()
        self.config = Config(read=False)

    def test_armel_preinstalled(self):
        self.config["IMAGE_TYPE"] = "daily-preinstalled"
        for subarch, fstype in (
            ("mx5", "ext4"),
            ("omap", "ext4"),
            ("omap4", "ext4"),
            ("ac100", "plain"),
            ("nexus7", "plain"),
        ):
            self.assertEqual(
                ["-f", fstype],
                live_build_options(self.config, "armel+%s" % subarch))
        self.assertEqual([], live_build_options(self.config, "armel+other"))

    def test_armhf_preinstalled(self):
        self.config["IMAGE_TYPE"] = "daily-preinstalled"
        for subarch, fstype in (
            ("mx5", "ext4"),
            ("omap", "ext4"),
            ("omap4", "ext4"),
            ("ac100", "plain"),
            ("nexus7", "plain"),
        ):
            self.assertEqual(
                ["-f", fstype],
                live_build_options(self.config, "armhf+%s" % subarch))
        self.assertEqual([], live_build_options(self.config, "armhf+other"))

    def test_ubuntu_core(self):
        self.config["PROJECT"] = "ubuntu-core"
        self.assertEqual(
            ["-f", "plain"], live_build_options(self.config, "i386"))

    def test_ubuntu_base(self):
        self.config["PROJECT"] = "ubuntu-base"
        self.assertEqual(
            ["-f", "plain"], live_build_options(self.config, "i386"))

    def test_wubi(self):
        self.config["SUBPROJECT"] = "wubi"
        for series, fstype in (
            ("bionic", "ext3"),  # ext4
        ):
            self.config["DIST"] = series
            self.assertEqual(
                ["-f", fstype], live_build_options(self.config, "i386"))


class TestLiveBuildCommand(TestCase):
    def setUp(self):
        super(TestLiveBuildCommand, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        make_livefs_production_config(self.config)
        self.base_expected = [
            "ssh", "-n", "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
        ]

    def contains_subsequence(self, haystack, needle):
        # This is inefficient, but it doesn't matter much here.
        for i in range(len(haystack) - len(needle) + 1):
            if haystack[i:i + len(needle)] == needle:
                return True
        return False

    def assertCommandContains(self, subsequence, arch):
        observed = live_build_command(self.config, arch)
        if not self.contains_subsequence(observed, subsequence):
            self.fail("%s does not contain %s" % (observed, subsequence))

    def test_basic(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        expected = self.base_expected + [
            "buildd@cardamom.buildd", "/home/buildd/bin/BuildLiveCD",
            "-l", "-A", "i386", "-d", "bionic", "ubuntu",
        ]
        self.assertEqual(expected, live_build_command(self.config, "i386"))

    def test_pre_live_build(self):
        self.config["DIST"] = "bionic"
        self.assertIn("-l", live_build_command(self.config, "i386"))

    @mock.patch(
        "cdimage.livefs.live_build_options", return_value=["-f", "plain"])
    def test_uses_live_build_options(self, *args):
        self.assertCommandContains(["-f", "plain"], "i386")

    def test_subarch(self):
        self.assertCommandContains(["-s", "omap4"], "armhf+omap4")

    def test_proposed(self):
        self.config["PROPOSED"] = "1"
        self.assertIn("-p", live_build_command(self.config, "i386"))

    def test_series(self):
        self.config["DIST"] = "bionic"
        self.assertCommandContains(["-d", "bionic"], "i386")

    def test_subproject(self):
        self.config["SUBPROJECT"] = "wubi"
        self.assertCommandContains(["-r", "wubi"], "i386")

    def test_project(self):
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            "kubuntu", live_build_command(self.config, "i386")[-1])


def mock_strftime(secs):
    original_strftime = time.strftime
    gmtime = time.gmtime(secs)
    return mock.patch(
        "time.strftime",
        side_effect=lambda fmt, *args: original_strftime(fmt, gmtime))


def mock_Popen(command):
    original_Popen = subprocess.Popen
    return mock.patch(
        "subprocess.Popen",
        side_effect=lambda *args, **kwargs: original_Popen(command))


def mock_urlopen(data):
    mock_obj = mock.MagicMock(name="urlopen", spec=urlopen)
    handle = mock.MagicMock(spec=["__enter__", "close", "read"])
    handle.__enter__.return_value = handle
    handle.read.return_value = data
    mock_obj.return_value = handle
    return mock_obj


class TestRunLiveBuilds(TestCase):
    def setUp(self):
        super(TestRunLiveBuilds, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        make_livefs_production_config(self.config)

    def test_live_build_full_name(self):
        self.config["PROJECT"] = "ubuntu"
        self.assertEqual(
            "ubuntu-i386", live_build_full_name(self.config, "i386"))
        self.assertEqual(
            "ubuntu-armhf-omap4",
            live_build_full_name(self.config, "armhf+omap4"))
        self.config["PROJECT"] = "kubuntu"
        self.config["SUBPROJECT"] = "wubi"
        self.assertEqual(
            "kubuntu-wubi-i386", live_build_full_name(self.config, "i386"))

    @mock.patch("cdimage.livefs.get_notify_addresses")
    def test_live_build_notify_failure_debug(self, mock_notify_addresses):
        self.config["DEBUG"] = "1"
        live_build_notify_failure(self.config, None)
        self.assertEqual(0, mock_notify_addresses.call_count)

    @mock.patch("cdimage.livefs.send_mail")
    def test_live_build_notify_failure_no_recipients(self, mock_send_mail):
        live_build_notify_failure(self.config, None)
        self.assertEqual(0, mock_send_mail.call_count)

    @mock.patch("time.strftime", return_value="20130315")
    @mock.patch("cdimage.livefs.urlopen", mock_urlopen(b""))
    @mock.patch("cdimage.livefs.send_mail")
    def test_live_build_notify_failure_no_log(self, mock_send_mail, *args):
        self.config.root = self.use_temp_dir()
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path) as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        live_build_notify_failure(self.config, "i386")
        mock_send_mail.assert_called_once_with(
            "LiveFS ubuntu/bionic/i386 failed to build on 20130315",
            "buildlive", ["foo@example.org"], b"")

    @mock.patch("time.strftime", return_value="20130315")
    @mock.patch("cdimage.livefs.send_mail")
    def test_live_build_notify_failure_log(self, mock_send_mail, *args):
        self.config["PROJECT"] = "kubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path) as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        mock_urlopen_obj = mock_urlopen(b"Log data\n")
        with mock.patch("cdimage.livefs.urlopen", mock_urlopen_obj):
            live_build_notify_failure(self.config, "armhf+omap4")
        mock_urlopen_obj.assert_called_once_with(
            "http://kishi00.buildd/~buildd/LiveCD/bionic/kubuntu-omap4/latest/"
            "livecd-armhf.out", timeout=30)
        mock_send_mail.assert_called_once_with(
            "LiveFS kubuntu-omap4/bionic/armhf+omap4 failed to build on "
            "20130315",
            "buildlive", ["foo@example.org"], b"Log data\n")

    @mock_strftime(1363355331)
    @mock.patch("cdimage.livefs.live_build_command", return_value=["false"])
    @mock.patch("cdimage.livefs.tracker_set_rebuild_status")
    @mock.patch("cdimage.livefs.send_mail")
    def test_run_live_builds_notifies_on_failure(self, mock_send_mail,
                                                 mock_tracker, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64 i386"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path) as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        self.capture_logging()
        with mock.patch("cdimage.livefs.urlopen", mock_urlopen(b"Log data\n")):
            self.assertRaisesRegex(
                LiveBuildsFailed, "No live filesystem builds succeeded.",
                run_live_builds, self.config)
        self.assertCountEqual([
            "ubuntu-amd64 on kapok.buildd starting at 2013-03-15 13:48:51",
            "ubuntu-i386 on cardamom.buildd starting at 2013-03-15 13:48:51",
            "ubuntu-amd64 on kapok.buildd finished at 2013-03-15 13:48:51 "
            "(failed)",
            "ubuntu-i386 on cardamom.buildd finished at 2013-03-15 13:48:51 "
            "(failed)",
        ], self.captured_log_messages())
        mock_send_mail.assert_has_calls([
            mock.call(
                "LiveFS ubuntu/bionic/amd64 failed to build on 20130315",
                "buildlive", ["foo@example.org"], b"Log data\n"),
            mock.call(
                "LiveFS ubuntu/bionic/i386 failed to build on 20130315",
                "buildlive", ["foo@example.org"], b"Log data\n"),
        ], any_order=True)
        mock_tracker.assert_has_calls([
            mock.call(self.config, [0, 1], 2, "amd64"),
            mock.call(self.config, [0, 1], 2, "i386"),
            mock.call(self.config, [0, 1, 2], 5, "amd64"),
            mock.call(self.config, [0, 1, 2], 5, "i386")
        ])

    @mock_strftime(1363355331)
    @mock.patch("cdimage.livefs.tracker_set_rebuild_status")
    @mock_Popen(["true"])
    @mock.patch("cdimage.livefs.live_build_notify_failure")
    def test_run_live_builds(self, mock_live_build_notify_failure, mock_popen,
                             mock_tracker_set_rebuild_status, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64 i386"
        self.capture_logging()
        self.assertCountEqual(["amd64", "i386"],
                              run_live_builds(self.config).keys())
        self.assertCountEqual([
            "ubuntu-amd64 on kapok.buildd starting at 2013-03-15 13:48:51",
            "ubuntu-i386 on cardamom.buildd starting at 2013-03-15 13:48:51",
            "ubuntu-amd64 on kapok.buildd finished at 2013-03-15 13:48:51 "
            "(success)",
            "ubuntu-i386 on cardamom.buildd finished at 2013-03-15 13:48:51 "
            "(success)",
        ], self.captured_log_messages())
        self.assertEqual(4, mock_tracker_set_rebuild_status.call_count)
        mock_tracker_set_rebuild_status.assert_has_calls([
            mock.call(self.config, [0, 1], 2, "amd64"),
            mock.call(self.config, [0, 1], 2, "i386"),
            mock.call(self.config, [0, 1, 2], 3, "amd64"),
            mock.call(self.config, [0, 1, 2], 3, "i386"),
        ])
        expected_command_base = [
            "ssh", "-n", "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
        ]
        mock_popen.assert_has_calls([
            mock.call(
                expected_command_base + [
                    "buildd@kapok.buildd", "/home/buildd/bin/BuildLiveCD",
                    "-l", "-A", "amd64", "-d", "bionic", "ubuntu",
                ]),
            mock.call(
                expected_command_base + [
                    "buildd@cardamom.buildd", "/home/buildd/bin/BuildLiveCD",
                    "-l", "-A", "i386", "-d", "bionic", "ubuntu",
                ])
        ])
        self.assertEqual(0, mock_live_build_notify_failure.call_count)

    @mock.patch("cdimage.livefs.tracker_set_rebuild_status")
    @mock_Popen(["true"])
    @mock.patch("cdimage.livefs.live_build_notify_failure")
    def test_run_live_builds_skips_amd64_mac(self,
                                             mock_live_build_notify_failure,
                                             mock_popen, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64"
        self.capture_logging()
        self.assertCountEqual(
            ["amd64"], run_live_builds(self.config).keys())
        expected_command = [
            "ssh", "-n", "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "buildd@kapok.buildd", "/home/buildd/bin/BuildLiveCD",
            "-l", "-A", "amd64", "-d", "bionic", "ubuntu",
        ]
        mock_popen.assert_called_once_with(expected_command)
        self.assertEqual(0, mock_live_build_notify_failure.call_count)

    @mock_strftime(1363355331)
    @mock.patch("cdimage.livefs.urlopen", mock_urlopen(b"Log data\n"))
    @mock.patch("cdimage.livefs.tracker_set_rebuild_status")
    @mock.patch("cdimage.livefs.send_mail")
    def test_run_live_builds_partial_success(self, mock_send_mail, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64 i386"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path) as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        self.capture_logging()
        original_Popen = subprocess.Popen
        with mock.patch("subprocess.Popen") as mock_popen:
            def Popen_side_effect(command, *args, **kwargs):
                if "amd64" in command:
                    return original_Popen(["true"])
                else:
                    return original_Popen(["false"])

            mock_popen.side_effect = Popen_side_effect
            self.assertCountEqual(
                ["amd64"],
                run_live_builds(self.config).keys())
            self.assertCountEqual([
                "ubuntu-amd64 on kapok.buildd starting at 2013-03-15 13:48:51",
                "ubuntu-i386 on cardamom.buildd starting at "
                "2013-03-15 13:48:51",
                "ubuntu-amd64 on kapok.buildd finished at "
                "2013-03-15 13:48:51 (success)",
                "ubuntu-i386 on cardamom.buildd finished at "
                "2013-03-15 13:48:51 (failed)",
            ], self.captured_log_messages())
            expected_command_base = [
                "ssh", "-n", "-o", "StrictHostKeyChecking=no",
                "-o", "BatchMode=yes",
            ]
            mock_popen.assert_has_calls([
                mock.call(
                    expected_command_base + [
                        "buildd@kapok.buildd", "/home/buildd/bin/BuildLiveCD",
                        "-l", "-A", "amd64", "-d", "bionic", "ubuntu",
                    ]),
                mock.call(
                    expected_command_base + [
                        "buildd@cardamom.buildd",
                        "/home/buildd/bin/BuildLiveCD",
                        "-l", "-A", "i386", "-d", "bionic", "ubuntu",
                    ]),
            ])
            mock_send_mail.assert_called_once_with(
                "LiveFS ubuntu/bionic/i386 failed to build on 20130315",
                "buildlive", ["foo@example.org"], b"Log data\n")

    @skipUnless(launchpad_available, "launchpadlib not available")
    @mock_strftime(1363355331)
    @mock.patch("time.sleep")
    @mock.patch("cdimage.livefs.tracker_set_rebuild_status")
    @mock.patch("cdimage.livefs.live_build_notify_failure")
    @mock.patch("cdimage.tests.test_livefs.MockLiveFSBuild._iter_buildstate")
    @mock.patch("cdimage.launchpad.login")
    def test_run_live_builds_lp(self, mock_login, mock_iter_buildstate,
                                mock_live_build_notify_failure,
                                mock_tracker_set_rebuild_status, mock_sleep,
                                *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64 i386"
        osextras.unlink_force(os.path.join(
            self.config.root, "production", "livefs-builders"))
        with mkfile(os.path.join(
                self.config.root, "production", "livefs-launchpad")) as f:
            print("*\t*\t*\t*\tubuntu-cdimage/ubuntu-desktop", file=f)
        self.capture_logging()
        mock_login.return_value = MockLaunchpad()
        mock_iter_buildstate.side_effect = lambda: (
            chain(["Needs building"] * 3, repeat("Successfully built")))
        self.assertCountEqual(["amd64", "i386"],
                              run_live_builds(self.config).keys())
        self.assertCountEqual([
            "ubuntu-amd64 on Launchpad starting at 2013-03-15 13:48:51",
            "ubuntu-amd64: https://launchpad.example/amd64-build",
            "ubuntu-i386 on Launchpad starting at 2013-03-15 13:48:51",
            "ubuntu-i386: https://launchpad.example/i386-build",
            "ubuntu-amd64 on Launchpad finished at 2013-03-15 13:48:51 "
            "(Successfully built)",
            "ubuntu-i386 on Launchpad finished at 2013-03-15 13:48:51 "
            "(Successfully built)",
        ], self.captured_log_messages())
        self.assertEqual(4, mock_tracker_set_rebuild_status.call_count)
        mock_tracker_set_rebuild_status.assert_has_calls([
            mock.call(self.config, [0, 1], 2, "amd64"),
            mock.call(self.config, [0, 1], 2, "i386"),
            mock.call(self.config, [0, 1, 2], 3, "amd64"),
            mock.call(self.config, [0, 1, 2], 3, "i386"),
        ])
        self.assertEqual(3, mock_sleep.call_count)
        mock_sleep.assert_has_calls([mock.call(15)] * 3)
        lp = get_launchpad()
        owner = lp.people["ubuntu-cdimage"]
        ubuntu = lp.distributions["ubuntu"]
        bionic = ubuntu.getSeries(name_or_version="bionic")
        dases = [
            bionic.getDistroArchSeries(archtag)
            for archtag in ("amd64", "i386")]
        self.assertEqual(2, len(dases))
        livefs = lp.livefses.getByName(
            owner=owner, distro_series=bionic, name="ubuntu-desktop")
        builds = [
            livefs.getLatestBuild(distro_arch_series=das) for das in dases]
        self.assertEqual(2, len(builds))
        self.assertEqual("Successfully built", builds[0].buildstate)
        self.assertEqual("Successfully built", builds[1].buildstate)
        self.assertEqual(0, mock_live_build_notify_failure.call_count)


class TestLiveCDBase(TestCase):
    def setUp(self):
        super(TestLiveCDBase, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        make_livefs_production_config(self.config)

    def assertBaseEqual(self, expected, arch, project, series, **kwargs):
        self.config["PROJECT"] = project
        self.config["DIST"] = series
        for key, value in kwargs.items():
            self.config[key.upper()] = value
        self.assertEqual(expected, livecd_base(self.config, arch))

    def base(self, builder, project, series):
        return "http://%s/~buildd/LiveCD/%s/%s/current" % (
            builder, series, project)

    def test_livecd_base_override(self):
        self.assertBaseEqual(
            "ftp://blah", "amd64", "ubuntu", "dapper",
            livecd_base="ftp://blah")

    def test_livecd_override(self):
        self.assertBaseEqual(
            "ftp://blah/bionic/ubuntu/current", "i386", "ubuntu", "bionic",
            livecd="ftp://blah")

    def test_subproject(self):
        for series in all_series:
            self.assertBaseEqual(
                self.base("cardamom.buildd", "ubuntu-wubi", series),
                "i386", "ubuntu", series, subproject="wubi")

    def test_no_subarch(self):
        for series in all_series:
            self.assertBaseEqual(
                self.base("cardamom.buildd", "ubuntu", series),
                "i386", "ubuntu", series)

    def test_subarch(self):
        self.assertBaseEqual(
            self.base("celbalrai.buildd", "ubuntu-server-omap", "bionic"),
            "armel+omap", "ubuntu-server", "bionic")


class TestDownloadLiveFilesystems(TestCase):
    def setUp(self):
        super(TestDownloadLiveFilesystems, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        make_livefs_production_config(self.config)

    def test_live_output_directory(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily-live"
        expected = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "bionic", "daily-live", "live")
        self.assertEqual(expected, live_output_directory(self.config))
        self.config.subtree = "subtree/test"
        expected = os.path.join(
            self.temp_dir, "scratch", "subtree", "test", "ubuntu", "bionic",
            "daily-live", "live")
        self.assertEqual(expected, live_output_directory(self.config))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_filesystems_ubuntu_live(self, mock_fetch):
        from cdimage.tests.test_build import mock_builds_for_config
        artifacts = (
            "squashfs", "kernel-generic", "kernel-generic.efi.signed",
            "initrd-generic", "manifest", "manifest-remove",
            "manifest-minimal-remove", "size",
            )
        artifacts_by_arch = {"amd64": list(artifacts), "i386": list(artifacts)}
        artifacts_by_arch['i386'].remove("kernel-generic.efi.signed")

        def fetch_side_effect(config, source, target):
            tail = os.path.basename(target).split(".", 1)[1]
            if tail in artifacts:
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.config["ARCHES"] = "amd64 i386"
        self.config["CDIMAGE_LIVE"] = "1"
        download_live_filesystems(
            self.config,
            mock_builds_for_config(
                self.config,
                artifact_names=artifacts_by_arch))
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "bionic", "daily-live", "live")
        self.assertCountEqual([
            "amd64.initrd-generic",
            "amd64.kernel-generic",
            "amd64.kernel-generic.efi.signed",
            "amd64.manifest",
            "amd64.manifest-remove",
            "amd64.manifest-minimal-remove",
            "amd64.size",
            "amd64.squashfs",
            "i386.initrd-generic",
            "i386.kernel-generic",
            "i386.manifest",
            "i386.manifest-remove",
            "i386.manifest-minimal-remove",
            "i386.size",
            "i386.squashfs",
        ], os.listdir(output_dir))

    def setupForDirectDownloads(self, project, built_artefacts):
        self.config["PROJECT"] = project
        self.config["DIST"] = "plucky"
        self.config["ARCHES"] = " ".join(list(built_artefacts))
        builds = {}
        for arch, names in built_artefacts.items():
            builds[arch] = build = MockLiveFSBuild()
            build.getFileUrls.return_value = [
                f"http://librarian.internal/xzy/{name}" for name in names
            ]
        return builds

    def assertDirectDownloadArtifacts(
        self, *, project, built_artefacts, expected_downloads
    ):
        builds = self.setupForDirectDownloads(project, built_artefacts)

        output_dir = live_output_directory(self.config).rstrip("/") + "/"
        downloads = []

        def mock_fetch(config, uri, target):
            downloads.append(target[len(output_dir):])

        def mock_sign(config, target):
            downloads.append(target[len(output_dir):] + ".gpg")

        with mock.patch("cdimage.osextras.fetch", mock_fetch):
            with mock.patch("cdimage.sign.sign_cdimage", mock_sign):
                got_builds = download_live_filesystems(self.config, builds)

        self.assertEqual(got_builds, builds)
        self.assertEqual(sorted(downloads), sorted(expected_downloads))

    def loadDataFile(self, path):
        fullpath = os.path.join(os.path.dirname(__file__), "data", path)
        with open(fullpath) as fp:
            return [line.strip() for line in fp]

    def test_simplified_ubuntu(self):
        # All builds are in practice a bit more complicated than
        # this. But it helps to have a simpler test case for ease of
        # understanding.
        self.config["CDIMAGE_LIVE"] = "1"
        self.assertDirectDownloadArtifacts(
            project="ubuntu",
            built_artefacts={
                "amd64": ["livecd.ubuntu.filesystem.squashfs"],
                "arm64": ["livecd.ubuntu.filesystem.squashfs"],
            },
            expected_downloads=[
                "amd64.filesystem.squashfs",
                "amd64.filesystem.squashfs.gpg",
                "arm64.filesystem.squashfs",
                "arm64.filesystem.squashfs.gpg",
            ],
        )

    def test_ubuntu_server_preinstalled(self):
        # Some preinstalled server livefs builds produce artifacts
        # (.ext4 and .filelist) which are not used, so we skip
        # downloading them.
        self.config["CDIMAGE_PREINSTALLED"] = "1"
        self.assertDirectDownloadArtifacts(
            project="ubuntu-server",
            built_artefacts={
                "amd64": [
                    "livecd.ubuntu-cpc-generic.ext4",
                    "livecd.ubuntu-cpc-generic.filelist",
                    "livecd.ubuntu-cpc-generic.initrd-generic",
                    "livecd.ubuntu-cpc-generic.kernel-generic",
                    "livecd.ubuntu-cpc-generic.manifest",
                    "livecd.ubuntu-cpc.disk1.img.xz",
                ],
                "arm64": [
                    "livecd.ubuntu-cpc-generic.ext4",
                    "livecd.ubuntu-cpc-generic.filelist",
                    "livecd.ubuntu-cpc-generic.initrd-generic",
                    "livecd.ubuntu-cpc-generic.kernel-generic",
                    "livecd.ubuntu-cpc-generic.manifest",
                    "livecd.ubuntu-cpc.disk1.img.xz",
                ],
                "arm64+raspi": [
                    "livecd.ubuntu-cpc-raspi.img.xz",
                    "livecd.ubuntu-cpc-raspi.manifest",
                ],
            },
            expected_downloads=[
                "amd64.disk1.img.xz",
                "amd64.initrd-generic",
                "amd64.kernel-generic",
                "amd64.manifest",
                "arm64+raspi.img.xz",
                "arm64+raspi.manifest",
                "arm64.disk1.img.xz",
                "arm64.initrd-generic",
                "arm64.kernel-generic",
                "arm64.manifest",
            ],
        )

    def test_ubuntu_mini_iso(self):
        # The mini iso build creates a rootfs.tar.gz artifact (for
        # now). Do not download it.
        self.config["CDIMAGE_LIVE"] = "1"
        self.assertDirectDownloadArtifacts(
            project="ubuntu-mini-iso",
            built_artefacts={
                "amd64": [
                    "livecd.ubuntu-mini-iso.iso",
                    "livecd.ubuntu-mini-iso.manifest",
                    "livecd.ubuntu-mini-iso.rootfs.tar.gz",
                ],
            },
            expected_downloads=[
                "amd64.iso",
                "amd64.manifest",
            ],
        )

    def test_full_ubuntu(self):
        # This test case is a record of the existing behaviour of a
        # build of the "ubuntu" project (specifically the 20240114
        # build for plucky) for regression testing.
        self.config["CDIMAGE_LIVE"] = "1"
        self.assertDirectDownloadArtifacts(
            project="ubuntu",
            built_artefacts={
                "amd64": self.loadDataFile("livefses/ubuntu-amd64-artifacts"),
                "arm64": self.loadDataFile("livefses/ubuntu-arm64-artifacts"),
                },
            expected_downloads=self.loadDataFile("livefses/ubuntu-downloads"),
            )

    def test_failed_download(self):
        self.config["CDIMAGE_LIVE"] = "1"
        builds = self.setupForDirectDownloads(
            project="ubuntu",
            built_artefacts={
                "amd64": [
                    "livecd.ubuntu.download-ok",
                    "livecd.ubuntu.download-fail",
                    ],
                "arm64": ["livecd.ubuntu.download-ok"],
            },
        )

        def mock_fetch(config, uri, target):
            if 'download-ok' in uri:
                return
            elif 'download-fail' in uri:
                raise osextras.FetchError
            else:
                self.fail("mock_fetch got unexpected uri: %r" % (uri,))

        with mock.patch("cdimage.osextras.fetch", mock_fetch):
            with mock.patch(
                    "cdimage.livefs.live_build_notify_download_failure"
                    ) as lbndf:
                new_builds = download_live_filesystems(self.config, builds)
                lbndf.assert_called_once()

        self.assertEqual(
            new_builds,
            {'arm64': builds['arm64']})
