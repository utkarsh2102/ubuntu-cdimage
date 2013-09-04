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

"""Unit tests for cdimage.osextras."""

from __future__ import print_function

import errno
import os
from textwrap import dedent

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage import osextras
from cdimage.config import Config
from cdimage.tests.helpers import TestCase, mkfile, touch


class TestOSExtras(TestCase):
    def setUp(self):
        super(TestOSExtras, self).setUp()
        self.use_temp_dir()

    def test_ensuredir_previously_missing(self):
        new_dir = os.path.join(self.temp_dir, "dir")
        osextras.ensuredir(new_dir)
        self.assertTrue(os.path.isdir(new_dir))

    def test_ensuredir_previously_present(self):
        new_dir = os.path.join(self.temp_dir, "dir")
        os.mkdir(new_dir)
        osextras.ensuredir(new_dir)
        self.assertTrue(os.path.isdir(new_dir))

    def test_mkemptydir_previously_missing(self):
        new_dir = os.path.join(self.temp_dir, "dir")
        osextras.mkemptydir(new_dir)
        self.assertTrue(os.path.isdir(new_dir))
        self.assertEqual([], os.listdir(new_dir))

    def test_mkemptydir_previously_populated(self):
        new_dir = os.path.join(self.temp_dir, "dir")
        touch(os.path.join(new_dir, "file"))
        osextras.mkemptydir(new_dir)
        self.assertTrue(os.path.isdir(new_dir))
        self.assertEqual([], os.listdir(new_dir))

    def test_listdir_directory_present(self):
        new_dir = os.path.join(self.temp_dir, "dir")
        touch(os.path.join(new_dir, "file"))
        self.assertEqual(["file"], osextras.listdir_force(new_dir))

    def test_listdir_directory_missing(self):
        new_dir = os.path.join(self.temp_dir, "dir")
        self.assertEqual([], osextras.listdir_force(new_dir))

    def test_listdir_oserror(self):
        not_dir = os.path.join(self.temp_dir, "file")
        touch(not_dir)
        self.assertRaises(OSError, osextras.listdir_force, not_dir)

    def test_unlink_file_present(self):
        path = os.path.join(self.temp_dir, "file")
        touch(path)
        osextras.unlink_force(path)
        self.assertFalse(os.path.exists(path))

    def test_unlink_file_missing(self):
        path = os.path.join(self.temp_dir, "file")
        osextras.unlink_force(path)
        self.assertFalse(os.path.exists(path))

    def test_unlink_oserror(self):
        path = os.path.join(self.temp_dir, "dir")
        os.mkdir(path)
        self.assertRaises(OSError, osextras.unlink_force, path)

    def test_symlink_file_present(self):
        path = os.path.join(self.temp_dir, "link")
        touch(path)
        osextras.symlink_force("source", path)
        self.assertTrue(os.path.islink(path))
        self.assertEqual("source", os.readlink(path))

    def test_symlink_link_present(self):
        path = os.path.join(self.temp_dir, "link")
        os.symlink("old", path)
        osextras.symlink_force("source", path)
        self.assertTrue(os.path.islink(path))
        self.assertEqual("source", os.readlink(path))

    def test_symlink_missing(self):
        path = os.path.join(self.temp_dir, "link")
        osextras.symlink_force("source", path)
        self.assertTrue(os.path.islink(path))
        self.assertEqual("source", os.readlink(path))

    def test_link_present(self):
        source = os.path.join(self.temp_dir, "source")
        touch(source)
        target = os.path.join(self.temp_dir, "target")
        touch(target)
        osextras.link_force(source, target)
        self.assertEqual(os.stat(source), os.stat(target))

    def test_link_missing(self):
        source = os.path.join(self.temp_dir, "source")
        touch(source)
        target = os.path.join(self.temp_dir, "target")
        osextras.link_force(source, target)
        self.assertEqual(os.stat(source), os.stat(target))

    def test_find_on_path_missing_environment(self):
        os.environ.pop("PATH", None)
        self.assertFalse(osextras.find_on_path("ls"))

    def test_find_on_path_present_executable(self):
        bin_dir = os.path.join(self.temp_dir, "bin")
        program = os.path.join(bin_dir, "program")
        touch(program)
        os.chmod(program, 0o755)
        os.environ["PATH"] = bin_dir
        self.assertTrue(osextras.find_on_path("program"))

    def test_find_on_path_present_not_executable(self):
        bin_dir = os.path.join(self.temp_dir, "bin")
        touch(os.path.join(bin_dir, "program"))
        os.environ["PATH"] = bin_dir
        self.assertFalse(osextras.find_on_path("program"))

    @mock.patch("os.waitpid")
    def test_waitpid_retry(self, mock_waitpid):
        class Completed(Exception):
            pass

        waitpid_called = [False]

        def waitpid_side_effect(*args, **kwargs):
            if not waitpid_called[0]:
                waitpid_called[0] = True
                raise OSError(errno.EINTR, "")
            else:
                raise Completed

        mock_waitpid.side_effect = waitpid_side_effect
        self.assertRaises(Completed, osextras.waitpid_retry, -1, 0)

    def test_run_bounded_runs(self):
        sentinel = os.path.join(self.temp_dir, "foo")
        osextras.run_bounded(3600, ["touch", sentinel])
        self.assertTrue(os.path.exists(sentinel))

    def test_run_bounded_finite(self):
        osextras.run_bounded(.1, ["sh", "-c", "while :; do sleep 3600; done"])

    def test_fetch_empty(self):
        config = Config(read=False)
        target = os.path.join(self.temp_dir, "target")
        self.assertRaises(
            osextras.FetchError, osextras.fetch, config, "", target)
        self.assertFalse(os.path.exists(target))

    def test_fetch_file(self):
        config = Config(read=False)
        source = os.path.join(self.temp_dir, "source")
        touch(source)
        target = os.path.join(self.temp_dir, "target")
        osextras.fetch(config, source, target)
        self.assertTrue(os.path.exists(target))
        self.assertEqual(os.stat(target), os.stat(source))

    @mock.patch("subprocess.call", return_value=1)
    def test_fetch_url_removes_target_on_failure(self, *args):
        config = Config(read=False)
        target = os.path.join(self.temp_dir, "target")
        touch(target)
        self.assertRaises(
            osextras.FetchError, osextras.fetch, config,
            "http://example.org/source", target)
        self.assertFalse(os.path.exists(target))

    @mock.patch("subprocess.call", return_value=0)
    def test_fetch_url(self, mock_call):
        config = Config(read=False)
        target = os.path.join(self.temp_dir, "target")
        osextras.fetch(config, "http://example.org/source", target)
        self.assertEqual(1, mock_call.call_count)
        self.assertEqual(
            ["wget", "-nv", "http://example.org/source", "-O", target],
            mock_call.call_args[0][0])

    def test_shell_escape(self):
        self.assertEqual("foo", osextras.shell_escape("foo"))
        self.assertEqual("'  '", osextras.shell_escape("  "))
        self.assertEqual(
            "'shell'\\''s great'", osextras.shell_escape("shell's great"))

    def test_read_shell_config(self):
        os.environ["ONE"] = "one"
        config_path = os.path.join(self.temp_dir, "config")
        with mkfile(config_path) as config:
            print(dedent("""\
                ONE="$ONE two three"
                TWO=two
                THREE=three"""), file=config)
        config_dict = dict(
            osextras.read_shell_config(config_path, ["ONE", "TWO"]))
        self.assertEqual("one two three", config_dict["ONE"])
        self.assertEqual("two", config_dict["TWO"])
        self.assertNotIn("three", config_dict)
