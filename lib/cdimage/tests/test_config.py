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

"""Unit tests for cdimage.config."""

from __future__ import print_function

__metaclass__ = type

import os
from textwrap import dedent
try:
    from test.support import EnvironmentVarGuard
except ImportError:
    from test.test_support import EnvironmentVarGuard

from cdimage.config import Config, Series
from cdimage.tests.helpers import TestCase


class TestSeries(TestCase):
    def test_str(self):
        series = Series("warty", ["warty"])
        self.assertEqual("warty", str(series))

    def test_compare(self):
        all_series = ["warty", "hoary", "breezy"]
        series = Series("hoary", all_series)

        self.assertLess(series, "breezy")
        self.assertLessEqual(series, "hoary")
        self.assertLessEqual(series, "breezy")
        self.assertEqual(series, "hoary")
        self.assertNotEqual(series, "warty")
        self.assertNotEqual(series, "breezy")
        self.assertGreaterEqual(series, "warty")
        self.assertGreaterEqual(series, "hoary")
        self.assertGreater(series, "warty")

        self.assertLess(series, Series("breezy", all_series))
        self.assertLessEqual(series, Series("hoary", all_series))
        self.assertLessEqual(series, Series("breezy", all_series))
        self.assertEqual(series, Series("hoary", all_series))
        self.assertNotEqual(series, Series("warty", all_series))
        self.assertNotEqual(series, Series("breezy", all_series))
        self.assertGreaterEqual(series, Series("warty", all_series))
        self.assertGreaterEqual(series, Series("hoary", all_series))
        self.assertGreater(series, Series("warty", all_series))


class TestConfig(TestCase):
    def test_default_root(self):
        with EnvironmentVarGuard() as env:
            env.pop("CDIMAGE_ROOT", None)
            config = Config(read=False)
            self.assertEqual("/srv/cdimage.ubuntu.com", config.root)

    def test_root_from_environment(self):
        with EnvironmentVarGuard() as env:
            env["CDIMAGE_ROOT"] = "/path"
            config = Config(read=False)
            self.assertEqual("/path", config.root)

    def test_default_values(self):
        config = Config(read=False)
        self.assertEqual("", config["PROJECT"])

    def test_read_shell(self):
        self.use_temp_dir()
        with EnvironmentVarGuard() as env:
            env["CDIMAGE_ROOT"] = self.temp_dir
            os.mkdir(os.path.join(self.temp_dir, "etc"))
            with open(os.path.join(self.temp_dir, "etc", "config"), "w") as f:
                print(dedent("""\
                    #! /bin/sh
                    PROJECT=ubuntu
                    CAPPROJECT=Ubuntu
                    """), file=f)
            config = Config()
            self.assertEqual("ubuntu", config["PROJECT"])
            self.assertEqual("Ubuntu", config["CAPPROJECT"])
