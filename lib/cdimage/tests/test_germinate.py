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

"""Unit tests for cdimage.germinate."""

from __future__ import print_function

import os

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.config import Config
from cdimage.germinate import (
    GerminateNotInstalled,
    GerminateOutput,
    Germination,
    NoMasterSeeds,
)
from cdimage.tests.helpers import TestCase, mkfile, touch, StubAptStateManager

__metaclass__ = type


class TestGermination(TestCase):
    def setUp(self):
        super(TestGermination, self).setUp()
        self.config = Config(read=False)
        self.germination = Germination(self.config, StubAptStateManager())

    def test_germinate_path(self):
        self.config.root = self.use_temp_dir()

        self.assertRaises(
            GerminateNotInstalled, getattr, self.germination, "germinate_path")

        germinate_dir = os.path.join(self.temp_dir, "germinate")
        new_germinate = os.path.join(germinate_dir, "bin", "germinate")
        touch(new_germinate)
        os.chmod(new_germinate, 0o755)
        self.assertEqual(new_germinate, self.germination.germinate_path)

    def test_output_dir(self):
        self.config.root = "/cdimage"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["PROJECT"] = "ubuntu"
        self.assertEqual(
            "/cdimage/scratch/ubuntu/bionic/daily/germinate",
            self.germination.output_dir())

    def test_seed_sources_local_seeds(self):
        self.config["LOCAL_SEEDS"] = "http://www.example.org/"
        self.assertEqual(
            ["http://www.example.org/"],
            self.germination.seed_sources())

    def test_seed_sources_bzr(self):
        for project, series, owners in (
            ("kubuntu", "bionic", ["kubuntu-dev", "ubuntu-core-dev"]),
            ("ubuntu", "bionic", ["ubuntu-core-dev"]),
            ("lubuntu", "bionic", ["lubuntu-dev", "ubuntu-core-dev"]),
            ("xubuntu", "bionic", ["xubuntu-dev", "ubuntu-core-dev"]),
            ("ubuntu-gnome", "bionic",
             ["ubuntu-gnome-dev", "ubuntu-core-dev"]),
            ("ubuntu-mate", "bionic", ["ubuntu-mate-dev", "ubuntu-core-dev"]),
            ("ubuntukylin", "bionic",
             ["ubuntukylin-members", "ubuntu-core-dev"]),
            ("ubuntu-budgie", "bionic",
             ["ubuntubudgie-dev", "ubuntu-core-dev"]),
            ("ubuntustudio", "bionic",
             ["ubuntustudio-dev", "ubuntu-core-dev"]),
        ):
            self.config["DIST"] = series
            self.config["PROJECT"] = project
            sources = [
                "https://git.launchpad.net/~%s/ubuntu-seeds/+git/" % owner
                for owner in owners]
            self.assertEqual(sources, self.germination.seed_sources())

    def test_use_vcs_local_seeds(self):
        self.config["LOCAL_SEEDS"] = "http://www.example.org/"
        self.assertFalse(self.germination.use_vcs)

    def test_seed_dist(self):
        for project, series, seed_dist in (
            ("ubuntu", "bionic", "ubuntu.bionic"),
            ("ubuntu-server", "bionic", "ubuntu.bionic"),
            ("ubuntukylin", "bionic", "ubuntukylin.bionic"),
            ("ubuntu-core-desktop", "noble", "ubuntu.noble"),
        ):
            self.config["DIST"] = series
            self.config["PROJECT"] = project
            self.assertEqual(seed_dist, self.germination.seed_dist())

    @mock.patch("subprocess.check_call")
    def test_germinate_arch(self, mock_check_call):
        self.config.root = self.use_temp_dir()
        germinate_path = os.path.join(
            self.temp_dir, "germinate", "bin", "germinate")
        touch(germinate_path)
        os.chmod(germinate_path, 0o755)
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["PROJECT"] = "ubuntu"

        output_dir = "%s/scratch/ubuntu/bionic/daily/germinate" % self.temp_dir

        def check_call_side_effect(*args, **kwargs):
            touch(os.path.join(output_dir, "amd64", "structure"))

        mock_check_call.side_effect = check_call_side_effect

        self.germination.germinate_arch("amd64")
        expected_command = [
            germinate_path,
            "--seed-source",
            "https://git.launchpad.net/~ubuntu-core-dev/ubuntu-seeds/+git/",
            "--seed-dist", "ubuntu.bionic",
            "--arch", "amd64",
            "--no-rdepends",
            "--apt-config", "amd64/apt.conf",
            "--vcs=git",
        ]
        self.assertEqual(1, mock_check_call.call_count)
        self.assertEqual(expected_command, mock_check_call.call_args[0][0])
        self.assertEqual(
            "%s/amd64" % output_dir, mock_check_call.call_args[1]["cwd"])

    @mock.patch("cdimage.germinate.Germination.germinate_arch")
    def test_germinate_run(self, mock_germinate_arch):
        self.config.root = self.use_temp_dir()
        self.config["DIST"] = "bionic"
        self.config["ARCHES"] = "amd64 i386"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["PROJECT"] = "ubuntu"
        self.capture_logging()
        self.germination.run()
        self.assertTrue(os.path.isdir(os.path.join(
            self.temp_dir, "scratch", "ubuntu", "bionic", "daily",
            "germinate")))
        mock_germinate_arch.assert_has_calls(
            [mock.call("amd64"), mock.call("i386")])
        self.assertLogEqual([
            "Germinating for bionic/amd64 ...",
            "Germinating for bionic/i386 ...",
        ])

    def test_output(self):
        self.config.root = self.use_temp_dir()
        self.config["DIST"] = "bionic"
        self.config["PROJECT"] = "ubuntu"
        output_dir = self.germination.output_dir()
        touch(os.path.join(output_dir, "STRUCTURE"))
        output = self.germination.output()
        self.assertEqual(self.config, output.config)
        self.assertEqual(output_dir, output.directory)


