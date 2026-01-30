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

"""Unit tests for cdimage.check_installable."""

from __future__ import print_function

import gzip
import os

from cdimage.check_installable import (
    _check_installable_command,
    _check_installable_dirs,
    _prepare_check_installable,
)
from cdimage.config import Config
from cdimage.tests.helpers import TestCase


class TestCheckInstallable(TestCase):
    def setUp(self):
        super(TestCheckInstallable, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["DIST"] = "bionic"
        self.config["ARCHES"] = "i386"

    def test_dirs(self):
        britney, image_top, live, data = _check_installable_dirs(self.config)
        self.assertEqual(os.path.join(self.config.root, "britney"), britney)
        self.assertEqual(
            os.path.join(
                self.config.root, "scratch", "ubuntu", "bionic", "daily", "tmp"
            ),
            image_top,
        )
        self.assertEqual(
            os.path.join(
                self.config.root, "scratch", "ubuntu", "bionic", "daily", "live"
            ),
            live,
        )
        self.assertEqual(
            os.path.join(britney, "data", "ubuntu", "daily", "bionic"), data
        )

    def test_prepare_no_packages(self):
        _, _, _, data = _check_installable_dirs(self.config)
        self.capture_logging()
        _prepare_check_installable(self.config)
        self.assertLogEqual(["No Packages.gz for bionic/i386; not checking"])
        self.assertEqual(["Sources"], os.listdir(data))
        self.assertEqual(0, os.stat(os.path.join(data, "Sources")).st_size)

    def test_prepare_with_packages(self):
        _, image_top, _, data = _check_installable_dirs(self.config)
        packages_gz = os.path.join(
            image_top,
            "bionic-i386",
            "CD1",
            "dists",
            "bionic",
            "main",
            "binary-i386",
            "Packages.gz",
        )
        os.makedirs(os.path.dirname(packages_gz))
        packages_gz_file = gzip.open(packages_gz, "wb")
        try:
            packages_gz_file.write(b"Package: foo\n\n")
        finally:
            packages_gz_file.close()
        self.capture_logging()
        _prepare_check_installable(self.config)
        self.assertLogEqual([])
        self.assertCountEqual(["Packages_i386", "Sources"], os.listdir(data))
        with open(os.path.join(data, "Packages_i386")) as packages_file:
            self.assertEqual("Package: foo\n\n", packages_file.read())

    def test_command(self):
        britney, _, _, data = _check_installable_dirs(self.config)
        command = _check_installable_command(self.config)
        self.assertEqual(
            [
                "timeout",
                "30",
                os.path.join(britney, "rptprobs.sh"),
                data,
                os.path.join(britney, "report", "ubuntu", "daily", "bionic_probs.html"),
                "Ubuntu bionic",
            ],
            command,
        )
