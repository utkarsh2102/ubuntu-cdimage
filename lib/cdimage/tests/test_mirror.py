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

from __future__ import print_function, with_statement

import glob
import os
import subprocess
import sys

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.config import Config, all_series
from cdimage.mirror import (
    AptStateManager,
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
            base, find_mirror(config, arch))

    def test_amd64(self):
        for series in all_series:
            self.assertMirrorEqual(
                "http://ftpmaster.internal/ubuntu/", "amd64", series)

    def test_armel(self):
        for series in all_series:
            self.assertMirrorEqual(
                "http://ftpmaster.internal/ubuntu/", "armel", series)

    def test_i386(self):
        for series in all_series:
            self.assertMirrorEqual(
                "http://ftpmaster.internal/ubuntu/", "i386", series)

    def test_ppc64el(self):
        for series in all_series:
            self.assertMirrorEqual(
                "http://ftpmaster.internal/ubuntu/", "ppc64el", series)

    def test_s390x(self):
        for series in all_series:
            self.assertMirrorEqual(
                "http://ftpmaster.internal/ubuntu/", "s390x", series)


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
                "ubuntu\tbionic\t/bionic/ubuntu-18.04.2-desktop-i386.iso\t"
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
                "ubuntu\tbionic\t/bionic/ubuntu-18.04.2-desktop-i386.iso\t"
                "726970368", file=f)
        touch(os.path.join(
            self.temp_dir, "www", "simple", "bionic",
            "ubuntu-18.04.2-desktop-i386.iso"))

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

    def test_trigger_command(self):
        config = Config(read=False)
        self.assertEqual("./releases-sync", _trigger_command(config))

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

    @mock.patch("cdimage.mirror._trigger_mirror")
    def test_no_trigger_mirrors_when_stopped(self, mock_trigger_mirror):
        self.configure_triggers()
        os.mkdir(os.path.join(self.config.root, "etc"))
        with open(os.path.join(self.config.root, "etc", "STOP_SYNC_MIRRORS"),
                  "w"):
            trigger_mirrors(self.config)
            mock_trigger_mirror.assert_not_called()


