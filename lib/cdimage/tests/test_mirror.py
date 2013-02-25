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

"""Unit tests for cdimage.mirror."""

from __future__ import print_function

__metaclass__ = type

import os

import mock

from cdimage.config import Config, all_series
from cdimage.mirror import (
    UnknownManifestFile,
    _trigger_mirror,
    check_manifest,
    find_mirror,
    trigger_mirrors,
)
from cdimage.tests.helpers import TestCase, touch


class TestChecksumFile(TestCase):
    def assertMirrorEqual(self, base, arch, series):
        config = Config(read=False)
        config["DIST"] = series
        self.assertEqual(
            os.path.join(config.root, base), find_mirror(config, arch))

    def test_amd64(self):
        for series in all_series:
            self.assertMirrorEqual("ftp", "amd64", series)

    def test_armel(self):
        for series in all_series:
            self.assertMirrorEqual("ftp", "armel", series)

    def test_hppa(self):
        for series in all_series:
            self.assertMirrorEqual("ftp", "hppa", series)

    def test_i386(self):
        for series in all_series:
            self.assertMirrorEqual("ftp", "i386", series)

    def test_lpia(self):
        for series in all_series:
            self.assertMirrorEqual("ftp", "lpia", series)

    def test_powerpc(self):
        for series in all_series:
            self.assertMirrorEqual("ftp", "powerpc", series)

    def test_sparc(self):
        for series in all_series:
            self.assertMirrorEqual("ftp", "sparc", series)


class TestTriggerMirrors(TestCase):
    @mock.patch("subprocess.Popen")
    def test_trigger_mirror_background(self, mock_popen):
        self.capture_logging()
        _trigger_mirror("id-test", "archvsync", "remote", background=True)
        self.assertLogEqual(["remote:"])
        mock_popen.assert_called_once_with([
            "ssh", "-i", "id-test",
            "-o", "StrictHostKeyChecking no",
            "-o", "BatchMode yes",
            "archvsync@remote",
            "./releases-sync",
        ])

    @mock.patch("subprocess.call")
    def test_trigger_mirror_foreground(self, mock_call):
        self.capture_logging()
        _trigger_mirror("id-test", "archvsync", "remote")
        self.assertLogEqual(["remote:"])
        mock_call.assert_called_once_with([
            "ssh", "-i", "id-test",
            "-o", "StrictHostKeyChecking no",
            "-o", "BatchMode yes",
            "archvsync@remote",
            "./releases-sync",
        ])

    def test_check_manifest_no_manifest(self):
        config = Config(read=False)
        config.root = self.use_temp_dir()
        check_manifest(config)

    def test_check_manifest_unknown_file(self):
        config = Config(read=False)
        config.root = self.use_temp_dir()
        manifest = os.path.join(self.temp_dir, "www", "simple", ".manifest")
        os.makedirs(os.path.dirname(manifest))
        with open(manifest, "w") as f:
            print(
                "ubuntu\tprecise\t/precise/ubuntu-12.04.2-desktop-i386.iso\t"
                "726970368", file=f)
        self.assertRaises(UnknownManifestFile, check_manifest, config)

    def test_check_manifest_unreadable_file(self):
        config = Config(read=False)
        config.root = self.use_temp_dir()
        manifest = os.path.join(self.temp_dir, "www", "simple", ".manifest")
        os.makedirs(os.path.dirname(manifest))
        os.symlink(".manifest", manifest)
        self.assertRaises(IOError, check_manifest, config)

    def check_manifest_pass(self):
        config = Config(read=False)
        config.root = self.use_temp_dir()
        manifest = os.path.join(self.temp_dir, "www", "simple", ".manifest")
        os.makedirs(os.path.dirname(manifest))
        with open(manifest, "w") as f:
            print(
                "ubuntu\tprecise\t/precise/ubuntu-12.04.2-desktop-i386.iso\t"
                "726970368", file=f)
        touch(os.path.join(
            self.temp_dir, "www", "simple", "precise",
            "ubuntu-12.04.2-desktop-i386.iso"))

    def configure_triggers(self):
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.config["TRIGGER_MIRRORS"] = "foo bar"
        self.config["TRIGGER_MIRRORS_ASYNC"] = "foo-async bar-async"
        self.home_secret = os.path.join(self.temp_dir, "home", "secret")

    @mock.patch("os.path.expanduser")
    @mock.patch("cdimage.mirror._trigger_mirror")
    def test_trigger_mirrors(self, mock_trigger_mirror, mock_expanduser):
        self.configure_triggers()
        mock_expanduser.return_value = self.home_secret
        key = os.path.join(self.temp_dir, "secret", "auckland")
        trigger_mirrors(self.config)
        mock_trigger_mirror.assert_has_calls([
            mock.call(key, "archvsync", "foo"),
            mock.call(key, "archvsync", "bar"),
            mock.call(key, "archvsync", "foo-async", background=True),
            mock.call(key, "archvsync", "bar-async", background=True),
        ])

    @mock.patch("os.path.expanduser")
    @mock.patch("cdimage.mirror._trigger_mirror")
    def test_trigger_mirrors_home_secret(self, mock_trigger_mirror,
                                         mock_expanduser):
        """If ~/secret exists, it is preferred over $CDIMAGE_ROOT/secret."""
        self.configure_triggers()
        mock_expanduser.return_value = self.home_secret
        os.makedirs(self.home_secret)
        key = os.path.join(self.home_secret, "auckland")
        trigger_mirrors(self.config)
        mock_trigger_mirror.assert_has_calls([
            mock.call(key, "archvsync", "foo"),
            mock.call(key, "archvsync", "bar"),
            mock.call(key, "archvsync", "foo-async", background=True),
            mock.call(key, "archvsync", "bar-async", background=True),
        ])
