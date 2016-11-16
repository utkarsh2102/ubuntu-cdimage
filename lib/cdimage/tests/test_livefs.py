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
    download_live_items,
    flavours,
    live_build_command,
    live_build_full_name,
    live_build_notify_failure,
    live_build_options,
    live_builder,
    live_item_paths,
    live_output_directory,
    live_project,
    livecd_base,
    run_live_builds,
    split_arch,
    write_autorun,
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
        self.assertEqual(("amd64", ""), split_arch("amd64"))

    def test_amd64_mac(self):
        self.assertEqual(("amd64", ""), split_arch("amd64+mac"))

    def test_armhf_omap4(self):
        self.assertEqual(("armhf", "omap4"), split_arch("armhf+omap4"))

    def test_i386(self):
        self.assertEqual(("i386", ""), split_arch("i386"))


class TestLiveProject(TestCase):
    def assertProjectEqual(self, expected, project, series, arch="i386",
                           **kwargs):
        config = Config(read=False)
        config["PROJECT"] = project
        config["DIST"] = series
        for key, value in kwargs.items():
            config[key.upper()] = value
        self.assertEqual(expected, live_project(config, arch))

    def test_project_livecd_base(self):
        self.assertProjectEqual("base", "livecd-base", "dapper")

    def test_project_tocd3_1(self):
        self.assertProjectEqual("tocd", "tocd3.1", "breezy")

    def test_project_ubuntu_touch_custom(self):
        self.assertProjectEqual("ubuntu-touch", "ubuntu-touch-custom", "vivid")

    def test_ubuntu_dvd(self):
        for series in all_series[:7]:
            self.assertProjectEqual(
                "ubuntu", "ubuntu", series, cdimage_dvd="1")
        for series in all_series[7:]:
            self.assertProjectEqual(
                "ubuntu-dvd", "ubuntu", series, cdimage_dvd="1")

    def test_kubuntu_dvd(self):
        for series in all_series[:7]:
            self.assertProjectEqual(
                "kubuntu", "kubuntu", series, cdimage_dvd="1")
        for series in all_series[7:]:
            self.assertProjectEqual(
                "kubuntu-dvd", "kubuntu", series, cdimage_dvd="1")

    def test_edubuntu_dvd(self):
        for series in all_series[:10]:
            self.assertProjectEqual(
                "edubuntu", "edubuntu", series, cdimage_dvd="1")
        for series in all_series[10:]:
            self.assertProjectEqual(
                "edubuntu-dvd", "edubuntu", series, cdimage_dvd="1")

    def test_ubuntustudio_dvd(self):
        for series in all_series[:15]:
            self.assertProjectEqual(
                "ubuntustudio", "ubuntustudio", series, cdimage_dvd="1")
        for series in all_series[15:]:
            self.assertProjectEqual(
                "ubuntustudio-dvd", "ubuntustudio", series, cdimage_dvd="1")

    def test_lpia(self):
        self.assertProjectEqual("ubuntu-lpia", "ubuntu", "hardy", arch="lpia")
        self.assertProjectEqual("ubuntu", "ubuntu", "intrepid", arch="lpia")


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
            *\t\t*\t\thppa\t\t\tcastilla.buildd
            *\t\t*\t\ti386\t\t\tcardamom.buildd
            *\t\t*\t\tia64\t\t\tweddell.buildd
            *\t\t-hardy\t\tlpia\t\t\tcardamom.buildd
            *\t\t*\t\tlpia\t\t\tconcordia.buildd
            *\t\t*\t\tpowerpc\t\t\troyal.buildd
            *\t\t*\t\tppc64el\t\t\tfisher01.buildd
            *\t\t*\t\tsparc\t\t\tvivies.buildd
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

    def test_hppa(self):
        for series in all_series:
            self.assertBuilderEqual("castilla.buildd", "hppa", series)

    def test_i386(self):
        for series in all_series:
            self.assertBuilderEqual("cardamom.buildd", "i386", series)

    def test_ia64(self):
        for series in all_series:
            self.assertBuilderEqual("weddell.buildd", "ia64", series)

    def test_lpia(self):
        for series in all_series[:8]:
            self.assertBuilderEqual("cardamom.buildd", "lpia", series)
        for series in all_series[8:]:
            self.assertBuilderEqual("concordia.buildd", "lpia", series)

    def test_powerpc(self):
        for series in all_series:
            self.assertBuilderEqual("royal.buildd", "powerpc", series)

    def test_ppc64el(self):
        for series in all_series:
            self.assertBuilderEqual("fisher01.buildd", "ppc64el", series)

    def test_sparc(self):
        for series in all_series:
            self.assertBuilderEqual("vivies.buildd", "sparc", series)


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

    def test_ubuntu_touch(self):
        self.config["PROJECT"] = "ubuntu-touch"
        self.assertEqual(
            ["-f", "plain"], live_build_options(self.config, "armhf"))

    def test_ubuntu_touch_custom(self):
        self.config["PROJECT"] = "ubuntu-touch-custom"
        self.assertEqual(
            ["-f", "plain"], live_build_options(self.config, "armhf"))

    def test_wubi(self):
        self.config["SUBPROJECT"] = "wubi"
        for series, fstype in (
            ("precise", "ext3"),
            ("quantal", "ext3"),  # ext4
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
        self.config["DIST"] = "raring"
        expected = self.base_expected + [
            "buildd@cardamom.buildd", "/home/buildd/bin/BuildLiveCD",
            "-l", "-A", "i386", "-d", "raring", "ubuntu",
        ]
        self.assertEqual(expected, live_build_command(self.config, "i386"))

    def test_ubuntu_defaults_locale(self):
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        self.assertCommandContains(["-u", "zh_CN"], "i386")

    def test_pre_live_build(self):
        self.config["DIST"] = "natty"
        self.assertNotIn("-l", live_build_command(self.config, "i386"))

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
        self.config["DIST"] = "precise"
        self.assertCommandContains(["-d", "precise"], "i386")

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
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path) as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        live_build_notify_failure(self.config, "i386")
        mock_send_mail.assert_called_once_with(
            "LiveFS ubuntu/raring/i386 failed to build on 20130315",
            "buildlive", ["foo@example.org"], b"")

    @mock.patch("time.strftime", return_value="20130315")
    @mock.patch("cdimage.livefs.send_mail")
    def test_live_build_notify_failure_log(self, mock_send_mail, *args):
        self.config["PROJECT"] = "kubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path) as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        mock_urlopen_obj = mock_urlopen(b"Log data\n")
        with mock.patch("cdimage.livefs.urlopen", mock_urlopen_obj):
            live_build_notify_failure(self.config, "armhf+omap4")
        mock_urlopen_obj.assert_called_once_with(
            "http://kishi00.buildd/~buildd/LiveCD/raring/kubuntu-omap4/latest/"
            "livecd-armhf.out", timeout=30)
        mock_send_mail.assert_called_once_with(
            "LiveFS kubuntu-omap4/raring/armhf+omap4 failed to build on "
            "20130315",
            "buildlive", ["foo@example.org"], b"Log data\n")

    @mock_strftime(1363355331)
    @mock.patch("cdimage.livefs.tracker_set_rebuild_status")
    @mock.patch("cdimage.livefs.live_build_command", return_value=["false"])
    @mock.patch("cdimage.livefs.send_mail")
    def test_run_live_builds_notifies_on_failure(self, mock_send_mail, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
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
                "LiveFS ubuntu/raring/amd64 failed to build on 20130315",
                "buildlive", ["foo@example.org"], b"Log data\n"),
            mock.call(
                "LiveFS ubuntu/raring/i386 failed to build on 20130315",
                "buildlive", ["foo@example.org"], b"Log data\n"),
        ], any_order=True)

    @mock_strftime(1363355331)
    @mock.patch("cdimage.livefs.tracker_set_rebuild_status")
    @mock_Popen(["true"])
    @mock.patch("cdimage.livefs.live_build_notify_failure")
    def test_run_live_builds(self, mock_live_build_notify_failure, mock_popen,
                             mock_tracker_set_rebuild_status, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64 i386"
        self.capture_logging()
        self.assertCountEqual(["amd64", "i386"], run_live_builds(self.config))
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
                    "-l", "-A", "amd64", "-d", "raring", "ubuntu",
                ]),
            mock.call(
                expected_command_base + [
                    "buildd@cardamom.buildd", "/home/buildd/bin/BuildLiveCD",
                    "-l", "-A", "i386", "-d", "raring", "ubuntu",
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
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64 amd64+mac"
        self.capture_logging()
        self.assertCountEqual(
            ["amd64", "amd64+mac"], run_live_builds(self.config))
        expected_command = [
            "ssh", "-n", "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "buildd@kapok.buildd", "/home/buildd/bin/BuildLiveCD",
            "-l", "-A", "amd64", "-d", "raring", "ubuntu",
        ]
        mock_popen.assert_called_once_with(expected_command)
        self.assertEqual(0, mock_live_build_notify_failure.call_count)

    @mock_strftime(1363355331)
    @mock.patch("cdimage.livefs.urlopen", mock_urlopen(b"Log data\n"))
    @mock.patch("cdimage.livefs.tracker_set_rebuild_status")
    @mock.patch("cdimage.livefs.send_mail")
    def test_run_live_builds_partial_success(self, mock_send_mail, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
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
            self.assertCountEqual(["amd64"], run_live_builds(self.config))
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
                        "-l", "-A", "amd64", "-d", "raring", "ubuntu",
                    ]),
                mock.call(
                    expected_command_base + [
                        "buildd@cardamom.buildd",
                        "/home/buildd/bin/BuildLiveCD",
                        "-l", "-A", "i386", "-d", "raring", "ubuntu",
                    ]),
            ])
            mock_send_mail.assert_called_once_with(
                "LiveFS ubuntu/raring/i386 failed to build on 20130315",
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
        self.config["DIST"] = "utopic"
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
        self.assertCountEqual(["amd64", "i386"], run_live_builds(self.config))
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
        utopic = ubuntu.getSeries(name_or_version="utopic")
        dases = [
            utopic.getDistroArchSeries(archtag)
            for archtag in ("amd64", "i386")]
        self.assertEqual(2, len(dases))
        livefs = lp.livefses.getByName(
            owner=owner, distro_series=utopic, name="ubuntu-desktop")
        builds = [
            livefs.getLatestBuild(distro_arch_series=das) for das in dases]
        self.assertEqual(2, len(builds))
        self.assertEqual("Successfully built", builds[0].buildstate)
        self.assertEqual("Successfully built", builds[1].buildstate)
        self.assertEqual(0, mock_live_build_notify_failure.call_count)

    @skipUnless(launchpad_available, "launchpadlib not available")
    @mock_strftime(1363355331)
    @mock.patch("time.sleep")
    @mock.patch("cdimage.livefs.tracker_set_rebuild_status")
    @mock.patch("cdimage.livefs.live_build_notify_failure")
    @mock.patch("cdimage.tests.test_livefs.MockLiveFSBuild._iter_buildstate")
    @mock.patch("cdimage.launchpad.login")
    def test_run_live_builds_lp_ubuntu_rtm(self, mock_login,
                                           mock_iter_buildstate,
                                           mock_live_build_notify_failure,
                                           mock_tracker_set_rebuild_status,
                                           mock_sleep, *args):
        self.config["PROJECT"] = "ubuntu-touch"
        self.config["DIST"] = "ubuntu-rtm/14.09"
        self.config["IMAGE_TYPE"] = "daily-preinstalled"
        self.config["ARCHES"] = "armhf i386"
        osextras.unlink_force(os.path.join(
            self.config.root, "production", "livefs-builders"))
        with mkfile(os.path.join(
                self.config.root, "production", "livefs-launchpad")) as f:
            print(
                "ubuntu-touch\tdaily-preinstalled\tubuntu-rtm/*\t*\t"
                "dogfood/ubuntu-cdimage/ubuntu-touch", file=f)
            print(
                "ubuntu-touch\tdaily-preinstalled\t*\t*\t"
                "ubuntu-cdimage/ubuntu-touch", file=f)
        self.capture_logging()
        mock_login.return_value = MockLaunchpad()
        mock_iter_buildstate.side_effect = lambda: (
            chain(["Needs building"] * 3, repeat("Successfully built")))
        self.assertCountEqual(["armhf", "i386"], run_live_builds(self.config))
        self.assertCountEqual([
            "ubuntu-touch-armhf on Launchpad starting at 2013-03-15 13:48:51",
            "ubuntu-touch-armhf: https://launchpad.example/armhf-build",
            "ubuntu-touch-i386 on Launchpad starting at 2013-03-15 13:48:51",
            "ubuntu-touch-i386: https://launchpad.example/i386-build",
            "ubuntu-touch-armhf on Launchpad finished at 2013-03-15 13:48:51 "
            "(Successfully built)",
            "ubuntu-touch-i386 on Launchpad finished at 2013-03-15 13:48:51 "
            "(Successfully built)",
        ], self.captured_log_messages())
        self.assertEqual(4, mock_tracker_set_rebuild_status.call_count)
        mock_tracker_set_rebuild_status.assert_has_calls([
            mock.call(self.config, [0, 1], 2, "armhf"),
            mock.call(self.config, [0, 1], 2, "i386"),
            mock.call(self.config, [0, 1, 2], 3, "armhf"),
            mock.call(self.config, [0, 1, 2], 3, "i386"),
        ])
        self.assertEqual(3, mock_sleep.call_count)
        mock_sleep.assert_has_calls([mock.call(15)] * 3)
        lp = get_launchpad()
        owner = lp.people["ubuntu-cdimage"]
        ubuntu_rtm = lp.distributions["ubuntu-rtm"]
        series = ubuntu_rtm.getSeries(name_or_version="14.09")
        dases = [
            series.getDistroArchSeries(archtag)
            for archtag in ("armhf", "i386")]
        self.assertEqual(2, len(dases))
        livefs = lp.livefses.getByName(
            owner=owner, distro_series=series, name="ubuntu-touch")
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
            "ftp://blah/quantal/ubuntu/current", "i386", "ubuntu", "quantal",
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
            self.base("royal.buildd", "ubuntu-ps3", "gutsy"),
            "powerpc+ps3", "ubuntu", "gutsy")
        self.assertBaseEqual(
            self.base("celbalrai.buildd", "ubuntu-server-omap", "oneiric"),
            "armel+omap", "ubuntu-server", "oneiric")

    def test_ubuntu_defaults_locale(self):
        for series in all_series:
            self.assertBaseEqual(
                self.base("cardamom.buildd", "ubuntu-zh_CN", series),
                "i386", "ubuntu", series, ubuntu_defaults_locale="zh_CN")


