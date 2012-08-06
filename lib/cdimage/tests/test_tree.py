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

"""Unit tests for cdimage.tree."""

__metaclass__ = type

import os

from cdimage.config import Config
from cdimage.tests.helpers import TestCase, touch
from cdimage.tree import DailyTree, SimpleTree, Tree


class TestTree(TestCase):
    def setUp(self):
        super(TestTree, self).setUp()
        self.use_temp_dir()
        self.config = Config(read=False)
        self.tree = Tree(self.config, self.temp_dir)

    def test_path_to_project(self):
        self.assertEqual("kubuntu", self.tree.path_to_project("kubuntu/foo"))
        self.assertEqual("ubuntu", self.tree.path_to_project("foo"))
        self.assertEqual("ubuntu", self.tree.path_to_project("ubuntu/foo/bar"))

    def test_manifest_file_allowed_passes_good_extensions(self):
        paths = [
            os.path.join(self.temp_dir, name)
            for name in ("foo.iso", "foo.img")]
        for path in paths:
            touch(path)
            self.assertTrue(self.tree.manifest_file_allowed(path))

    def test_manifest_file_allowed_fails_bad_extensions(self):
        paths = [
            os.path.join(self.temp_dir, name)
            for name in ("foo.txt", "foo")]
        for path in paths:
            touch(path)
            self.assertFalse(self.tree.manifest_file_allowed(path))

    def test_manifest_file_allowed_fails_directories(self):
        path = os.path.join(self.temp_dir, "dir.iso")
        os.mkdir(path)
        self.assertFalse(self.tree.manifest_file_allowed(path))


class TestDailyTree(TestCase):
    def setUp(self):
        super(TestDailyTree, self).setUp()
        self.use_temp_dir()
        self.config = Config(read=False)
        self.tree = DailyTree(self.config, self.temp_dir)

    def test_name_to_series(self):
        self.assertEqual(
            "warty", self.tree.name_to_series("warty-install-i386.iso"))
        self.assertRaises(ValueError, self.tree.name_to_series, "README")

    def test_path_to_manifest(self):
        iso = "kubuntu/hoary-install-i386.iso"
        iso_path = os.path.join(self.temp_dir, iso)
        os.makedirs(os.path.dirname(iso_path))
        touch(iso_path)
        self.assertEqual(
            "kubuntu\thoary\t/%s\t0" % iso, self.tree.path_to_manifest(iso))

    def test_manifest_files_includes_current(self):
        daily = os.path.join(self.temp_dir, "daily")
        os.makedirs(os.path.join(daily, "20120806"))
        os.symlink("20120806", os.path.join(daily, "current"))
        touch(os.path.join(daily, "20120806", "warty-install-i386.iso"))
        self.assertEqual(
            ["daily/current/warty-install-i386.iso"],
            list(self.tree.manifest_files()))

    def test_manifest(self):
        daily = os.path.join(self.temp_dir, "daily")
        os.makedirs(os.path.join(daily, "20120806"))
        os.symlink("20120806", os.path.join(daily, "current"))
        touch(os.path.join(daily, "20120806", "hoary-install-i386.iso"))
        daily_live = os.path.join(self.temp_dir, "daily-live")
        os.makedirs(os.path.join(daily_live, "20120806"))
        os.symlink("20120806", os.path.join(daily_live, "current"))
        touch(os.path.join(daily_live, "20120806", "hoary-live-i386.iso"))
        self.assertEqual([
            "ubuntu\thoary\t/daily-live/current/hoary-live-i386.iso\t0",
            "ubuntu\thoary\t/daily/current/hoary-install-i386.iso\t0",
            ], self.tree.manifest())


class TestSimpleTree(TestCase):
    def setUp(self):
        super(TestSimpleTree, self).setUp()
        self.use_temp_dir()
        self.config = Config(read=False)
        self.tree = SimpleTree(self.config, self.temp_dir)

    def test_name_to_series(self):
        self.assertEqual(
            "warty", self.tree.name_to_series("ubuntu-4.10-install-i386.iso"))
        self.assertRaises(ValueError, self.tree.name_to_series, "foo-bar.iso")

    def test_path_to_manifest(self):
        iso = "kubuntu/.pool/kubuntu-5.04-install-i386.iso"
        iso_path = os.path.join(self.temp_dir, iso)
        os.makedirs(os.path.dirname(iso_path))
        touch(iso_path)
        self.assertEqual(
            "kubuntu\thoary\t/%s\t0" % iso, self.tree.path_to_manifest(iso))

    def test_manifest_files_prefers_non_pool(self):
        pool = os.path.join(self.temp_dir, ".pool")
        os.mkdir(pool)
        touch(os.path.join(pool, "ubuntu-4.10-install-i386.iso"))
        dist = os.path.join(self.temp_dir, "warty")
        os.mkdir(dist)
        os.symlink(
            os.path.join(os.pardir, ".pool", "ubuntu-4.10-install-i386.iso"),
            os.path.join(dist, "ubuntu-4.10-install-i386.iso"))
        self.assertEqual(
            ["warty/ubuntu-4.10-install-i386.iso"],
            list(self.tree.manifest_files()))

    def test_manifest_files_includes_non_duplicates_in_pool(self):
        pool = os.path.join(self.temp_dir, ".pool")
        os.mkdir(pool)
        touch(os.path.join(pool, "ubuntu-4.10-install-i386.iso"))
        touch(os.path.join(pool, "ubuntu-4.10-install-amd64.iso"))
        dist = os.path.join(self.temp_dir, "warty")
        os.mkdir(dist)
        os.symlink(
            os.path.join(os.pardir, ".pool", "ubuntu-4.10-install-i386.iso"),
            os.path.join(dist, "ubuntu-4.10-install-i386.iso"))
        self.assertEqual([
            "warty/ubuntu-4.10-install-i386.iso",
            ".pool/ubuntu-4.10-install-amd64.iso",
            ], list(self.tree.manifest_files()))

    def test_manifest(self):
        pool = os.path.join(self.temp_dir, "kubuntu", ".pool")
        os.makedirs(pool)
        touch(os.path.join(pool, "kubuntu-5.04-install-i386.iso"))
        touch(os.path.join(pool, "kubuntu-5.04-live-i386.iso"))
        dist = os.path.join(self.temp_dir, "kubuntu", "hoary")
        os.makedirs(dist)
        os.symlink(
            os.path.join(os.pardir, ".pool", "kubuntu-5.04-install-i386.iso"),
            os.path.join(dist, "kubuntu-5.04-install-i386.iso"))
        os.symlink(
            os.path.join(os.pardir, ".pool", "kubuntu-5.04-live-i386.iso"),
            os.path.join(dist, "kubuntu-5.04-live-i386.iso"))
        self.assertEqual([
            "kubuntu\thoary\t/kubuntu/hoary/kubuntu-5.04-install-i386.iso\t0",
            "kubuntu\thoary\t/kubuntu/hoary/kubuntu-5.04-live-i386.iso\t0",
            ], self.tree.manifest())
