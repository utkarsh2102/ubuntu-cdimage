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

import os

from cdimage.config import Config, Series, all_series
from cdimage.germinate import GerminateOutput
from cdimage.tests.helpers import TestCase


class TestGerminateOutput(TestCase):
    def setUp(self):
        super(TestGerminateOutput, self).setUp()
        self.use_temp_dir()
        self.config = Config(read=False)

    def write_structure(self, seed_inherit):
        with open(os.path.join(self.temp_dir, "STRUCTURE"), "w") as structure:
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
        self.config["DIST"] = Series.find_by_name("raring")
        expected = [
            "boot", "installer", "required", "minimal", "standard",
            "desktop-common", "desktop", "d-i-requirements", "ship",
        ]
        self.assertEqual(expected, list(output.list_seeds("tasks")))
        self.config["CDIMAGE_DVD"] = 1
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

    def test_list_seeds_tasks_kubuntu_active(self):
        self.write_kubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["PROJECT"] = "kubuntu-active"
        self.config["DIST"] = Series.find_by_name("raring")
        expected = [
            "boot", "installer", "required", "minimal", "standard",
            "desktop-common", "desktop", "d-i-requirements", "ship",
            "active-ship",
        ]
        self.assertEqual(expected, list(output.list_seeds("tasks")))

    def test_list_seeds_installer(self):
        self.write_ubuntu_breezy_structure()
        self.write_structure([["installer", []], ["casper", []]])
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["CDIMAGE_INSTALL_BASE"] = 1
        self.assertEqual(["installer"], list(output.list_seeds("installer")))
        del self.config["CDIMAGE_INSTALL_BASE"]
        self.config["CDIMAGE_LIVE"] = 1
        self.config["DIST"] = Series.find_by_name("hoary")
        self.assertEqual(["casper"], list(output.list_seeds("installer")))
        self.config["DIST"] = Series.find_by_name("breezy")
        self.assertEqual(["casper"], list(output.list_seeds("installer")))
        self.config["DIST"] = Series.find_by_name("dapper")
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

    def test_list_seeds_ship_live_ubuntu_server(self):
        self.write_ubuntu_structure()
        output = GerminateOutput(self.config, self.temp_dir)
        self.config["PROJECT"] = "ubuntu-server"
        expected = [
            "boot", "installer", "standard", "dns-server", "lamp-server",
            "openssh-server", "print-server", "samba-server",
            "postgresql-server", "mail-server", "server", "tomcat-server",
            "virt-host", "d-i-requirements", "server-ship",
        ]
        self.assertEqual(expected, list(output.list_seeds("ship-live")))

    # TODO list_seeds addon/dvd untested
