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

"""Unit tests for cdimage.build_id."""

import os
try:
    from test.support import EnvironmentVarGuard
except ImportError:
    from test.test_support import EnvironmentVarGuard

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.build_id import next_build_id
from cdimage.config import Config
from cdimage.tests.helpers import TestCase


class TestNextBuildId(TestCase):
    def test_increment(self):
        with EnvironmentVarGuard() as env:
            config = Config(read=False)
            config.root = self.use_temp_dir()
            config["PROJECT"] = "ubuntu"
            config["DIST"] = "warty"
            config["DATE"] = "20120806"
            os.mkdir(os.path.join(self.temp_dir, "etc"))
            stamp = os.path.join(
                config.root, "etc",
                ".next-build-suffix-ubuntu-warty-daily-live")
            self.assertFalse(os.path.exists(stamp))
            self.assertEqual("20120806", next_build_id(config, "daily-live"))
            with open(stamp) as stamp_file:
                self.assertEqual("20120806:1\n", stamp_file.read())
            self.assertEqual("20120806.1", next_build_id(config, "daily-live"))
            with open(stamp) as stamp_file:
                self.assertEqual("20120806:2\n", stamp_file.read())

    @mock.patch("time.strftime", return_value="20130225")
    def test_defaults(self, *args):
        with EnvironmentVarGuard() as env:
            config = Config(read=False)
            config.root = self.use_temp_dir()
            config["PROJECT"] = "ubuntu"
            config["DIST"] = "warty"
            os.mkdir(os.path.join(self.temp_dir, "etc"))
            stamp = os.path.join(
                config.root, "etc", ".next-build-suffix-ubuntu-warty-daily")
            self.assertFalse(os.path.exists(stamp))
            self.assertEqual("20130225", next_build_id(config, ""))
            with open(stamp) as stamp_file:
                self.assertEqual("20130225:1\n", stamp_file.read())

    @mock.patch("time.strftime", return_value="20130225")
    def test_date_suffix(self, *args):
        with EnvironmentVarGuard() as env:
            config = Config(read=False)
            config.root = self.use_temp_dir()
            config["PROJECT"] = "ubuntu"
            config["DIST"] = "warty"
            config["DATE_SUFFIX"] = "5"
            os.mkdir(os.path.join(self.temp_dir, "etc"))
            stamp = os.path.join(
                config.root, "etc", ".next-build-suffix-ubuntu-warty-daily")
            self.assertFalse(os.path.exists(stamp))
            self.assertEqual("20130225.5", next_build_id(config, "daily"))
            with open(stamp) as stamp_file:
                self.assertEqual("20130225:6\n", stamp_file.read())

    def test_debug(self):
        with EnvironmentVarGuard() as env:
            config = Config(read=False)
            config.root = self.use_temp_dir()
            config["PROJECT"] = "ubuntu"
            config["DIST"] = "warty"
            config["DEBUG"] = "1"
            os.mkdir(os.path.join(self.temp_dir, "etc"))
            stamp = os.path.join(
                config.root, "etc", ".next-build-suffix-ubuntu-warty-daily")
            self.assertFalse(os.path.exists(stamp))
            next_build_id(config, "daily")
            self.assertFalse(os.path.exists(stamp))
