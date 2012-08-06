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

"""Checksum file handling."""

from __future__ import print_function

__metaclass__ = type

import hashlib
import os
import re

from cdimage.atomicfile import AtomicFile
from cdimage.sign import sign_cdimage


class ChecksumFile:
    """Manipulate a single checksum file."""

    def __init__(self, config, directory, name, hash_method, sign=True):
        self.config = config
        self.directory = directory
        self.name = name
        self.path = os.path.join(directory, name)
        self.hash_method = hash_method
        self.sign = sign
        self.entries = {}

    def read(self):
        self.entries = {}
        if not os.path.exists(self.path):
            return
        with open(self.path) as checksums:
            for line in checksums:
                bits = re.split("[ *]+", line.rstrip("\n"), maxsplit=1)
                if len(bits) == 2:
                    self.entries[bits[1]] = bits[0]

    def checksum(self, entry_path):
        with open(entry_path, "rb") as fh:
            hash_obj = self.hash_method()
            while True:
                buf = fh.read(16 * 1024)
                if not buf:
                    break
                hash_obj.update(buf)
            return hash_obj.hexdigest()

    def add(self, entry_name):
        entry_path = os.path.join(self.directory, entry_name)
        self.entries[entry_name] = self.checksum(entry_path)

    def remove(self, entry_name):
        self.entries.pop(entry_name, None)

    def write(self):
        if self.entries:
            with AtomicFile(self.path) as checksums:
                for entry_name in sorted(self.entries):
                    print("%s *%s" % (self.entries[entry_name], entry_name),
                          file=checksums)
            if self.sign:
                sign_cdimage(self.config, self.path)
        else:
            try:
                os.unlink(self.path)
            except OSError:
                pass

    def __enter__(self):
        self.read()
        return self

    def __exit__(self, exc_type, unused_exc_value, unused_exc_tb):
        if exc_type is None:
            self.write()


_checksum_files = {
    "MD5SUMS": hashlib.md5,
    "SHA1SUMS": hashlib.sha1,
    "SHA256SUMS": hashlib.sha256,
    }


class ChecksumFileSet:
    """Manipulate the standard set of checksums files together."""

    def __init__(self, config, directory, sign=True):
        self.checksum_files = [
            ChecksumFile(config, directory, filename, hash_method, sign=sign)
            for filename, hash_method in _checksum_files.items()]

    def read(self):
        for checksum_file in self.checksum_files:
            checksum_file.read()

    def add(self, entry_name):
        for checksum_file in self.checksum_files:
            checksum_file.add(entry_name)

    def remove(self, entry_name):
        for checksum_file in self.checksum_files:
            checksum_file.remove(entry_name)

    def write(self):
        for checksum_file in self.checksum_files:
            checksum_file.write()

    def __enter__(self):
        self.read()
        return self

    def __exit__(self, exc_type, unused_exc_value, unused_exc_tb):
        if exc_type is None:
            self.write()