class TestAptStateManager(TestCase):
    def test_output_dir(self):
        config = Config(read=False)
        config.root = "/cdimage"
        config["PROJECT"] = "ubuntu"
        config["DIST"] = "noble"
        config["IMAGE_TYPE"] = "daily"
        mgr = AptStateManager(config)
        self.assertEqual(
            "/cdimage/scratch/ubuntu/noble/daily/apt-state/amd64",
            mgr._output_dir("amd64"))

    def test_otherarch_no_foreign_arch(self):
        config = Config(read=False)
        config.root = self.use_temp_dir()
        mgr = AptStateManager(config)
        self.assertEqual("arm64", mgr._otherarch("arm64"))

    def test_otherarch_with_foreign_arch(self):
        config = Config(read=False)
        config.root = self.use_temp_dir()
        config["DIST"] = "noble"
        # This is duplicating the implementation a bit too much to be
        # a truly useful tests. But really this information should be
        # moved into ubuntu-cdimage somewhere.
        archlist_path = os.path.join(
            config.root, "debian-cd", "data", config.series,
            "multiarch", "arm64")
        os.makedirs(os.path.dirname(archlist_path))
        with open(archlist_path, 'w') as fp:
            fp.write("armhf\n")
        mgr = AptStateManager(config)
        self.assertEqual("armhf", mgr._otherarch("arm64"))

    def test_components(self):
        config = Config(read=False)
        mgr = AptStateManager(config)
        self.assertEqual("main restricted", mgr._components())
        config["CDIMAGE_ONLYFREE"] = "1"
        self.assertEqual("main", mgr._components())
        config["CDIMAGE_UNSUPPORTED"] = "1"
        self.assertEqual("main universe", mgr._components())
        del config["CDIMAGE_ONLYFREE"]
        self.assertEqual(
            "main restricted universe multiverse", mgr._components())

    def test_suites(self):
        config = Config(read=False)
        config["DIST"] = "noble"
        mgr = AptStateManager(config)
        self.assertEqual("noble noble-security noble-updates", mgr._suites())
        config["PROPOSED"] = "1"
        self.assertEqual(
            "noble noble-security noble-updates noble-proposed", mgr._suites())

    @mock.patch("cdimage.mirror.find_mirror")
    @mock.patch("cdimage.mirror.AptStateManager._components")
    @mock.patch("cdimage.mirror.AptStateManager._suites")
    def test_get_sources_text(self, m_suites, m_components, m_find_mirror):
        m_find_mirror.return_value = "MIRROR"
        m_components.return_value = "COMPONENTS"
        m_suites.return_value = "SUITES"
        config = Config(read=False)
        config["DIST"] = "noble"
        mgr = AptStateManager(config)
        expected = """\
Types: deb deb-src
URIs: MIRROR
Suites: SUITES
Components: COMPONENTS
Signed-By: /etc/apt/trusted.gpg.d/ubuntu-keyring-2018-archive.gpg
"""
        self.assertEqual(expected, mgr._get_sources_text("s390x"))

    def test_get_sources_text_override(self):
        config = Config(read=False)
        sources_path = os.path.join(self.use_temp_dir(), "my.sources")
        content = "# content\n"
        with open(sources_path, 'w') as fp:
            fp.write(content)
        config["CDIMAGE_POOL_SOURCES"] = sources_path
        mgr = AptStateManager(config)
        self.assertEqual(content, mgr._get_sources_text("s390x"))

    def _get_apt_config(self, apt_config, var, meth='find'):
        env = dict(os.environ, APT_CONFIG=apt_config)
        cmd = [
            sys.executable,
            "-c",
            "import apt_pkg, sys; apt_pkg.init_config()\n"
            "print(apt_pkg.config.{}(sys.argv[1]))".format(meth),
            var
            ]
        cp = subprocess.run(
            cmd, env=env, encoding='utf-8', stdout=subprocess.PIPE, check=True)
        return cp.stdout.strip()

    @mock.patch("cdimage.mirror.AptStateManager._output_dir")
    @mock.patch("cdimage.mirror.AptStateManager._get_sources_text")
    @mock.patch("cdimage.mirror.AptStateManager._otherarch")
    def test_setup_arch(self, m_otherarch, m_get_sources_text, m_output_dir):
        m_otherarch.return_value = "OTHERARCH"
        # We *could* create a local apt repo with apt-ftparchive and
        # arrange to point the apt update call _setup_arch does to it
        # but that seems like a lot of effort.
        m_get_sources_text.return_value = "# Our content\n"
        m_output_dir.return_value = self.use_temp_dir()
        config = Config(read=False)
        config["DIST"] = "noble"
        mgr = AptStateManager(config)
        apt_conf = mgr._setup_arch("ARCH")
        self.assertEqual(
            "ARCH",
            self._get_apt_config(apt_conf, "Apt::Architecture"))
        self.assertEqual(
            "OTHERARCH",
            self._get_apt_config(apt_conf, "Apt::Architectures"))
        sources_list_d = self._get_apt_config(
            apt_conf, "Dir::Etc::sourceparts", meth='find_dir')
        sources_files = glob.glob(os.path.join(sources_list_d, "*.sources"))
        self.assertEqual(1, len(sources_files))
        with open(sources_files[0]) as fp:
            self.assertEqual("# Our content\n", fp.read())

    @mock.patch("subprocess.check_call")
    def test_setup_arch_proxy(self, m_check_call):
        config = Config(read=False)
        config["DIST"] = "noble"
        config["APT_PROXY"] = "http://localhost:3128"
        mgr = AptStateManager(config)
        apt_conf = mgr._setup_arch("ARCH")
        self.assertEqual(
            "http://localhost:3128",
            self._get_apt_config(apt_conf, "Acquire::http::Proxy"))
        self.assertEqual(
            "http://localhost:3128",
            self._get_apt_config(apt_conf, "Acquire::https::Proxy"))

    @mock.patch("cdimage.mirror.AptStateManager._setup_arch")
    def test_setup(self, m_setup_arch):
        self.capture_logging()
        m_setup_arch.side_effect = lambda arch: arch + 'dir'
        config = Config(read=False)
        config["ARCHES"] = "arch1 arch2"
        mgr = AptStateManager(config)
        mgr.setup()
        self.assertEqual(
            [mock.call("arch1"), mock.call("arch2")],
            m_setup_arch.mock_calls)
        self.assertEqual('arch1dir', mgr.apt_conf_for_arch('arch1'))