class TestFlavours(TestCase):
    def assertFlavoursEqual(self, expected, arch, project, series):
        config = Config(read=False)
        config["PROJECT"] = project
        config["DIST"] = series
        self.assertEqual(expected.split(), flavours(config, arch))

    def test_amd64(self):
        for series in all_series[:4]:
            self.assertFlavoursEqual(
                "amd64-generic", "amd64", "ubuntu", series)
        for series in all_series[4:]:
            self.assertFlavoursEqual(
                "generic", "amd64", "ubuntu", series)
        for series in all_series[15:]:
            self.assertFlavoursEqual(
                "lowlatency", "amd64", "ubuntustudio", series)

    def test_armel(self):
        self.assertFlavoursEqual("imx51", "armel+imx51", "ubuntu", "jaunty")
        self.assertFlavoursEqual("imx51", "armel+omap", "ubuntu", "jaunty")
        for series in all_series[10:]:
            self.assertFlavoursEqual(
                "linaro-lt-mx5", "armel+mx5", "ubuntu", series)
            self.assertFlavoursEqual("omap", "armel+omap", "ubuntu", series)

    def test_armhf(self):
        for series in all_series:
            self.assertFlavoursEqual(
                "linaro-lt-mx5", "armhf+mx5", "ubuntu", series)
            self.assertFlavoursEqual("omap4", "armhf+omap4", "ubuntu", series)

    def test_hppa(self):
        for series in all_series:
            self.assertFlavoursEqual("hppa32 hppa64", "hppa", "ubuntu", series)

    def test_i386(self):
        for series in all_series[:4]:
            self.assertFlavoursEqual("i386", "i386", "ubuntu", series)
        for series in all_series[4:15] + all_series[17:]:
            self.assertFlavoursEqual("generic", "i386", "ubuntu", series)
        self.assertFlavoursEqual("generic", "i386", "ubuntu", "precise")
        for series in all_series[4:]:
            self.assertFlavoursEqual("generic", "i386", "xubuntu", series)
            self.assertFlavoursEqual("generic", "i386", "lubuntu", series)
        self.assertFlavoursEqual(
            "lowlatency-pae", "i386", "ubuntustudio", "precise")
        for series in all_series[16:]:
            self.assertFlavoursEqual(
                "lowlatency", "i386", "ubuntustudio", series)

    def test_ia64(self):
        for series in all_series[:4]:
            self.assertFlavoursEqual(
                "itanium-smp mckinley-smp", "ia64", "ubuntu", series)
        for series in all_series[4:10]:
            self.assertFlavoursEqual(
                "itanium mckinley", "ia64", "ubuntu", series)
        for series in all_series[10:]:
            self.assertFlavoursEqual("ia64", "ia64", "ubuntu", series)

    def test_lpia(self):
        for series in all_series:
            self.assertFlavoursEqual("lpia", "lpia", "ubuntu", series)

    def test_powerpc(self):
        for series in all_series[:15]:
            self.assertFlavoursEqual(
                "powerpc powerpc64-smp", "powerpc", "ubuntu", series)
        for series in all_series[15:24]:
            self.assertFlavoursEqual(
                "powerpc-smp powerpc64-smp", "powerpc", "ubuntu", series)
        for series in all_series[24:]:
            self.assertFlavoursEqual(
                "powerpc-smp generic", "powerpc", "ubuntu", series)
        self.assertFlavoursEqual("cell", "powerpc+ps3", "ubuntu", "gutsy")
        for series in all_series[7:15]:
            self.assertFlavoursEqual(
                "powerpc powerpc64-smp", "powerpc+ps3", "ubuntu", "hardy")

    def test_ppc64el(self):
        for series in all_series:
            self.assertFlavoursEqual("generic", "ppc64el", "ubuntu", series)

    def test_s390x(self):
        for series in all_series:
            self.assertFlavoursEqual("generic", "s390x", "ubuntu", series)

    def test_sparc(self):
        for series in all_series:
            self.assertFlavoursEqual("sparc64", "sparc", "ubuntu", series)


