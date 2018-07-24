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

from functools import partial
import gzip
import os
import subprocess
from textwrap import dedent

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.config import Config, all_series
from cdimage.germinate import (
    GerminateNotInstalled,
    GerminateOutput,
    Germination,
    NoMasterSeeds,
)
from cdimage.mail import text_file_type
from cdimage.tests.helpers import TestCase, mkfile, touch

__metaclass__ = type


class TestGermination(TestCase):
    def setUp(self):
        super(TestGermination, self).setUp()
        self.config = Config(read=False)
        self.germination = Germination(self.config)

    def test_germinate_path(self):
        self.config.root = self.use_temp_dir()

        self.assertRaises(
            GerminateNotInstalled, getattr, self.germination, "germinate_path")

        germinate_dir = os.path.join(self.temp_dir, "germinate")
        old_germinate = os.path.join(germinate_dir, "germinate.py")
        touch(old_germinate)
        os.chmod(old_germinate, 0o755)
        self.assertEqual(old_germinate, self.germination.germinate_path)

        new_germinate = os.path.join(germinate_dir, "bin", "germinate")
        touch(new_germinate)
        os.chmod(new_germinate, 0o755)
        self.assertEqual(new_germinate, self.germination.germinate_path)

    def test_output_dir(self):
        self.config.root = "/cdimage"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily"
        self.assertEqual(
            "/cdimage/scratch/ubuntu/raring/daily/germinate",
            self.germination.output_dir("ubuntu"))

    def test_seed_sources_local_seeds(self):
        self.config["LOCAL_SEEDS"] = "http://www.example.org/"
        self.assertEqual(
            ["http://www.example.org/"],
            self.germination.seed_sources("ubuntu"))

    def test_seed_sources_bzr(self):
        for project, series, owners in (
            ("kubuntu", "natty", ["ubuntu-core-dev"]),
            ("kubuntu", "oneiric", ["kubuntu-dev"]),
            ("kubuntu-active", "natty", ["ubuntu-core-dev"]),
            ("kubuntu-active", "oneiric", ["kubuntu-dev"]),
            ("kubuntu-plasma5", "utopic", ["kubuntu-dev"]),
            ("ubuntustudio", "raring",
             ["ubuntustudio-dev"]),
            ("mythbuntu", "raring", ["mythbuntu-dev"]),
            ("xubuntu", "hardy", ["ubuntu-core-dev"]),
            ("ubuntu-gnome", "raring",
             ["ubuntu-gnome-dev"]),
            ("ubuntu-budgie", "zesty",
             ["ubuntubudgie-dev"]),
            ("ubuntu-mate", "vivid",
             ["ubuntu-mate-dev"]),
            ("ubuntu-moblin-remix", "hardy", ["moblin"]),
            ("ubuntukylin", "trusty", ["ubuntu-core-dev"]),
        ):
            self.config["DIST"] = series
            sources = [
                "http://bazaar.launchpad.net/~%s/ubuntu-seeds/" % owner
                for owner in owners]
            sources.append(
                "https://git.launchpad.net/~ubuntu-core-dev/"
                "ubuntu-seeds/+git/")
            self.assertEqual(sources, self.germination.seed_sources(project))

        for project, series, owners in (
            ("ubuntu", "raring", ["ubuntu-core-dev"]),
            ("lubuntu", "raring", ["lubuntu-dev", "ubuntu-core-dev"]),
            ("xubuntu", "intrepid", ["xubuntu-dev", "ubuntu-core-dev"]),
            ("ubuntukylin", "utopic",
             ["ubuntukylin-members", "ubuntu-core-dev"]),
        ):
            self.config["DIST"] = series
            sources = [
                "https://git.launchpad.net/~%s/ubuntu-seeds/+git/" % owner
                for owner in owners]
            self.assertEqual(sources, self.germination.seed_sources(project))

    def test_seed_sources_non_bzr(self):
        self.germination = Germination(self.config, prefer_vcs=False)
        self.config["DIST"] = "raring"
        self.assertEqual(
            ["http://people.canonical.com/~ubuntu-archive/seeds/"],
            self.germination.seed_sources("ubuntu"))

    def test_use_vcs_local_seeds(self):
        self.config["LOCAL_SEEDS"] = "http://www.example.org/"
        self.assertFalse(self.germination.use_vcs)

    def test_use_vcs_honours_preference(self):
        self.assertTrue(self.germination.prefer_vcs)
        self.assertTrue(self.germination.use_vcs)
        self.germination.prefer_vcs = False
        self.assertFalse(self.germination.use_vcs)

    def test_make_index(self):
        self.config.root = self.use_temp_dir()
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily"
        files = []
        for component in "main", "restricted", "universe", "multiverse":
            source_dir = os.path.join(
                self.temp_dir, "ftp", "dists", "raring", component, "source")
            os.makedirs(source_dir)
            with gzip.GzipFile(
                    os.path.join(source_dir, "Sources.gz"), "wb") as sources:
                sources.write(component.encode("UTF-8"))
                sources.write(b"\n")
            files.append("dists/raring/%s/source/Sources.gz" % component)
        self.germination.make_index("ubuntu", "i386", files[0], files)
        output_file = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily", "germinate",
            "dists", "raring", "main", "source", "Sources.gz")
        self.assertTrue(os.path.exists(output_file))
        with gzip.GzipFile(output_file, "rb") as output_sources:
            self.assertEqual(
                b"main\nrestricted\nuniverse\nmultiverse\n",
                output_sources.read())

    def test_germinate_dists_environment_override(self):
        self.config["GERMINATE_DISTS"] = "sentinel,sentinel-updates"
        self.assertEqual(
            ["sentinel", "sentinel-updates"], self.germination.germinate_dists)

    def test_germinate_dists_proposed(self):
        self.config["DIST"] = "precise"
        self.config["PROPOSED"] = "1"
        self.assertEqual([
            "precise",
            "precise-security",
            "precise-updates",
            "precise-proposed",
        ], self.germination.germinate_dists)

    def test_germinate_dists_no_proposed(self):
        self.config["DIST"] = "raring"
        self.assertEqual([
            "raring",
            "raring-security",
            "raring-updates",
        ], self.germination.germinate_dists)

    def test_seed_dist(self):
        for project, series, seed_dist in (
            ("ubuntu", "raring", "ubuntu.raring"),
            ("ubuntu-server", "breezy", "ubuntu-server.breezy"),
            ("ubuntu-server", "raring", "ubuntu.raring"),
            ("jeos", "breezy", "jeos.breezy"),
            ("jeos", "hardy", "ubuntu.hardy"),
            ("ubuntukylin", "raring", "ubuntu.raring"),
            ("ubuntukylin", "utopic", "ubuntukylin.utopic"),
            ("ubuntu-mid", "hardy", "mobile.hardy"),
            ("ubuntu-netbook", "maverick", "netbook.maverick"),
            ("ubuntu-headless", "lucid", "ubuntu.lucid"),
            ("ubuntu-moblin-remix", "hardy", "moblin.hardy"),
            ("ubuntu-desktop-next", "utopic", "ubuntu-touch.utopic"),
        ):
            self.config["DIST"] = series
            self.assertEqual(seed_dist, self.germination.seed_dist(project))

    def test_components(self):
        self.assertEqual(
            ["main", "restricted"], list(self.germination.components))
        self.config["CDIMAGE_UNSUPPORTED"] = "1"
        self.assertEqual(
            ["main", "restricted", "universe", "multiverse"],
            list(self.germination.components))
        self.config["CDIMAGE_ONLYFREE"] = "1"
        self.assertEqual(
            ["main", "universe"], list(self.germination.components))
        del self.config["CDIMAGE_UNSUPPORTED"]
        self.assertEqual(["main"], list(self.germination.components))

    @mock.patch("subprocess.check_call")
    def test_germinate_arch(self, mock_check_call):
        self.config.root = self.use_temp_dir()
        germinate_path = os.path.join(
            self.temp_dir, "germinate", "bin", "germinate")
        touch(germinate_path)
        os.chmod(germinate_path, 0o755)
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily"

        output_dir = "%s/scratch/ubuntu/raring/daily/germinate" % self.temp_dir
        expected_files = []

        for dist in "raring", "raring-security", "raring-updates":
            for suffix in (
                "binary-amd64/Packages.gz",
                "source/Sources.gz",
                "debian-installer/binary-amd64/Packages.gz",
            ):
                for component in "main", "restricted":
                    path = os.path.join(
                        self.temp_dir, "ftp", "dists", dist, component, suffix)
                    os.makedirs(os.path.dirname(path))
                    with gzip.GzipFile(path, "wb"):
                        pass
                expected_files.append(
                    os.path.join(output_dir, "dists", dist, "main", suffix))

        def check_call_side_effect(*args, **kwargs):
            touch(os.path.join(output_dir, "amd64+mac", "structure"))

        mock_check_call.side_effect = check_call_side_effect
        self.germination.germinate_arch("ubuntu", "amd64+mac")
        for expected_file in expected_files:
            self.assertTrue(os.path.exists(expected_file))
        expected_command = [
            germinate_path,
            "--seed-source",
            "https://git.launchpad.net/~ubuntu-core-dev/ubuntu-seeds/+git/",
            "--mirror", "file://%s/" % output_dir,
            "--seed-dist", "ubuntu.raring",
            "--dist", "raring,raring-security,raring-updates",
            "--arch", "amd64",
            "--components", "main",
            "--no-rdepends",
            "--vcs=auto",
        ]
        self.assertEqual(1, mock_check_call.call_count)
        self.assertEqual(expected_command, mock_check_call.call_args[0][0])
        self.assertEqual(
            "%s/amd64+mac" % output_dir, mock_check_call.call_args[1]["cwd"])

    @mock.patch("cdimage.germinate.Germination.germinate_arch")
    def test_germinate_project(self, mock_germinate_arch):
        self.config.root = self.use_temp_dir()
        self.config["DIST"] = "raring"
        self.config["ARCHES"] = "amd64 i386"
        self.config["IMAGE_TYPE"] = "daily"
        self.capture_logging()
        self.germination.germinate_project("ubuntu")
        self.assertTrue(os.path.isdir(os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily",
            "germinate")))
        mock_germinate_arch.assert_has_calls(
            [mock.call("ubuntu", "amd64"), mock.call("ubuntu", "i386")])
        self.assertLogEqual([
            "Germinating for raring/amd64 ...",
            "Germinating for raring/i386 ...",
        ])

    @mock.patch("cdimage.germinate.Germination.germinate_project")
    def test_run(self, mock_germinate_project):
        self.config["PROJECT"] = "ubuntu"
        self.config["IMAGE_TYPE"] = "daily"
        self.germination.run()
        mock_germinate_project.assert_called_once_with("ubuntu")

        mock_germinate_project.reset_mock()
        del self.config["PROJECT"]
        self.config["ALL_PROJECTS"] = "ubuntu kubuntu"
        self.config["IMAGE_TYPE"] = "source"
        self.germination.run()
        mock_germinate_project.assert_has_calls(
            [mock.call("ubuntu"), mock.call("kubuntu")])

    def test_output(self):
        self.config.root = self.use_temp_dir()
        self.config["DIST"] = "precise"
        output_dir = self.germination.output_dir("ubuntu")
        touch(os.path.join(output_dir, "STRUCTURE"))
        output = self.germination.output("ubuntu")
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

    def write_ubuntu_hoary_structure(self):
        """Write the Ubuntu 5.04 STRUCTURE file."""
        self.write_structure([
            ["base", []],
            ["desktop", ["base"]],
            ["ship", ["base", "desktop"]],
            ["live", ["base", "desktop"]],
            ["installer", []],
            ["casper", []],
            ["supported", ["base", "desktop", "ship", "live"]],
        ])

    def write_ubuntu_breezy_structure(self):
        """Write the Ubuntu 5.10 STRUCTURE file."""
        self.write_structure([
            ["minimal", []],
            ["standard", ["minimal"]],
            ["desktop", ["minimal", "standard"]],
            ["ship", ["minimal", "standard", "desktop"]],
            ["live", ["minimal", "standard", "desktop"]],
            ["installer", []],
            ["casper", []],
            ["supported", ["minimal", "standard", "desktop", "ship", "live"]],
        ])

    def write_ubuntu_dapper_structure(self):
        """Write a reduced version of the Ubuntu 6.06 LTS STRUCTURE file."""
        self.write_structure([
            ["minimal", []],
            ["boot", []],
            ["standard", ["minimal"]],
            ["desktop", ["minimal", "standard"]],
            ["server", ["boot", "minimal", "standard"]],
            ["ship", ["boot", "minimal", "standard", "desktop"]],
            ["live", ["minimal", "standard", "desktop"]],
            ["ship-live", ["boot", "minimal", "standard", "desktop", "live"]],
            ["installer", []],
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

    def test_inheritance_recurses(self):
        """_inheritance recurses properly."""
        self.write_structure([["a", []], ["b", ["a"]], ["c", ["b"]]])
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(["a"], output._inheritance("a"))
        self.assertEqual(["a", "b"], output._inheritance("b"))
        self.assertEqual(["a", "b", "c"], output._inheritance("c"))

    def test_inheritance_avoids_duplicates(self):
        """_inheritance avoids adding a seed more than once."""
        self.write_structure([["a", []], ["b", ["a"]], ["c", ["a", "b"]]])
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(["a", "b", "c"], output._inheritance("c"))

    def test_without_inheritance(self):
        self.write_structure(
            [["a", []], ["b", ["a"]], ["c", ["b"]], ["d", ["a", "c"]]])
        output = GerminateOutput(self.config, self.temp_dir)
        inheritance = output._inheritance("d")
        self.assertEqual(["a", "b", "c", "d"], inheritance)
        self.assertEqual(
            ["c", "d"], output._without_inheritance("b", inheritance))

    def test_list_seeds_all(self):
        self.write_structure([["a", []], ["b", ["a"]], ["c", []]])
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(["a", "b", "c"], list(output.list_seeds("all")))

    def test_list_seeds_tasks_ubuntu(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        expected = [
            "boot", "installer", "required", "minimal", "standard",
            "desktop-common", "desktop", "d-i-requirements", "ship",
        ]
        self.assertEqual(expected, list(output.list_seeds("tasks")))
        self.config["CDIMAGE_DVD"] = "1"
        expected.extend(["dns-server", "lamp-server"])
        self.assertEqual(expected, list(output.list_seeds("tasks")))

    def test_list_seeds_tasks_ubuntu_server(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["PROJECT"] = "ubuntu-server"
        expected = [
            "boot", "installer", "required", "minimal", "standard",
            "desktop-common", "desktop", "d-i-requirements", "ship",
        ]
        for series in all_series[:3]:
            self.config["DIST"] = series
            self.assertEqual(expected, list(output.list_seeds("tasks")))
        expected = ["required", "minimal", "standard", "server"]
        self.config["DIST"] = all_series[3]
        self.assertEqual(expected, list(output.list_seeds("tasks")))
        expected = [
            "boot", "installer", "required", "minimal", "standard",
            "dns-server", "lamp-server", "openssh-server", "print-server",
            "samba-server", "postgresql-server", "mail-server", "server",
            "tomcat-server", "virt-host", "d-i-requirements", "server-ship",
        ]
        for series in all_series[4:]:
            self.config["DIST"] = series
            self.assertEqual(expected, list(output.list_seeds("tasks")))

    def test_list_seeds_task_ubuntu_server_squashfs(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["PROJECT"] = "ubuntu-server"
        self.config["DIST"] = "raring"
        self.config["CDIMAGE_SQUASHFS_BASE"] = "1"
        expected = [
            "boot", "installer", "standard", "dns-server", "lamp-server",
            "openssh-server", "print-server", "samba-server",
            "postgresql-server", "mail-server", "server", "tomcat-server",
            "virt-host", "d-i-requirements", "server-ship",
        ]
        self.assertEqual(expected, list(output.list_seeds("tasks")))

    def test_list_seeds_tasks_kubuntu_active(self):
        self.write_kubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["PROJECT"] = "kubuntu-active"
        self.config["DIST"] = "raring"
        expected = [
            "boot", "installer", "required", "minimal", "standard",
            "desktop-common", "desktop", "d-i-requirements", "ship",
            "active-ship",
        ]
        self.assertEqual(expected, list(output.list_seeds("tasks")))

    def test_list_seeds_tasks_jeos(self):
        self.write_structure([
            ["required", []],
            ["minimal", ["required"]],
            ["jeos", ["minimal"]],
        ])
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["PROJECT"] = "jeos"
        self.assertEqual(
            ["required", "minimal", "jeos"], list(output.list_seeds("tasks")))

    def test_list_seeds_installer(self):
        self.write_ubuntu_breezy_structure()
        self.write_structure([["installer", []], ["casper", []]])
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["CDIMAGE_INSTALL_BASE"] = "1"
        self.assertEqual(["installer"], list(output.list_seeds("installer")))
        del self.config["CDIMAGE_INSTALL_BASE"]
        self.config["CDIMAGE_LIVE"] = "1"
        self.config["DIST"] = "hoary"
        self.assertEqual(["casper"], list(output.list_seeds("installer")))
        self.config["DIST"] = "breezy"
        self.assertEqual(["casper"], list(output.list_seeds("installer")))
        self.config["DIST"] = "dapper"
        self.assertEqual([], list(output.list_seeds("installer")))

    def test_list_seeds_debootstrap(self):
        self.write_ubuntu_hoary_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        for series in all_series[:2]:
            self.config["DIST"] = series
            self.assertEqual(["base"], list(output.list_seeds("debootstrap")))
        self.write_ubuntu_breezy_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        for series in all_series[2:6]:
            self.config["DIST"] = series
            self.assertEqual(
                ["minimal"], list(output.list_seeds("debootstrap")))
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        for series in all_series[6:]:
            self.config["DIST"] = series
            self.assertEqual(
                ["required", "minimal"],
                list(output.list_seeds("debootstrap")))

    def test_list_seeds_base(self):
        self.write_ubuntu_hoary_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        for series in all_series[:2]:
            self.config["DIST"] = series
            self.assertEqual(["base"], list(output.list_seeds("base")))
        self.write_ubuntu_breezy_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["DIST"] = all_series[2]
        self.assertEqual(
            ["minimal", "standard"], list(output.list_seeds("base")))
        self.write_ubuntu_dapper_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        for series in all_series[3:6]:
            self.config["DIST"] = series
            self.assertEqual(
                ["boot", "minimal", "standard"],
                list(output.list_seeds("base")))
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        for series in all_series[6:]:
            self.config["DIST"] = series
            self.assertEqual(
                ["boot", "required", "minimal", "standard"],
                list(output.list_seeds("base")))

    # TODO list_seeds ship-live/addon/dvd untested

    def test_seed_path(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(
            os.path.join(self.temp_dir, "i386", "required"),
            output.seed_path("i386", "required"))

    def write_seed_output(self, arch, seed, packages):
        """Write a simplified Germinate output file, enough for testing."""
        with mkfile(os.path.join(self.temp_dir, arch, seed)) as f:
            why = "Ubuntu.Raring %s seed" % seed
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

    # TODO: master_seeds addon untested

    def test_master_seeds_onlysource(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["CDIMAGE_ONLYSOURCE"] = "1"
        self.assertEqual([
            "required", "minimal", "boot", "standard", "desktop-common",
            "d-i-requirements", "installer", "live-common", "desktop",
            "dns-server", "lamp-server", "openssh-server", "print-server",
            "samba-server", "postgresql-server", "mail-server",
            "tomcat-server", "virt-host", "server", "server-ship", "ship",
            "live", "ship-live", "usb", "usb-live", "usb-langsupport",
            "usb-ship-live",
        ], list(output.master_seeds()))

    def test_master_seeds_dvd_ubuntu_raring(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["CDIMAGE_DVD"] = "1"
        self.assertEqual(
            ["usb-langsupport", "usb-ship-live"], list(output.master_seeds()))

    def test_master_seeds_install_ubuntu_raring(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["CDIMAGE_INSTALL"] = "1"
        self.config["CDIMAGE_INSTALL_BASE"] = "1"
        self.assertEqual([
            "installer", "boot", "required", "minimal", "standard",
            "desktop-common", "desktop", "d-i-requirements", "ship",
        ], list(output.master_seeds()))

    def test_master_seeds_live_ubuntu_raring(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["CDIMAGE_INSTALL_BASE"] = "1"
        self.config["CDIMAGE_LIVE"] = "1"
        self.assertEqual([
            "installer", "boot", "required", "minimal", "standard",
            "ship-live",
        ], list(output.master_seeds()))

    @mock.patch("cdimage.germinate.GerminateOutput.master_seeds")
    def test_master_task_entries(self, mock_master_seeds):
        def side_effect():
            yield "required"
            yield "minimal"

        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["DIST"] = "raring"
        mock_master_seeds.side_effect = side_effect
        self.assertEqual([
            "#include <ubuntu/raring/required>",
            "#include <ubuntu/raring/minimal>",
        ], list(output.master_task_entries("ubuntu")))

    @mock.patch(
        "cdimage.germinate.GerminateOutput.master_seeds", return_value=[])
    def test_master_task_entries_no_seeds(self, mock_master_seeds):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["DIST"] = "raring"
        self.assertRaises(
            NoMasterSeeds, list, output.master_task_entries("ubuntu"))

    def test_tasks_output_dir(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "scratch", "ubuntu", "raring", "daily",
                "tasks"),
            output.tasks_output_dir("ubuntu"))

    def test_task_packages_plain(self):
        self.write_structure([["base", []]])
        self.write_seed_output("i386", "base", ["base-files", "base-passwd"])
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(
            ["base-files", "base-passwd"],
            list(output.task_packages("i386", "base", "base")))

    def test_task_packages_installer(self):
        # kernel-image-* is excluded from the installer seed.
        self.write_structure([["installer", []]])
        self.write_seed_output(
            "i386", "installer", [
                "block-modules-3.8.0-6-generic-di",
                "kernel-image-3.8.0-6-generic-di",
            ])
        self.config["CDIMAGE_INSTALL_BASE"] = "1"
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(
            ["block-modules-3.8.0-6-generic-di"],
            list(output.task_packages("i386", "installer", "installer")))

    def test_task_packages_squashfs(self):
        self.write_ubuntu_structure()
        self.config["PROJECT"] = "ubuntu-server"
        self.config["DIST"] = "raring"
        self.write_seed_output(
            "i386", "installer", ["base-installer", "bootstrap-base"])
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(
            ["base-installer", "bootstrap-base"],
            list(output.task_packages("i386", "installer", "installer")))
        self.config["CDIMAGE_SQUASHFS_BASE"] = "1"
        self.assertEqual(
            ["base-installer", "live-installer"],
            list(output.task_packages("i386", "installer", "installer")))

    def test_task_packages_gutsy_ps3_hack(self):
        self.write_structure([["boot", []], ["installer", []]])
        self.write_seed_output(
            "powerpc+ps3", "boot", ["linux-image-2.6.22-14-powerpc64-smp"])
        self.write_seed_output(
            "powerpc+ps3", "installer", [
                "block-modules-2.6.22-14-powerpc-di",
                "block-modules-2.6.22-14-powerpc64-smp-di",
            ])
        self.config["DIST"] = "gutsy"
        self.config["CDIMAGE_INSTALL_BASE"] = "1"
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(
            ["linux-image-2.6.22-14-cell"],
            list(output.task_packages("powerpc+ps3", "boot", "boot")))
        self.assertEqual(
            ["block-modules-2.6.22-14-cell-di"],
            list(output.task_packages(
                "powerpc+ps3", "installer", "installer")))

    def test_task_packages_precise_kernels(self):
        self.write_structure([["boot", []], ["installer", []]])
        self.write_seed_output(
            "i386", "boot", ["linux-image-3.2.0-23-generic-pae"])
        self.write_seed_output(
            "i386", "installer", ["block-modules-3.2.0-23-generic-pae-di"])
        self.config["DIST"] = "precise"
        self.config["CDIMAGE_INSTALL_BASE"] = "1"
        output = GerminateOutput(self.config, self.temp_dir)
        for project, flavour in (
            ("ubuntu", "generic-pae"),
            ("xubuntu", "generic"),
            ("lubuntu", "generic"),
        ):
            self.config["PROJECT"] = project
            self.assertEqual(
                ["linux-image-3.2.0-23-%s" % flavour],
                list(output.task_packages("i386", "boot", "boot")))
            self.assertEqual(
                ["block-modules-3.2.0-23-%s-di" % flavour],
                list(output.task_packages("i386", "installer", "installer")))

    # TODO: installer_initrds, installer_subarches untested

    def test_initrd_packages(self):
        self.write_ubuntu_structure()
        manifest_path = os.path.join(
            self.temp_dir, "ftp", "dists", "raring", "main", "installer-i386",
            "current", "images", "MANIFEST.udebs")
        with mkfile(manifest_path) as manifest:
            print(dedent("""\
                cdrom/initrd.gz
                \tanna 1.45ubuntu1 i386
                \tcdrom-detect 1.43ubuntu1 all
                netboot/netboot.tar.gz
                \tdownload-installer 1.32ubuntu1 all
                \tnet-retriever 1.32ubuntu1 i386"""), file=manifest)
        self.config["DIST"] = "raring"
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(
            set(["anna", "cdrom-detect"]),
            output.initrd_packages("./cdrom/initrd.gz", "i386"))
        self.assertEqual(
            set(["download-installer", "net-retriever"]),
            output.initrd_packages("./netboot/netboot.tar.gz", "i386"))
        self.assertEqual(set(), output.initrd_packages("unknown", "powerpc"))

    def test_common_initrd_packages(self):
        self.write_ubuntu_structure()
        manifest_path = os.path.join(
            self.temp_dir, "ftp", "dists", "raring", "main", "installer-i386",
            "current", "images", "MANIFEST.udebs")
        with mkfile(manifest_path) as manifest:
            print(dedent("""\
                cdrom/initrd.gz
                \tanna 1.45ubuntu1 i386
                \tcdrom-detect 1.43ubuntu1 all
                netboot/netboot.tar.gz
                \tanna 1.45ubuntu1 i386
                \tnet-retriever 1.32ubuntu1 i386"""), file=manifest)
        self.config["DIST"] = "raring"
        output = GerminateOutput(self.config, self.temp_dir)
        self.assertEqual(set(["anna"]), output.common_initrd_packages("i386"))

    # TODO: task_project untested

    def test_task_headers(self):
        self.write_ubuntu_structure()
        seedtext_path = os.path.join(self.temp_dir, "i386", "desktop.seedtext")
        with mkfile(seedtext_path) as seedtext:
            print(dedent("""\
                Task-Per-Derivative: 1
                Task-Key: ubuntu-desktop
                Task-Seeds: desktop-common

                = Seed text starts here ="""), file=seedtext)
        output = GerminateOutput(self.config, self.temp_dir)
        expected = {
            "per-derivative": "1",
            "key": "ubuntu-desktop",
            "seeds": "desktop-common",
        }
        self.assertEqual(expected, output.task_headers("i386", "desktop"))
        self.assertEqual({}, output.task_headers("i386", "missing"))

    # TODO: seed_task_mapping <= gutsy untested

    def test_seed_task_mapping(self):
        self.write_ubuntu_structure()
        seed_dir = os.path.join(self.temp_dir, "i386")
        with mkfile(os.path.join(seed_dir, "standard.seedtext")) as seedtext:
            print("Task-Key: ubuntu-standard", file=seedtext)
        with mkfile(os.path.join(seed_dir, "desktop.seedtext")) as seedtext:
            print(dedent("""\
                Task-Per-Derivative: 1
                Task-Seeds: desktop-common"""), file=seedtext)
        self.config["DIST"] = "raring"
        output = GerminateOutput(self.config, self.temp_dir)
        expected = [
            (["standard"], "standard"),
            (["desktop", "desktop-common"], "ubuntu-desktop"),
        ]
        self.assertEqual(
            expected, list(output.seed_task_mapping("ubuntu", "i386")))

    def test_write_tasks_project(self):
        self.write_ubuntu_structure()
        for arch in "amd64", "i386":
            seed_dir = os.path.join(self.temp_dir, arch)
            self.write_seed_output(arch, "required", ["base-files-%s" % arch])
            self.write_seed_output(arch, "minimal", ["adduser-%s" % arch])
            self.write_seed_output(arch, "desktop", ["xterm", "firefox"])
            self.write_seed_output(arch, "live", ["xterm"])
            with mkfile(os.path.join(
                    seed_dir, "minimal.seedtext")) as seedtext:
                print("Task-Seeds: required", file=seedtext)
            with mkfile(os.path.join(
                    seed_dir, "desktop.seedtext")) as seedtext:
                print("Task-Per-Derivative: 1", file=seedtext)
            with mkfile(os.path.join(seed_dir, "live.seedtext")) as seedtext:
                print("Task-Per-Derivative: 1", file=seedtext)
        self.config["DIST"] = "raring"
        self.config["ARCHES"] = "amd64 i386"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.config["CDIMAGE_LIVE"] = "1"
        output = GerminateOutput(self.config, self.temp_dir)
        output.write_tasks_project("ubuntu")
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live",
            "tasks")
        self.assertCountEqual([
            "required", "minimal", "desktop", "live",
            "override.amd64", "override.i386",
            "important.amd64", "important.i386",
            "MASTER",
        ], os.listdir(output_dir))
        with open(os.path.join(output_dir, "required")) as f:
            self.assertEqual(
                dedent("""\
                    #ifdef ARCH_amd64
                    base-files-amd64
                    #endif /* ARCH_amd64 */
                    #ifdef ARCH_i386
                    base-files-i386
                    #endif /* ARCH_i386 */
                    """),
                f.read())
        with open(os.path.join(output_dir, "minimal")) as f:
            self.assertEqual(
                dedent("""\
                    #ifdef ARCH_amd64
                    adduser-amd64
                    #endif /* ARCH_amd64 */
                    #ifdef ARCH_i386
                    adduser-i386
                    #endif /* ARCH_i386 */
                    """),
                f.read())
        with open(os.path.join(output_dir, "desktop")) as f:
            self.assertEqual(
                dedent("""\
                    #ifdef ARCH_amd64
                    firefox
                    xterm
                    #endif /* ARCH_amd64 */
                    #ifdef ARCH_i386
                    firefox
                    xterm
                    #endif /* ARCH_i386 */
                    """),
                f.read())
        with open(os.path.join(output_dir, "live")) as f:
            self.assertEqual(
                dedent("""\
                    #ifdef ARCH_amd64
                    xterm
                    #endif /* ARCH_amd64 */
                    #ifdef ARCH_i386
                    xterm
                    #endif /* ARCH_i386 */
                    """),
                f.read())
        with open(os.path.join(output_dir, "override.amd64")) as f:
            self.assertEqual(
                dedent("""\
                    adduser-amd64  Task  minimal
                    base-files-amd64  Task  minimal
                    firefox  Task  ubuntu-desktop
                    xterm  Task  ubuntu-desktop, ubuntu-live
                    """),
                f.read())
        with open(os.path.join(output_dir, "override.i386")) as f:
            self.assertEqual(
                dedent("""\
                    adduser-i386  Task  minimal
                    base-files-i386  Task  minimal
                    firefox  Task  ubuntu-desktop
                    xterm  Task  ubuntu-desktop, ubuntu-live
                    """),
                f.read())
        with open(os.path.join(output_dir, "important.amd64")) as f:
            self.assertEqual("adduser-amd64\nbase-files-amd64\n", f.read())
        with open(os.path.join(output_dir, "important.i386")) as f:
            self.assertEqual("adduser-i386\nbase-files-i386\n", f.read())
        with open(os.path.join(output_dir, "MASTER")) as f:
            self.assertEqual("#include <ubuntu/raring/ship-live>\n", f.read())

    # TODO: write_tasks untested

    @mock.patch("subprocess.call", return_value=1)
    def test_diff_tasks(self, mock_call):
        self.write_ubuntu_structure()
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live",
            "tasks")
        touch(os.path.join(output_dir, "required"))
        touch(os.path.join(output_dir, "minimal"))
        touch(os.path.join(output_dir, "standard"))
        touch(os.path.join("%s-previous" % output_dir, "minimal"))
        touch(os.path.join("%s-previous" % output_dir, "standard"))
        output = GerminateOutput(self.config, self.temp_dir)
        output.diff_tasks()
        self.assertEqual(2, mock_call.call_count)
        mock_call.assert_has_calls([
            mock.call([
                "diff", "-u",
                os.path.join("%s-previous" % output_dir, "minimal"),
                os.path.join(output_dir, "minimal")]),
            mock.call([
                "diff", "-u",
                os.path.join("%s-previous" % output_dir, "standard"),
                os.path.join(output_dir, "standard")]),
        ])

    @mock.patch("cdimage.germinate.GerminateOutput.diff_tasks")
    def test_update_tasks_no_mail(self, mock_diff_tasks):
        self.write_ubuntu_structure()
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live",
            "tasks")
        touch(os.path.join(output_dir, "required"))
        touch(os.path.join(output_dir, "minimal"))
        output = GerminateOutput(self.config, self.temp_dir)
        output.update_tasks("20130319")
        self.assertCountEqual(
            ["required", "minimal"],
            os.listdir(os.path.join(
                self.temp_dir, "debian-cd", "tasks", "auto", "daily-live",
                "ubuntu", "raring")))
        self.assertCountEqual(
            ["required", "minimal"], os.listdir("%s-previous" % output_dir))

    @mock.patch("cdimage.germinate.send_mail")
    @mock.patch("cdimage.germinate.GerminateOutput.diff_tasks")
    def test_update_tasks_no_recipients(self, mock_diff_tasks, mock_send_mail):
        self.write_ubuntu_structure()
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        output = GerminateOutput(self.config, self.temp_dir)
        os.makedirs(output.tasks_output_dir("ubuntu"))
        output.update_tasks("20130319")
        self.assertEqual(0, mock_send_mail.call_count)
        task_mail_path = os.path.join(self.temp_dir, "etc", "task-mail")
        touch(task_mail_path)
        output.update_tasks("20130319")
        self.assertEqual(0, mock_send_mail.call_count)

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

    @mock.patch("cdimage.germinate.send_mail")
    def test_update_tasks_sends_mail(self, mock_send_mail):
        original_call = subprocess.call

        def call_side_effect(command, *args, **kwargs):
            if (len(command) >= 4 and command[:2] == ["diff", "-u"] and
                    "stdout" in kwargs):
                old = os.path.basename(command[2])
                new = os.path.basename(command[3])
                original_call(
                    ["printf", "%s\\n", "--- %s" % old], *args, **kwargs)
                original_call(
                    ["printf", "%s\\n", "+++ %s" % new], *args, **kwargs)
                return 1
            else:
                return original_call(command, *args, **kwargs)

        self.write_ubuntu_structure()
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily-live"
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily-live",
            "tasks")
        touch(os.path.join(output_dir, "required"))
        touch(os.path.join(output_dir, "minimal"))
        touch(os.path.join(output_dir, "standard"))
        touch(os.path.join("%s-previous" % output_dir, "minimal"))
        touch(os.path.join("%s-previous" % output_dir, "standard"))
        task_mail_path = os.path.join(self.temp_dir, "etc", "task-mail")
        with mkfile(task_mail_path) as task_mail:
            print("foo@example.org", file=task_mail)
        mock_send_mail.side_effect = partial(
            self.send_mail_to_file, os.path.join(self.temp_dir, "mail"))
        output = GerminateOutput(self.config, self.temp_dir)
        with mock.patch("subprocess.call", side_effect=call_side_effect):
            output.update_tasks("20130319")
        with open(os.path.join(self.temp_dir, "mail")) as mail:
            self.assertEqual(dedent("""\
                To: foo@example.org
                Subject: Task changes for Ubuntu daily-live/raring on 20130319
                X-Generated-By: update-tasks

                --- minimal
                +++ minimal
                --- standard
                +++ standard
                """), mail.read())
