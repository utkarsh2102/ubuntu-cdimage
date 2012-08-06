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

"""Unit tests for cdimage.checksums."""

from __future__ import print_function

__metaclass__ = type

import hashlib
import os
import subprocess
from textwrap import dedent

from cdimage.checksums import ChecksumFile, ChecksumFileSet
from cdimage.config import Config
from cdimage.tests.helpers import TestCase


class TestChecksumFile(TestCase):
    def setUp(self):
        super(TestChecksumFile, self).setUp()
        self.config = Config(read=False)
        self.use_temp_dir()

    def test_read(self):
        with open(os.path.join(self.temp_dir, "MD5SUMS"), "w") as md5sums:
            print(dedent("""\
                checksum  one-path
                checksum *another-path
                """), file=md5sums)
        checksum_file = ChecksumFile(
            self.config, self.temp_dir, "MD5SUMS", hashlib.md5)
        checksum_file.read()
        self.assertEqual(
            {"one-path": "checksum", "another-path": "checksum"},
            checksum_file.entries)

    def test_read_missing(self):
        checksum_file = ChecksumFile(
            self.config, self.temp_dir, "MD5SUMS", hashlib.md5)
        checksum_file.read()
        self.assertEqual({}, checksum_file.entries)

    def test_checksum_small_file(self):
        entry_path = os.path.join(self.temp_dir, "entry")
        data = "test\n"
        with open(entry_path, "w") as entry:
            print(data, end="", file=entry)
        checksum_file = ChecksumFile(
            self.config, self.temp_dir, "MD5SUMS", hashlib.md5)
        self.assertEqual(
            hashlib.md5(data).hexdigest(), checksum_file.checksum(entry_path))

    def test_checksum_large_file(self):
        entry_path = os.path.join(self.temp_dir, "entry")
        data = "a" * 1048576
        with open(entry_path, "w") as entry:
            print(data, end="", file=entry)
        checksum_file = ChecksumFile(
            self.config, self.temp_dir, "SHA1SUMS", hashlib.sha1)
        self.assertEqual(
            hashlib.sha1(data).hexdigest(), checksum_file.checksum(entry_path))

    def test_add(self):
        entry_path = os.path.join(self.temp_dir, "entry")
        data = "test\n"
        with open(entry_path, "w") as entry:
            print(data, end="", file=entry)
        checksum_file = ChecksumFile(
            self.config, self.temp_dir, "MD5SUMS", hashlib.md5)
        checksum_file.add("entry")
        self.assertEqual(
            {"entry": hashlib.md5(data).hexdigest()}, checksum_file.entries)

    def test_remove(self):
        checksum_file = ChecksumFile(
            self.config, self.temp_dir, "MD5SUMS", hashlib.md5)
        checksum_file.entries["entry"] = "checksum"
        checksum_file.remove("entry")
        self.assertEqual({}, checksum_file.entries)

    def test_write(self):
        checksum_file = ChecksumFile(
            self.config, self.temp_dir, "MD5SUMS", hashlib.md5, sign=False)
        for name in "1", "2":
            entry_path = os.path.join(self.temp_dir, name)
            with open(entry_path, "w") as entry:
                print(name, end="", file=entry)
            checksum_file.add(name)
        checksum_file.write()
        with open(checksum_file.path) as md5sums:
            self.assertEqual(dedent("""\
                %s *1
                %s *2
                """) %
                (hashlib.md5("1").hexdigest(), hashlib.md5("2").hexdigest()),
                md5sums.read())
        self.assertEqual(
            0,
            subprocess.call(
                ["md5sum", "-c", "--status", "MD5SUMS"], cwd=self.temp_dir))

    def test_context_manager(self):
        for name in "1", "2":
            entry_path = os.path.join(self.temp_dir, name)
            with open(entry_path, "w") as entry:
                print(name, end="", file=entry)
        md5sums_path = os.path.join(self.temp_dir, "MD5SUMS")
        with open(md5sums_path, "w") as md5sums:
            subprocess.call(
                ["md5sum", "-b", "1", "2"], stdout=md5sums, cwd=self.temp_dir)
        with ChecksumFile(
            self.config, self.temp_dir, "MD5SUMS", hashlib.md5,
            sign=False) as checksum_file:
            self.assertEqual(["1", "2"], sorted(checksum_file.entries))
            checksum_file.remove("1")
        with open(md5sums_path) as md5sums:
            self.assertEqual(
                "%s *2\n" % hashlib.md5("2").hexdigest(), md5sums.read())


