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

__metaclass__ = type

import gzip
import os
from textwrap import dedent

import mock

from cdimage.config import Config, all_series
from cdimage.germinate import (
    GerminateNotInstalled,
    GerminateOutput,
    Germination,
)
from cdimage.tests.helpers import TestCase


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
        os.makedirs(os.path.join(germinate_dir, "bin"))
        old_germinate = os.path.join(germinate_dir, "germinate.py")
        with open(old_germinate, "w"):
            pass
        os.chmod(old_germinate, 0o755)
        self.assertEqual(old_germinate, self.germination.germinate_path)

        new_germinate = os.path.join(germinate_dir, "bin", "germinate")
        with open(new_germinate, "w"):
            pass
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
            ("ubuntu", "raring", ["ubuntu-core-dev"]),
            ("kubuntu", "natty", ["ubuntu-core-dev"]),
            ("kubuntu", "oneiric", ["kubuntu-dev", "ubuntu-core-dev"]),
            ("kubuntu-active", "natty", ["ubuntu-core-dev"]),
            ("kubuntu-active", "oneiric", ["kubuntu-dev", "ubuntu-core-dev"]),
            ("ubuntustudio", "raring",
             ["ubuntustudio-dev", "ubuntu-core-dev"]),
            ("mythbuntu", "raring", ["mythbuntu-dev", "ubuntu-core-dev"]),
            ("xubuntu", "hardy", ["ubuntu-core-dev"]),
            ("xubuntu", "intrepid", ["xubuntu-dev", "ubuntu-core-dev"]),
            ("lubuntu", "raring", ["lubuntu-dev", "ubuntu-core-dev"]),
        ):
            self.config["DIST"] = series
            sources = [
                "http://bazaar.launchpad.net/~%s/ubuntu-seeds/" % owner
                for owner in owners]
            self.assertEqual(sources, self.germination.seed_sources(project))

    def test_seed_sources_non_bzr(self):
        self.germination = Germination(self.config, prefer_bzr=False)
        self.config["DIST"] = "raring"
        self.assertEqual(
            ["http://people.canonical.com/~ubuntu-archive/seeds/"],
            self.germination.seed_sources("ubuntu"))

    def test_use_bzr_local_seeds(self):
        self.config["LOCAL_SEEDS"] = "http://www.example.org/"
        self.assertFalse(self.germination.use_bzr)

    def test_use_bzr_honours_preference(self):
        self.assertTrue(self.germination.prefer_bzr)
        self.assertTrue(self.germination.use_bzr)
        self.germination.prefer_bzr = False
        self.assertFalse(self.germination.use_bzr)

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
            ("ubuntu-netbook", "maverick", "netbook.maverick"),
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
        os.makedirs(os.path.dirname(germinate_path))
        with open(germinate_path, "w"):
            pass
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
            with open(os.path.join(output_dir, "amd64+mac", "structure"), "w"):
                pass

        mock_check_call.side_effect = check_call_side_effect
        self.germination.germinate_arch("ubuntu", "amd64+mac")
        for expected_file in expected_files:
            self.assertTrue(os.path.exists(expected_file))
        expected_command = [
            germinate_path,
            "--seed-source",
            "http://bazaar.launchpad.net/~ubuntu-core-dev/ubuntu-seeds/",
            "--mirror", "file://%s/" % output_dir,
            "--seed-dist", "ubuntu.raring",
            "--dist", "raring,raring-security,raring-updates",
            "--arch", "amd64",
            "--components", "main",
            "--no-rdepends",
            "--bzr",
        ]
        self.assertEqual(
            [mock.call(expected_command, cwd=("%s/amd64+mac" % output_dir))],
            mock_check_call.call_args_list)

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
        self.assertEqual(
            [mock.call("ubuntu", "amd64"), mock.call("ubuntu", "i386")],
            mock_germinate_arch.call_args_list)
        self.assertLogEqual([
            "Germinating for raring/amd64 ...",
            "Germinating for raring/i386 ...",
        ])

    @mock.patch("cdimage.germinate.Germination.germinate_project")
    def test_run(self, mock_germinate_project):
        self.config["PROJECT"] = "ubuntu"
        self.config["IMAGE_TYPE"] = "daily"
        self.germination.run()
        self.assertEqual(
            [mock.call("ubuntu")], mock_germinate_project.call_args_list)

        mock_germinate_project.reset_mock()
        del self.config["PROJECT"]
        self.config["ALL_PROJECTS"] = "ubuntu kubuntu"
        self.config["IMAGE_TYPE"] = "source"
        self.germination.run()
        self.assertEqual(
            [mock.call("ubuntu"), mock.call("kubuntu")],
            mock_germinate_project.call_args_list)