class TestLiveItemPaths(TestCase):
    def setUp(self):
        super(TestLiveItemPaths, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        make_livefs_production_config(self.config)

    def assertPathsEqual(self, expected, arch, item, project, series):
        self.config["PROJECT"] = project
        self.config["DIST"] = series
        self.assertEqual(
            expected, list(live_item_paths(self.config, arch, item)))

    def assertNoPaths(self, arch, item, project, series):
        self.config["PROJECT"] = project
        self.config["DIST"] = series
        self.assertEqual([], list(live_item_paths(self.config, arch, item)))

    def test_tocd3_fallback(self):
        for item in ("cloop", "manifest"):
            self.assertPathsEqual(
                ["/home/cjwatson/tocd3/livecd.tocd3.%s" % item],
                "i386", item, "tocd3", "hoary")

    def test_ubuntu_breezy_fallback(self):
        for item in ("cloop", "manifest"):
            for arch in ("amd64", "i386", "powerpc"):
                self.assertPathsEqual(
                    ["/home/cjwatson/breezy-live/ubuntu/livecd.%s.%s" %
                     (arch, item)],
                    arch, item, "ubuntu", "breezy")

    def test_desktop_items(self):
        for item in (
            "cloop", "squashfs", "manifest", "manifest-desktop",
            "manifest-remove", "size", "ext2", "ext3", "ext4", "rootfs.tar.gz",
            "custom.tar.gz", "tar.xz", "iso", "device.tar.gz",
            "azure.device.tar.gz", "plano.device.tar.gz",
            "raspi2.device.tar.gz",
        ):
            self.assertPathsEqual(
                ["http://kapok.buildd/~buildd/LiveCD/precise/kubuntu/"
                 "current/livecd.kubuntu.%s" % item],
                "amd64", item, "kubuntu", "precise")
            self.assertPathsEqual(
                ["http://royal.buildd/~buildd/LiveCD/hardy/ubuntu-ps3/"
                 "current/livecd.ubuntu-ps3.%s" % item],
                "powerpc+ps3", item, "ubuntu", "hardy")

    def test_kernel_items(self):
        for item in ("kernel", "initrd", "bootimg"):
            root = "http://kapok.buildd/~buildd/LiveCD/precise/kubuntu/current"
            self.assertPathsEqual(
                ["%s/livecd.kubuntu.%s-generic" % (root, item)],
                "amd64", item, "kubuntu", "precise")
            root = ("http://royal.buildd/~buildd/LiveCD/hardy/ubuntu-ps3/"
                    "current")
            self.assertPathsEqual(
                ["%s/livecd.ubuntu-ps3.%s-powerpc" % (root, item),
                 "%s/livecd.ubuntu-ps3.%s-powerpc64-smp" % (root, item)],
                "powerpc+ps3", item, "ubuntu", "hardy")

    def test_kernel_efi_signed(self):
        self.assertNoPaths("i386", "kernel-efi-signed", "ubuntu", "quantal")
        self.assertNoPaths("amd64", "kernel-efi-signed", "ubuntu", "oneiric")
        root = "http://kapok.buildd/~buildd/LiveCD/precise/ubuntu/current"
        self.assertPathsEqual(
            ["%s/livecd.ubuntu.kernel-generic.efi.signed" % root],
            "amd64", "kernel-efi-signed", "ubuntu", "precise")
        root = "http://kapok.buildd/~buildd/LiveCD/quantal/ubuntu/current"
        self.assertPathsEqual(
            ["%s/livecd.ubuntu.kernel-generic.efi.signed" % root],
            "amd64", "kernel-efi-signed", "ubuntu", "quantal")

    # TODO: Since this is only of historical interest, we only test a small
    # number of cases at the moment.
    def test_winfoss(self):
        self.assertNoPaths("i386", "winfoss", "ubuntu", "warty")
        self.assertNoPaths("powerpc", "winfoss", "ubuntu", "hardy")
        self.assertPathsEqual(
            ["http://people.canonical.com/~henrik/winfoss/gutsy/"
             "ubuntu/current/ubuntu-winfoss-7.10.tar.gz"],
            "i386", "winfoss", "ubuntu", "karmic")
        self.assertNoPaths("i386", "winfoss", "ubuntu", "precise")

    def test_wubi(self):
        for series in all_series[:6]:
            self.assertNoPaths("amd64", "wubi", "ubuntu", series)
            self.assertNoPaths("i386", "wubi", "ubuntu", series)
        for series in all_series[6:]:
            path = ("http://people.canonical.com/~ubuntu-archive/wubi/%s/"
                    "stable" % series)
            self.assertPathsEqual([path], "amd64", "wubi", "ubuntu", series)
            self.assertPathsEqual([path], "i386", "wubi", "ubuntu", series)
        self.assertNoPaths("i386", "wubi", "xubuntu", "precise")
        self.assertNoPaths("powerpc", "wubi", "ubuntu", "precise")

    def test_umenu(self):
        for series in all_series[:7] + all_series[8:]:
            self.assertNoPaths("amd64", "umenu", "ubuntu", series)
            self.assertNoPaths("i386", "umenu", "ubuntu", series)
        path = "http://people.canonical.com/~evand/umenu/stable"
        self.assertPathsEqual([path], "amd64", "umenu", "ubuntu", "hardy")
        self.assertPathsEqual([path], "i386", "umenu", "ubuntu", "hardy")
        self.assertNoPaths("powerpc", "umenu", "ubuntu", "hardy")

    def test_usb_creator(self):
        for series in all_series:
            path = ("http://people.canonical.com/~evand/usb-creator/%s/"
                    "stable" % series)
            self.assertPathsEqual(
                [path], "amd64", "usb-creator", "ubuntu", series)
            self.assertPathsEqual(
                [path], "i386", "usb-creator", "ubuntu", series)
        self.assertNoPaths("powerpc", "usb-creator", "ubuntu", "precise")

    def test_ltsp_squashfs(self):
        for series in all_series:
            path = ("http://cardamom.buildd/~buildd/LiveCD/%s/edubuntu/"
                    "current/livecd.edubuntu-ltsp.squashfs" % series)
            self.assertPathsEqual(
                [path], "amd64", "ltsp-squashfs", "edubuntu", series)
            self.assertPathsEqual(
                [path], "i386", "ltsp-squashfs", "edubuntu", series)
        self.assertNoPaths("powerpc", "ltsp-squashfs", "edubuntu", "precise")


class TestDownloadLiveFilesystems(TestCase):
    def setUp(self):
        super(TestDownloadLiveFilesystems, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        make_livefs_production_config(self.config)

    def test_live_output_directory(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        expected = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live", "live")
        self.assertEqual(expected, live_output_directory(self.config))
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        expected = os.path.join(
            self.temp_dir, "scratch", "ubuntu-zh_CN", "raring", "daily-live",
            "live")
        self.assertEqual(expected, live_output_directory(self.config))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_no_item(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.assertFalse(download_live_items(self.config, "powerpc", "umenu"))
        self.assertEqual(0, mock_fetch.call_count)

    @mock.patch("cdimage.osextras.fetch", side_effect=osextras.FetchError)
    def test_download_live_items_failed_fetch(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.assertFalse(download_live_items(self.config, "i386", "squashfs"))
        mock_fetch.assert_called_once_with(
            self.config,
            "http://cardamom.buildd/~buildd/LiveCD/raring/ubuntu/current/"
            "livecd.ubuntu.squashfs",
            os.path.join(
                self.temp_dir, "scratch", "ubuntu", "raring", "daily-live",
                "live", "i386.squashfs"))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_kernel(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "quantal"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.assertTrue(download_live_items(self.config, "powerpc", "kernel"))
        prefix = ("http://royal.buildd/~buildd/LiveCD/quantal/ubuntu/current/"
                  "livecd.ubuntu.kernel-")
        target_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "quantal", "daily-live",
            "live")
        mock_fetch.assert_has_calls([
            mock.call(
                self.config, prefix + "powerpc-smp",
                os.path.join(target_dir, "powerpc.kernel-powerpc-smp")),
            mock.call(
                self.config, prefix + "powerpc64-smp",
                os.path.join(target_dir, "powerpc.kernel-powerpc64-smp")),
        ])

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_diskimg(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu-server"
        self.config["DIST"] = "xenial"
        self.config["IMAGE_TYPE"] = "daily-preinstalled"
        self.assertTrue(download_live_items(self.config, "armhf+raspi2",
                                            "disk1.img.xz"))
        mock_fetch.assert_called_once_with(
            self.config,
            "http://kishi00.buildd/~buildd/LiveCD/xenial/ubuntu-cpc-raspi2"
            "/current/livecd.ubuntu-cpc.disk1.img.xz",
            os.path.join(
                self.temp_dir, "scratch", "ubuntu-server", "xenial",
                "daily-preinstalled", "live", "armhf+raspi2.disk1.img.xz"))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_initrd(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.assertTrue(download_live_items(self.config, "i386", "kernel"))
        prefix = ("http://cardamom.buildd/~buildd/LiveCD/raring/ubuntu/"
                  "current/livecd.ubuntu.kernel-")
        target_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live", "live")
        mock_fetch.assert_called_once_with(
            self.config, prefix + "generic",
            os.path.join(target_dir, "i386.kernel-generic"))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_kernel_efi_signed(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.assertTrue(
            download_live_items(self.config, "amd64", "kernel-efi-signed"))
        prefix = ("http://kapok.buildd/~buildd/LiveCD/raring/ubuntu/"
                  "current/livecd.ubuntu.kernel-")
        target_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live", "live")
        mock_fetch.assert_called_once_with(
            self.config, prefix + "generic.efi.signed",
            os.path.join(target_dir, "amd64.kernel-generic.efi.signed"))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_bootimg(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-preinstalled"
        self.assertTrue(
            download_live_items(self.config, "armhf+omap4", "bootimg"))
        url = ("http://kishi00.buildd/~buildd/LiveCD/raring/ubuntu-omap4/"
               "current/livecd.ubuntu-omap4.bootimg-omap4")
        target_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-preinstalled",
            "live")
        mock_fetch.assert_called_once_with(
            self.config, url,
            os.path.join(target_dir, "armhf+omap4.bootimg-omap4"))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_wubi(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.assertTrue(download_live_items(self.config, "i386", "wubi"))
        url = "http://people.canonical.com/~ubuntu-archive/wubi/raring/stable"
        target_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live", "live")
        mock_fetch.assert_called_once_with(
            self.config, url, os.path.join(target_dir, "i386.wubi.exe"))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_umenu(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "hardy"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.assertTrue(download_live_items(self.config, "i386", "umenu"))
        url = "http://people.canonical.com/~evand/umenu/stable"
        target_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "hardy", "daily-live", "live")
        mock_fetch.assert_called_once_with(
            self.config, url, os.path.join(target_dir, "i386.umenu.exe"))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_usb_creator(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.assertTrue(
            download_live_items(self.config, "i386", "usb-creator"))
        url = "http://people.canonical.com/~evand/usb-creator/raring/stable"
        target_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live", "live")
        mock_fetch.assert_called_once_with(
            self.config, url, os.path.join(target_dir, "i386.usb-creator.exe"))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_winfoss(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "gutsy"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.assertTrue(download_live_items(self.config, "i386", "winfoss"))
        url = ("http://people.canonical.com/~henrik/winfoss/gutsy/ubuntu/"
               "current/ubuntu-winfoss-7.10.tar.gz")
        target_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "gutsy", "daily-live", "live")
        mock_fetch.assert_called_once_with(
            self.config, url, os.path.join(target_dir, "i386.winfoss.tgz"))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_squashfs(self, mock_fetch):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.assertTrue(download_live_items(self.config, "i386", "squashfs"))
        url = ("http://cardamom.buildd/~buildd/LiveCD/raring/ubuntu/"
               "current/livecd.ubuntu.squashfs")
        target_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live", "live")
        mock_fetch.assert_called_once_with(
            self.config, url, os.path.join(target_dir, "i386.squashfs"))

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_items_server_squashfs(self, mock_fetch):
        self.config["PROJECT"] = "edubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "dvd"
        self.assertTrue(
            download_live_items(self.config, "i386", "server-squashfs"))
        url = ("http://cardamom.buildd/~buildd/LiveCD/raring/ubuntu-server/"
               "current/livecd.ubuntu-server.squashfs")
        target_dir = os.path.join(
            self.temp_dir, "scratch", "edubuntu", "raring", "dvd", "live")
        mock_fetch.assert_called_once_with(
            self.config, url, os.path.join(target_dir, "i386.server-squashfs"))

    def test_write_autorun(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live", "live")
        os.makedirs(output_dir)
        write_autorun(self.config, "i386", "wubi.exe", "Install Ubuntu")
        autorun_path = os.path.join(output_dir, "i386.autorun.inf")
        self.assertTrue(os.path.exists(autorun_path))
        with open(autorun_path, "rb") as autorun:
            self.assertEqual(
                b"[autorun]\r\n"
                b"open=wubi.exe\r\n"
                b"icon=wubi.exe,0\r\n"
                b"label=Install Ubuntu\r\n"
                b"\r\n"
                b"[Content]\r\n"
                b"MusicFiles=false\r\n"
                b"PictureFiles=false\r\n"
                b"VideoFiles=false\r\n",
                autorun.read())

    @mock.patch("cdimage.osextras.fetch")
    def test_download_live_filesystems_ubuntu_live(self, mock_fetch):
        def fetch_side_effect(config, source, target):
            tail = os.path.basename(target).split(".", 1)[1]
            if tail in (
                "squashfs", "kernel-generic", "kernel-generic.efi.signed",
                "initrd-generic", "manifest", "manifest-remove", "size",
                "wubi.exe",
            ):
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.config["ARCHES"] = "amd64 i386"
        self.config["CDIMAGE_LIVE"] = "1"
        download_live_filesystems(self.config)
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live", "live")
        self.assertCountEqual([
            "amd64.autorun.inf",
            "amd64.initrd-generic",
            "amd64.kernel-generic",
            "amd64.kernel-generic.efi.signed",
            "amd64.manifest",
            "amd64.manifest-remove",
            "amd64.size",
            "amd64.squashfs",
            "amd64.wubi.exe",
            "i386.autorun.inf",
            "i386.initrd-generic",
            "i386.kernel-generic",
            "i386.manifest",
            "i386.manifest-remove",
            "i386.size",
            "i386.squashfs",
            "i386.wubi.exe",
        ], os.listdir(output_dir))
        autorun_contents = (
            b"[autorun]\r\n"
            b"open=wubi.exe\r\n"
            b"icon=wubi.exe,0\r\n"
            b"label=Install Ubuntu\r\n"
            b"\r\n"
            b"[Content]\r\n"
            b"MusicFiles=false\r\n"
            b"PictureFiles=false\r\n"
            b"VideoFiles=false\r\n")
        for name in "amd64.autorun.inf", "i386.autorun.inf":
            with open(os.path.join(output_dir, name), "rb") as autorun:
                self.assertEqual(autorun_contents, autorun.read())