class TestGerminateOutput(TestCase):
    def setUp(self):
        super(TestGerminateOutput, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()

    def write_structure(self, seed_inherit):
        with mkfile(os.path.join(self.temp_dir, "STRUCTURE")) as structure:
            for seed, inherit in seed_inherit:
                print("%s: %s" % (seed, " ".join(inherit)), file=structure)

    def write_ubuntu_structure(self):
        """Write a reduced version of the Ubuntu STRUCTURE file.

        This is based on that in raring.  For brevity, we use the same data
        for testing output for some older series, so the seed expansions in
        these tests will not necessarily match older real-world data.  Given
        that the older series are mainly around for documentation these
        days, this isn't really worth fixing.
        """
        self.write_structure([
            ["required", []],
            ["minimal", ["required"]],
            ["boot", []],
            ["standard", ["minimal"]],
            ["desktop-common", ["standard"]],
            ["d-i-requirements", []],
            ["installer", []],
            ["live-common", ["standard"]],
            ["desktop", ["desktop-common"]],
            ["dns-server", ["standard"]],
            ["lamp-server", ["standard"]],
            ["openssh-server", ["standard"]],
            ["print-server", ["standard"]],
            ["samba-server", ["standard"]],
            ["postgresql-server", ["standard"]],
            ["mail-server", ["standard"]],
            ["tomcat-server", ["standard"]],
            ["virt-host", ["standard"]],
            ["server", ["standard"]],
            ["server-ship", [
                "boot", "installer", "dns-server", "lamp-server",
                "openssh-server", "print-server", "samba-server",
                "postgresql-server", "mail-server", "server", "tomcat-server",
                "virt-host", "d-i-requirements",
            ]],
            ["ship", ["boot", "installer", "desktop", "d-i-requirements"]],
            ["live", ["desktop", "live-common"]],
            ["ship-live", ["boot", "live"]],
            ["usb", ["boot", "installer", "desktop"]],
            ["usb-live", ["usb", "live-common"]],
            ["usb-langsupport", ["usb-live"]],
            ["usb-ship-live", ["usb-langsupport"]],
        ])

    def write_kubuntu_structure(self):
        """Write a reduced version of the Kubuntu STRUCTURE file.

        This is based on that in raring.  For brevity, we use the same data
        for testing output for older series, so the seed expansions in these
        tests will not necessarily match older real-world data.  Given that
        the older series are mainly around for documentation these days,
        this isn't really worth fixing.
        """
        self.write_structure([
            ["required", []],
            ["minimal", ["required"]],
            ["boot", []],
            ["standard", ["minimal"]],
            ["desktop-common", ["standard"]],
            ["d-i-requirements", []],
            ["installer", []],
            ["live-common", ["standard"]],
            ["desktop", ["desktop-common"]],
            ["ship", ["boot", "installer", "desktop", "d-i-requirements"]],
            ["live", ["desktop"]],
            ["dvd-live-langsupport", ["dvd-live"]],
            ["dvd-live", ["live", "dvd-live-langsupport", "ship-live"]],
            ["ship-live", ["boot", "live"]],
            ["development", ["desktop"]],
            ["dvd-langsupport", ["ship"]],
            ["dvd", ["ship", "development", "dvd-langsupport"]],
            ["active", ["standard"]],
            ["active-ship", ["ship"]],
            ["active-live", ["active"]],
            ["active-ship-live", ["ship-live"]],
        ])

    def test_seed_path(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(
            os.path.join(self.temp_dir, "i386", "required"),
            output.seed_path("i386", "required"))

    def write_seed_output(self, arch, seed, packages):
        """Write a simplified Germinate output file, enough for testing."""
        with mkfile(os.path.join(self.temp_dir, arch, seed)) as f:
            why = "Ubuntu.Bionic %s seed" % seed
            pkg_len = max(len("Package"), max(map(len, packages)))
            src_len = max(len("Source"), max(map(len, packages)))
            why_len = len(why)
            print(
                "%-*s | %-*s | %-*s |" % (
                    pkg_len, "Package", src_len, "Source", why_len, "Why"),
                file=f)
            print(
                ("-" * pkg_len) + "-+-" +
                ("-" * src_len) + "-+-" +
                ("-" * why_len) + "-+",
                file=f)
            for pkg in packages:
                print(
                    "%-*s | %-*s | %-*s |" % (
                        pkg_len, pkg, src_len, pkg, why_len, why),
                    file=f)
            print(("-" * (pkg_len + src_len + why_len + 6)) + "-+", file=f)
            print("%*s |" % (pkg_len + src_len + why_len + 6, ""), file=f)

    def test_seed_packages(self):
        self.write_structure([["base", []]])
        self.write_seed_output("i386", "base", ["base-files", "base-passwd"])
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(
            ["base-files", "base-passwd"],
            output.seed_packages("i386", "base"))

    def test_pool_seeds_invalid_config(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["DIST"] = "bionic"
        self.config["PROJECT"] = "ubuntu"
        self.assertRaises(
            NoMasterSeeds, list, output.pool_seeds())

    def test_tasks_output_dir(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["DIST"] = "bionic"
        self.config["PROJECT"] = "ubuntu"
        self.config["IMAGE_TYPE"] = "daily"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "scratch", "ubuntu", "bionic", "daily",
                "tasks"),
            output.tasks_output_dir())

    def test_write_tasks(self):
        self.write_ubuntu_structure()
        for arch in "amd64", "i386":
            seed_dir = os.path.join(self.temp_dir, arch)
            self.write_seed_output(arch, "required", ["base-files-%s" % arch])
            self.write_seed_output(arch, "minimal", ["adduser-%s" % arch])
            self.write_seed_output(arch, "desktop", ["xterm", "firefox"])
            self.write_seed_output(arch, "live", ["xterm"])
            self.write_seed_output(arch, "ship-live", ["pool-pkg-%s" % arch])
            with mkfile(os.path.join(
                    seed_dir, "minimal.seedtext")) as seedtext:
                print("Task-Seeds: required", file=seedtext)
            with mkfile(os.path.join(
                    seed_dir, "desktop.seedtext")) as seedtext:
                print("Task-Per-Derivative: 1", file=seedtext)
            with mkfile(os.path.join(seed_dir, "live.seedtext")) as seedtext:
                print("Task-Per-Derivative: 1", file=seedtext)
        self.config["DIST"] = "bionic"
        self.config["ARCHES"] = "amd64 i386"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.config["CDIMAGE_LIVE"] = "1"
        self.config["PROJECT"] = "ubuntu"
        output = GerminateOutput(self.config, self.temp_dir)
        output.write_tasks()
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "bionic", "daily-live",
            "tasks")
        self.assertCountEqual([
            "amd64-packages", "i386-packages",
        ], os.listdir(output_dir))
        with open(os.path.join(output_dir, "amd64-packages")) as f:
            self.assertEqual("pool-pkg-amd64\n", f.read())
        with open(os.path.join(output_dir, "i386-packages")) as f:
            self.assertEqual("pool-pkg-i386\n", f.read())

    @mock.patch("subprocess.call", return_value=1)
    def test_diff_tasks(self, mock_call):
        self.write_ubuntu_structure()
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.config["ARCHES"] = "amd64 s390x"
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "bionic", "daily-live",
            "tasks")
        touch(os.path.join(output_dir, "required"))
        touch(os.path.join(output_dir, "minimal"))
        touch(os.path.join(output_dir, "standard"))
        touch(os.path.join("%s-previous" % output_dir, "minimal"))
        touch(os.path.join("%s-previous" % output_dir, "standard"))
        touch(os.path.join(output_dir, "amd64-packages"))
        touch(os.path.join(output_dir, "s390x-packages"))
        touch(os.path.join("%s-previous" % output_dir, "amd64-packages"))
        touch(os.path.join("%s-previous" % output_dir, "s390x-packages"))
        output = GerminateOutput(self.config, self.temp_dir)
        output.diff_tasks()
        self.assertEqual(2, mock_call.call_count)
        mock_call.assert_has_calls([
            mock.call([
                "diff", "-u",
                os.path.join("%s-previous" % output_dir, "amd64-packages"),
                os.path.join(output_dir, "amd64-packages")]),
            mock.call([
                "diff", "-u",
                os.path.join("%s-previous" % output_dir, "s390x-packages"),
                os.path.join(output_dir, "s390x-packages")]),
        ])

    @mock.patch("cdimage.germinate.GerminateOutput.diff_tasks")
    def test_update_tasks(self, mock_diff_tasks):
        self.write_ubuntu_structure()
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.config["IMAGE_TYPE"] = "daily-live"
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "bionic", "daily-live",
            "tasks")
        touch(os.path.join(output_dir, "required"))
        touch(os.path.join(output_dir, "minimal"))
        output = GerminateOutput(self.config, self.temp_dir)
        output.update_tasks("20130319")
        self.assertCountEqual(
            ["required", "minimal"],
            os.listdir(os.path.join(
                self.temp_dir, "debian-cd", "tasks", "auto", "daily-live",
                "ubuntu", "bionic")))
        self.assertCountEqual(
            ["required", "minimal"], os.listdir("%s-previous" % output_dir))