class TestGerminateOutput(TestCase):
    def setUp(self):
        super(TestGerminateOutput, self).setUp()
        self.use_temp_dir()
        self.config = Config(read=False)
        self.structure = os.path.join(self.temp_dir, "STRUCTURE")

    def write_structure(self, seed_inherit):
        with open(self.structure, "w") as structure:
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
        output = GerminateOutput(self.config, self.structure)
        self.assertEqual(["a"], output._inheritance("a"))
        self.assertEqual(["a", "b"], output._inheritance("b"))
        self.assertEqual(["a", "b", "c"], output._inheritance("c"))

    def test_inheritance_avoids_duplicates(self):
        """_inheritance avoids adding a seed more than once."""
        self.write_structure([["a", []], ["b", ["a"]], ["c", ["a", "b"]]])
        output = GerminateOutput(self.config, self.structure)
        self.assertEqual(["a", "b", "c"], output._inheritance("c"))

    def test_without_inheritance(self):
        self.write_structure(
            [["a", []], ["b", ["a"]], ["c", ["b"]], ["d", ["a", "c"]]])
        output = GerminateOutput(self.config, self.structure)
        inheritance = output._inheritance("d")
        self.assertEqual(["a", "b", "c", "d"], inheritance)
        self.assertEqual(
            ["c", "d"], output._without_inheritance("b", inheritance))

    def test_list_seeds_all(self):
        self.write_structure([["a", []], ["b", ["a"]], ["c", []]])
        output = GerminateOutput(self.config, self.structure)
        self.assertEqual(["a", "b", "c"], list(output.list_seeds("all")))

    def test_list_seeds_tasks_ubuntu(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.structure)
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
        output = GerminateOutput(self.config, self.structure)
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

    def test_list_seeds_tasks_kubuntu_active(self):
        self.write_kubuntu_structure()
        output = GerminateOutput(self.config, self.structure)
        self.config["PROJECT"] = "kubuntu-active"
        self.config["DIST"] = "raring"
        expected = [
            "boot", "installer", "required", "minimal", "standard",
            "desktop-common", "desktop", "d-i-requirements", "ship",
            "active-ship",
        ]
        self.assertEqual(expected, list(output.list_seeds("tasks")))

    def test_list_seeds_installer(self):
        self.write_ubuntu_breezy_structure()
        self.write_structure([["installer", []], ["casper", []]])
        output = GerminateOutput(self.config, self.structure)
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
        output = GerminateOutput(self.config, self.structure)
        for series in all_series[:2]:
            self.config["DIST"] = series
            self.assertEqual(["base"], list(output.list_seeds("debootstrap")))
        self.write_ubuntu_breezy_structure()
        output = GerminateOutput(self.config, self.structure)
        for series in all_series[2:6]:
            self.config["DIST"] = series
            self.assertEqual(
                ["minimal"], list(output.list_seeds("debootstrap")))
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.structure)
        for series in all_series[6:]:
            self.config["DIST"] = series
            self.assertEqual(
                ["required", "minimal"],
                list(output.list_seeds("debootstrap")))

    def test_list_seeds_base(self):
        self.write_ubuntu_hoary_structure()
        output = GerminateOutput(self.config, self.structure)
        for series in all_series[:2]:
            self.config["DIST"] = series
            self.assertEqual(["base"], list(output.list_seeds("base")))
        self.write_ubuntu_breezy_structure()
        output = GerminateOutput(self.config, self.structure)
        self.config["DIST"] = all_series[2]
        self.assertEqual(
            ["minimal", "standard"], list(output.list_seeds("base")))
        self.write_ubuntu_dapper_structure()
        output = GerminateOutput(self.config, self.structure)
        for series in all_series[3:6]:
            self.config["DIST"] = series
            self.assertEqual(
                ["boot", "minimal", "standard"],
                list(output.list_seeds("base")))
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.structure)
        for series in all_series[6:]:
            self.config["DIST"] = series
            self.assertEqual(
                ["boot", "required", "minimal", "standard"],
                list(output.list_seeds("base")))

    def test_list_seeds_ship_live_ubuntu_server(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.structure)
        self.config["PROJECT"] = "ubuntu-server"
        expected = [
            "boot", "installer", "standard", "dns-server", "lamp-server",
            "openssh-server", "print-server", "samba-server",
            "postgresql-server", "mail-server", "server", "tomcat-server",
            "virt-host", "d-i-requirements", "server-ship",
        ]
        self.assertEqual(expected, list(output.list_seeds("ship-live")))

    # TODO list_seeds addon/dvd untested

    def test_seed_packages(self):
        self.write_structure([["base", []]])
        arch_output_dir = os.path.join(self.temp_dir, "i386")
        os.mkdir(arch_output_dir)
        with open(os.path.join(arch_output_dir, "base"), "w") as base:
            # A real germinate output file is more complex than this, but
            # this is more than enough for testing.
            print(
                dedent("""\
                    Package     | Source      | Why                     |
                    ------------+-------------+-------------------------+
                    base-files  | base-files  | Ubuntu.Raring base seed |
                    base-passwd | base-passwd | Ubuntu.Raring base seed |
                    ----------------------------------------------------+
                                                                        |"""),
                file=base)
        output = GerminateOutput(self.config, self.structure)
        self.assertEqual(
            ["base-files", "base-passwd"],
            output.seed_packages("i386", "base"))
