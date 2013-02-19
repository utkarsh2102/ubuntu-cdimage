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

"""Atomic counting semaphore files."""

from __future__ import print_function

__metaclass__ = type

import errno
import os
import subprocess

from cdimage import osextras


class SemaphoreError(Exception):
    pass


class Semaphore:
    """A shared lock which only opens when all users have unlocked."""

    def __init__(self, path):
        self.path = path
        self.lock_path = "%s.lock" % path

    def __str__(self):
        return "semaphore %s" % self.path

    def __enter__(self):
        command = ["lockfile", "-r", "4", self.lock_path]
        if subprocess.call(command) != 0:
            raise SemaphoreError("Cannot acquire lock on %s!" % self)

    def __exit__(self, unused_exc_type, unused_exc_value, unused_exc_tb):
        try:
            osextras.unlink_force(self.lock_path)
        except OSError:
            pass

    def _read(self):
        # Must be called within context manager lock.
        assert os.path.exists(self.lock_path), (
            "Called _read on %s without locking!" % self)
        try:
            with open(self.path) as fd:
                return int(fd.read())
        except IOError as e:
            if e.errno == errno.ENOENT:
                return 0
            raise

    def _add(self, offset):
        # Must be called within context manager lock.
        assert os.path.exists(self.lock_path), (
            "Called _add on %s without locking!" % self)
        cur = self._read()
        with open(self.path, "w") as fd:
            print(cur + offset, file=fd)
        return cur + offset

    def test_increment(self):
        """Test, increment, return state of test."""
        with self:
            state = self._read()
            self._add(1)
            return state

    def decrement_test(self):
        """Decrement, test, return state of test.

        It is an error to call decrement-test on a semaphore that is already
        zero.
        """
        with self:
            state = self._read()
            if state == 0:
                osextras.unlink_force(self.path)
                raise SemaphoreError(
                    "Attempted to decrement %s when already zero!" % self)
            state = self._add(-1)
            if state == 0:
                osextras.unlink_force(self.path)
            return state
