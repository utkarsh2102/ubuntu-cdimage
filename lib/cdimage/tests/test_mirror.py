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

import os

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.config import Config, all_series
from cdimage.mirror import (
    UnknownManifestFile,
    _get_mirror_key,
    _get_mirrors,
    _get_mirrors_async,
    _trigger_command,
    _trigger_mirror,
    check_manifest,
    find_mirror,
    trigger_mirrors,
)
from cdimage.tests.helpers import TestCase, mkfile, touch

__metaclass__ = type


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

    def test_powerpc(self):
        for series in all_series:
            self.assertMirrorEqual("ftp", "powerpc", series)

    def test_ppc64el(self):
        for series in all_series:
            self.assertMirrorEqual("ftp", "ppc64el", series)

    def test_s390x(self):
        for series in all_series:
            self.assertMirrorEqual("ftp", "s390x", series)

    def test_sparc(self):
        for series in all_series:
            self.assertMirrorEqual("ftp", "sparc", series)


class TestTriggerMirrors(TestCase):
    def test_check_manifest_no_manifest(self):
        config = Config(read=False)
        config.root = self.use_temp_dir()
        check_manifest(config)

    def test_check_manifest_unknown_file(self):
        config = Config(read=False)
        config.root = self.use_temp_dir()
        manifest = os.path.join(self.temp_dir, "www", "simple", ".manifest")
        with mkfile(manifest) as f:
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
        with mkfile(manifest) as f:
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
    def test_get_mirror_key(self, mock_expanduser):
        """If ~/secret exists, it is preferred over $CDIMAGE_ROOT/secret."""
        self.configure_triggers()
        mock_expanduser.return_value = self.home_secret
        key = os.path.join(self.temp_dir, "secret", "auckland")
        self.assertEqual(key, _get_mirror_key(self.config))
        os.makedirs(self.home_secret)
        key = os.path.join(self.home_secret, "auckland")
        self.assertEqual(key, _get_mirror_key(self.config))

    def test_get_mirrors(self):
        config = Config(read=False)
        config.root = self.use_temp_dir()
        production_path = os.path.join(
            self.temp_dir, "production", "trigger-mirrors")
        os.makedirs(os.path.dirname(production_path))
        with mkfile(production_path) as production:
            print("sync x.example.org", file=production)
            print("async other.example.org", file=production)
            print("sync y.example.org z.example.org", file=production)
        self.assertEqual(
            ["x.example.org", "y.example.org", "z.example.org"],
            _get_mirrors(config))
        self.configure_triggers()
        self.assertEqual(["foo", "bar"], _get_mirrors(self.config))
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        self.assertEqual(["strix.canonical.com"], _get_mirrors(self.config))

    def test_get_mirrors_async(self):
        config = Config(read=False)
        config.root = self.use_temp_dir()
        production_path = os.path.join(
            self.temp_dir, "production", "trigger-mirrors")
        with mkfile(production_path) as production:
            print("sync x.example.org", file=production)
            print("async a.example.org b.example.org", file=production)
            print("sync y.example.org z.example.org", file=production)
            print("async c.example.org", file=production)
        self.assertEqual(
            ["a.example.org", "b.example.org", "c.example.org"],
            _get_mirrors_async(config))
        self.configure_triggers()
        self.assertEqual(
            ["foo-async", "bar-async"], _get_mirrors_async(self.config))
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        self.assertEqual([], _get_mirrors_async(self.config))

    def test_trigger_command(self):
        config = Config(read=False)
        self.assertEqual("./releases-sync", _trigger_command(config))
        config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        self.assertEqual("./china-sync", _trigger_command(config))

    @mock.patch("subprocess.Popen")
    def test_trigger_mirror_background(self, mock_popen):
        config = Config(read=False)
        self.capture_logging()
        _trigger_mirror(
            config, "id-test", "archvsync", "remote", background=True)
        self.assertLogEqual(["remote:"])
        mock_popen.assert_called_once_with([
            "ssh", "-i", "id-test",
            "-o", "StrictHostKeyChecking no",
            "-o", "BatchMode yes",
            "archvsync@remote",
            "./releases-sync",
        ])

    @mock.patch("subprocess.call", return_value=0)
    def test_trigger_mirror_foreground(self, mock_call):
        config = Config(read=False)
        self.capture_logging()
        _trigger_mirror(config, "id-test", "archvsync", "remote")
        self.assertLogEqual(["remote:"])
        mock_call.assert_called_once_with([
            "ssh", "-i", "id-test",
            "-o", "StrictHostKeyChecking no",
            "-o", "BatchMode yes",
            "archvsync@remote",
            "./releases-sync",
        ])

    @mock.patch("os.path.expanduser")
    @mock.patch("cdimage.mirror._trigger_mirror")
    def test_trigger_mirrors(self, mock_trigger_mirror, mock_expanduser):
        self.configure_triggers()
        mock_expanduser.return_value = self.home_secret
        key = os.path.join(self.temp_dir, "secret", "auckland")
        trigger_mirrors(self.config)
        mock_trigger_mirror.assert_has_calls([
            mock.call(self.config, key, "archvsync", "foo"),
            mock.call(self.config, key, "archvsync", "bar"),
            mock.call(
                self.config, key, "archvsync", "foo-async", background=True),
            mock.call(
                self.config, key, "archvsync", "bar-async", background=True),
        ])