class TestChecksumFileSet(TestCase):
    def setUp(self):
        super(TestChecksumFileSet, self).setUp()
        self.config = Config(read=False)
        self.use_temp_dir()
        self.files_and_commands = {
            "MD5SUMS": "md5sum",
            "SHA1SUMS": "sha1sum",
            "SHA256SUMS": "sha256sum",
            }

    def create_checksum_files(self, names):
        for base, command in self.files_and_commands.items():
            with open(os.path.join(self.temp_dir, base), "w") as f:
                subprocess.call(
                    [command, "-b"] + names, stdout=f, cwd=self.temp_dir)

    def assertChecksumsEqual(self, entry_data, checksum_files):
        expected = {
            "MD5SUMS": dict(
                (k, hashlib.md5(v).hexdigest())
                for k, v in entry_data.items()),
            "SHA1SUMS": dict(
                (k, hashlib.sha1(v).hexdigest())
                for k, v in entry_data.items()),
            "SHA256SUMS": dict(
                (k, hashlib.sha256(v).hexdigest())
                for k, v in entry_data.items()),
            }
        observed = dict(
            (cf.name, cf.entries) for cf in checksum_files.checksum_files)
        self.assertEqual(expected, observed)

    def test_read(self):
        entry_path = os.path.join(self.temp_dir, "entry")
        with open(entry_path, "w") as entry:
            print("data", end="", file=entry)
        self.create_checksum_files(["entry"])
        for base, command in self.files_and_commands.items():
            with open(os.path.join(self.temp_dir, base), "w") as f:
                subprocess.call(
                    [command, "-b", "entry"], stdout=f, cwd=self.temp_dir)
        checksum_files = ChecksumFileSet(self.config, self.temp_dir)
        checksum_files.read()
        self.assertChecksumsEqual({"entry": "data"}, checksum_files)

    def test_add(self):
        entry_path = os.path.join(self.temp_dir, "entry")
        data = "test\n"
        with open(entry_path, "w") as entry:
            print(data, end="", file=entry)
        checksum_files = ChecksumFileSet(self.config, self.temp_dir)
        checksum_files.add("entry")
        self.assertChecksumsEqual({"entry": "test\n"}, checksum_files)

    def test_remove(self):
        entry_path = os.path.join(self.temp_dir, "entry")
        data = "test\n"
        with open(entry_path, "w") as entry:
            print(data, end="", file=entry)
        self.create_checksum_files(["entry"])
        checksum_files = ChecksumFileSet(self.config, self.temp_dir)
        checksum_files.read()
        checksum_files.remove("entry")
        self.assertChecksumsEqual({}, checksum_files)

    def test_write(self):
        checksum_files = ChecksumFileSet(
            self.config, self.temp_dir, sign=False)
        for name in "1", "2":
            entry_path = os.path.join(self.temp_dir, name)
            with open(entry_path, "w") as entry:
                print(name, end="", file=entry)
            checksum_files.add(name)
        checksum_files.write()
        for cf in checksum_files.checksum_files:
            self.assertEqual(
                0,
                subprocess.call(
                    [self.files_and_commands[cf.name], "-c", "--status",
                     cf.name], cwd=self.temp_dir))

    def test_context_manager(self):
        for name in "1", "2":
            entry_path = os.path.join(self.temp_dir, name)
            with open(entry_path, "w") as entry:
                print(name, end="", file=entry)
        self.create_checksum_files(["1", "2"])
        with ChecksumFileSet(
            self.config, self.temp_dir, sign=False) as checksum_files:
            self.assertChecksumsEqual({"1": "1", "2": "2"}, checksum_files)
            checksum_files.remove("1")
        with open(os.path.join(self.temp_dir, "MD5SUMS")) as md5sums:
            self.assertEqual(
                "%s *2\n" % hashlib.md5("2").hexdigest(), md5sums.read())
        with open(os.path.join(self.temp_dir, "SHA1SUMS")) as sha1sums:
            self.assertEqual(
                "%s *2\n" % hashlib.sha1("2").hexdigest(), sha1sums.read())
        with open(os.path.join(self.temp_dir, "SHA256SUMS")) as sha256sums:
            self.assertEqual(
                "%s *2\n" % hashlib.sha256("2").hexdigest(), sha256sums.read())
