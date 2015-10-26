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

"""Test compliance with various static analysis tools."""

from __future__ import print_function

import os
import re
import subprocess
import sys

from cdimage import osextras
from cdimage.tests.helpers import TestCase

__metaclass__ = type


class TestStatic(TestCase):
    def all_paths(self):
        paths = []
        for dirpath, dirnames, filenames in os.walk("."):
            for ignore in ".bzr", "__pycache__":
                if ignore in dirnames:
                    dirnames.remove(ignore)
            filenames = [
                n for n in filenames
                if not n.startswith(".") and not n.endswith("~")]
            if dirpath.split(os.sep)[-1] == "bin":
                for filename in filenames:
                    paths.append(os.path.join(dirpath, filename))
            else:
                for filename in filenames:
                    if filename.endswith(".py"):
                        paths.append(os.path.join(dirpath, filename))
        return paths

    def test_pep8_clean(self):
        if not osextras.find_on_path("pep8"):
            return
        if "SKIP_SLOW_TESTS" in os.environ:
            return
        subp = subprocess.Popen(
            ["pep8"] + self.all_paths(),
            stdout=subprocess.PIPE, universal_newlines=True)
        output = subp.communicate()[0].splitlines()
        for line in output:
            print(line)
        self.assertEqual(0, len(output))

    def test_pyflakes_clean(self):
        if sys.version < "3":
            pyflakes = "pyflakes"
        else:
            pyflakes = "pyflakes3"
        if not osextras.find_on_path(pyflakes):
            return
        if "SKIP_SLOW_TESTS" in os.environ:
            return

        # Exclude handling based on run-pyflakes.py from reviewboard,
        # licensed under the MIT License.
        cur_dir = os.path.dirname(__file__)
        exclusions_path = os.path.join(cur_dir, "%s.exclude" % pyflakes)
        exclusions = set()
        if os.path.exists(exclusions_path):
            with open(exclusions_path, "r") as f:
                for line in f:
                    if not line.startswith("#"):
                        exclusions.add(line.rstrip())

        error = False
        subp = subprocess.Popen(
            [pyflakes] + self.all_paths(),
            stdout=subprocess.PIPE, universal_newlines=True)
        output = subp.communicate()[0].splitlines()
        for line in output:
            if line.startswith("#"):
                continue
            line = line.rstrip()
            canon_line = re.sub(r":[0-9]+:", ":*:", line, 1)
            canon_line = re.sub(r"line [0-9]+", "line *", canon_line)
            if canon_line not in exclusions:
                print(line)
                error = True

        self.assertFalse(error)
