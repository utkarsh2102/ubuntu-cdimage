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

"""Unit tests for cdimage.project."""

import os

from cdimage.project import setenv_for_project
from cdimage.tests.helpers import TestCase


class TestProject(TestCase):
    def setUp(self):
        super(TestProject, self).setUp()
        os.environ.pop("PROJECT", None)
        os.environ.pop("CAPPROJECT", None)
        os.environ.pop("UBUNTU_DEFAULTS_LOCALE", None)

    def test_nonexistent(self):
        self.assertFalse(setenv_for_project("nonexistent"))
        self.assertNotIn("PROJECT", os.environ)
        self.assertNotIn("CAPPROJECT", os.environ)

    def test_ubuntu(self):
        self.assertTrue(setenv_for_project("ubuntu"))
        self.assertEqual("ubuntu", os.environ["PROJECT"])
        self.assertEqual("Ubuntu", os.environ["CAPPROJECT"])

    def test_ubuntu_zh_CN(self):
        os.environ["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        self.assertTrue(setenv_for_project("ubuntu"))
        self.assertEqual("ubuntu", os.environ["PROJECT"])
        self.assertEqual("Ubuntu Chinese Edition", os.environ["CAPPROJECT"])
