# Copyright (C) 2013, 2016 Canonical Ltd.
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

"""PID files containing multiple PIDs."""

from __future__ import print_function

import contextlib
import errno
import os
import subprocess

from cdimage import osextras

__metaclass__ = type


class MultiPIDFileError(Exception):
    pass


class MultiPIDFile:
    """A file tracking multiple PIDs."""

    def __init__(self, path):
        self.path = path
        self.lock_path = "%s.lock" % path

    def __str__(self):
        return "multipidfile %s" % self.path

    def __enter__(self):
        command = ["lockfile", "-r", "4", self.lock_path]
        if subprocess.call(command) != 0:
            raise MultiPIDFileError("Cannot acquire lock on %s!" % self)

    def __exit__(self, unused_exc_type, unused_exc_value, unused_exc_tb):
        osextras.unlink_force(self.lock_path)

    def _read(self):
        # Must be called within context manager lock.
        assert os.path.exists(self.lock_path), (
            "Called _read on %s without locking!" % self)
        try:
            with open(self.path) as fd:
                pids = set(int(line) for line in fd)
                return set(pid for pid in pids if osextras.pid_exists(pid))
        except IOError as e:
            if e.errno == errno.ENOENT:
                return set()
            raise

    def _write(self, pids):
        # Must be called within context manager lock.
        assert os.path.exists(self.lock_path), (
            "Called _write on %s without locking!" % self)
        if pids:
            with open(self.path, "w") as fd:
                for pid in sorted(pids):
                    print(pid, file=fd)
        else:
            osextras.unlink_force(self.path)

    @property
    def state(self):
        """Return current set of tracked PIDs."""
        with self:
            return self._read()

    def test_add(self, pid):
        """Test, add PID, return state of test.

        It is an error to add a PID that is already present.
        """
        with self:
            pids = self._read()
            if pid in pids:
                raise MultiPIDFileError(
                    "Attempted to add PID %d to %s which was already "
                    "present!" % (pid, self))
            state = set(pids)
            pids.add(pid)
            self._write(pids)
            return state

    def remove_test(self, pid):
        """Remove PID, test, return state of test.

        It is an error to remove a PID that is not already present.
        """
        with self:
            pids = self._read()
            try:
                pids.remove(pid)
            except KeyError:
                raise MultiPIDFileError(
                    "Attempted to remove PID %d from %s which was not "
                    "present!" % (pid, self))
            self._write(pids)
            return pids

    @contextlib.contextmanager
    def held(self, pid):
        try:
            yield self.test_add(pid)
        finally:
            self.remove_test(pid)
