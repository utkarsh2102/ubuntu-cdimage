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

"""Unit tests for cdimage.build."""

from __future__ import print_function

__metaclass__ = type

import gzip
import os
import subprocess

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.build import (
    _debootstrap_script,
    extract_debootstrap,
    update_local_indices,
)
from cdimage.config import Config
from cdimage.tests.helpers import TestCase, touch


class TestUpdateLocalIndices(TestCase):
    def setUp(self):
        super(TestUpdateLocalIndices, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.config["DIST"] = "raring"
        self.config["CPUARCHES"] = "i386"
        self.packages = os.path.join(self.temp_dir, "local", "packages")
        self.database = os.path.join(self.temp_dir, "local", "database")
        self.dists = os.path.join(self.database, "dists")
        self.indices = os.path.join(self.database, "indices")
        self.pool = os.path.join(self.packages, "pool", "local")

    @mock.patch("subprocess.call")
    def test_no_local_packages(self, mock_call):
        self.assertFalse(os.path.exists(self.packages))
        mock_call.side_effect = Exception(
            "subprocess.call called when it should not have been")
        update_local_indices(self.config)

    def test_lists_and_overrides(self):
        fake_dir = os.path.join(self.pool, "f", "fake")
        os.makedirs(fake_dir)
        self.make_deb(
            os.path.join(fake_dir, "fake_1_i386.deb"), "misc", "optional")
        self.make_deb(
            os.path.join(fake_dir, "fake_1_unknown.deb"), "misc", "optional")
        self.make_deb(
            os.path.join(fake_dir, "fake-nf_1_all.deb"),
            "non-free/admin", "extra")
        self.make_deb(
            os.path.join(fake_dir, "fake-udeb_1_i386.udeb"),
            "debian-installer", "optional")
        self.make_deb(
            os.path.join(fake_dir, "fake-udeb_1_unknown.udeb"),
            "debian-installer", "optional")
        self.make_deb(
            os.path.join(fake_dir, "fake-udeb-indep_1_all.udeb"),
            "debian-installer", "extra")
        touch(os.path.join(fake_dir, "random-file"))

        with mock.patch("subprocess.call", return_value=0) as mock_call:
            update_local_indices(self.config)

            expected_command = [
                "apt-ftparchive", "generate", "apt-ftparchive.conf"]
            mock_call.assert_called_once_with(
                expected_command, cwd=self.packages)

        self.assertCountEqual([
            "raring_local_binary-i386.list",
            "raring_local_debian-installer_binary-i386.list",
        ], os.listdir(self.dists))
        with open(os.path.join(
                self.dists, "raring_local_binary-i386.list")) as f:
            self.assertCountEqual([
                "pool/local/f/fake/fake_1_i386.deb",
                "pool/local/f/fake/fake-nf_1_all.deb",
            ], f.read().splitlines())
        with open(os.path.join(
                self.dists,
                "raring_local_debian-installer_binary-i386.list")) as f:
            self.assertCountEqual([
                "pool/local/f/fake/fake-udeb_1_i386.udeb",
                "pool/local/f/fake/fake-udeb-indep_1_all.udeb",
            ], f.read().splitlines())

        self.assertCountEqual([
            "override.raring.local.i386",
            "override.raring.local.debian-installer.i386",
        ], os.listdir(self.indices))
        with open(os.path.join(
                self.indices, "override.raring.local.i386")) as f:
            self.assertCountEqual([
                "fake\toptional\tlocal/misc",
                "fake-nf\textra\tlocal/admin",
            ], f.read().splitlines())
        with open(os.path.join(
                self.indices,
                "override.raring.local.debian-installer.i386")) as f:
            self.assertCountEqual([
                "fake-udeb\toptional\tlocal/debian-installer",
                "fake-udeb-indep\textra\tlocal/debian-installer",
            ], f.read().splitlines())

        self.assertTrue(os.path.exists(os.path.join(
            self.packages, "dists", "raring", "local", "binary-i386")))
        self.assertTrue(os.path.exists(os.path.join(
            self.packages, "dists", "raring", "local", "debian-installer",
            "binary-i386")))


class TestExtractDebootstrap(TestCase):
    def setUp(self):
        super(TestExtractDebootstrap, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()

    def test_debootstrap_script(self):
        for series, script in (
            ("gutsy", "usr/lib/debootstrap/scripts/gutsy"),
            ("hardy", "usr/share/debootstrap/scripts/hardy"),
        ):
            self.config["DIST"] = series
            self.assertEqual(script, _debootstrap_script(self.config))

    def test_extract_debootstrap(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64+mac"
        mirror_dir = os.path.join(self.temp_dir, "ftp")
        packages_path = os.path.join(
            mirror_dir, "dists", "raring", "main", "debian-installer",
            "binary-amd64", "Packages.gz")
        udeb_path = os.path.join(
            mirror_dir, "pool", "main", "d", "debootstrap",
            "debootstrap-udeb_1_all.udeb")
        os.makedirs(os.path.dirname(packages_path))
        os.makedirs(os.path.dirname(udeb_path))
        self.make_deb(
            udeb_path, "debian-installer", "extra",
            files={"/usr/share/debootstrap/scripts/raring": b"sentinel"})
        with gzip.GzipFile(packages_path, "wb") as packages:
            ftparchive = subprocess.Popen(
                ["apt-ftparchive", "packages", "pool"],
                stdout=subprocess.PIPE, cwd=mirror_dir)
            data, _ = ftparchive.communicate()
            packages.write(data)
            self.assertEqual(0, ftparchive.returncode)
        extract_debootstrap(self.config)
        output_path = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "raring", "daily",
            "debootstrap", "raring-amd64+mac")
        self.assertTrue(os.path.exists(output_path))
        with open(output_path, "rb") as output:
            self.assertEqual(b"sentinel", output.read())
