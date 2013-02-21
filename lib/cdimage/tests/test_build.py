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

import os
import shutil
import subprocess
from textwrap import dedent

from cdimage.build import update_local_indices
from cdimage.config import Config, Series
from cdimage.tests.helpers import TestCase


class TestUpdateLocalIndices(TestCase):
    def setUp(self):
        super(TestUpdateLocalIndices, self).setUp()
        self.use_temp_dir()
        self.config = Config(read=False)
        self.config.root = self.temp_dir
        self.config["DIST"] = Series.find_by_name("raring")
        self.config["CPUARCHES"] = "i386"
        self.packages = os.path.join(self.temp_dir, "local", "packages")
        self.database = os.path.join(self.temp_dir, "local", "database")
        self.dists = os.path.join(self.database, "dists")
        self.indices = os.path.join(self.database, "indices")
        self.pool = os.path.join(self.packages, "pool", "local")

    def make_deb(self, path, section, priority):
        build_dir = os.path.join(self.temp_dir, "make_deb")
        os.mkdir(build_dir)
        try:
            base = os.path.basename(path).split(".", 1)[0]
            name, version, arch = base.split("_")
            control_dir = os.path.join(build_dir, "DEBIAN")
            os.mkdir(control_dir)
            with open(os.path.join(control_dir, "control"), "w") as control:
                print(dedent("""\
                    Package: %s
                    Version: %s
                    Architecture: %s
                    Section: %s
                    Priority: %s
                    Maintainer: Fake Maintainer <fake@example.org>
                    Description: fake package""") %
                    (name, version, arch, section, priority),
                    file=control)
            with open("/dev/null", "w") as devnull:
                subprocess.check_call(
                    ["dpkg-deb", "-b", build_dir, path], stdout=devnull)
        finally:
            shutil.rmtree(build_dir)

    def test_no_local_packages(self):
        self.assertFalse(os.path.exists(self.packages))

        def mock_call(*args, **kwargs):
            self.fail("subprocess.call called when it should not have been")

        real_call = subprocess.call
        subprocess.call = mock_call
        try:
            update_local_indices(self.config)
        finally:
            subprocess.call = real_call

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
        with open(os.path.join(fake_dir, "random-file"), "w"):
            pass

        def mock_call(*args, **kwargs):
            pass

        real_call = subprocess.call
        subprocess.call = mock_call
        try:
            update_local_indices(self.config)
        finally:
            subprocess.call = real_call

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
