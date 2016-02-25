#! /usr/bin/python

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

"""Unit tests for cdimage.multipidfile."""

from __future__ import print_function

import os

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.multipidfile import MultiPIDFile, MultiPIDFileError
from cdimage.tests.helpers import TestCase, mkfile

__metaclass__ = type


class TestMultiPIDFile(TestCase):
    def setUp(self):
        super(TestMultiPIDFile, self).setUp()
        self.use_temp_dir()
        self.multipidfile = MultiPIDFile(os.path.join(self.temp_dir, "pids"))

    def test_str(self):
        """A MultiPIDFile stringifies to 'multipidfile PATH'."""
        self.assertEqual(
            "multipidfile %s" % self.multipidfile.path, str(self.multipidfile))

    def test_context_manager(self):
        """A MultiPIDFile operates as a context manager, with locking."""
        self.assertFalse(os.path.exists(self.multipidfile.lock_path))
        with self.multipidfile:
            self.assertTrue(os.path.exists(self.multipidfile.lock_path))
        self.assertFalse(os.path.exists(self.multipidfile.lock_path))

    @mock.patch("subprocess.call", return_value=1)
    def test_lock_failure(self, mock_call):
        """__enter__ raises MultiPIDFileError if the lock is already held."""
        self.assertRaises(MultiPIDFileError, self.multipidfile.__enter__)

    def test_read_requires_lock(self):
        """_read must be called within the lock."""
        self.assertRaises(AssertionError, self.multipidfile._read)

    def test_read_missing(self):
        """A missing MultiPIDFile reads as empty."""
        with self.multipidfile:
            self.assertEqual(set(), self.multipidfile._read())

    @mock.patch("cdimage.osextras.pid_exists", return_value=True)
    def test_read_existing(self, mock_pid_exists):
        """An existing MultiPIDFile reads as the set of PIDs it contains."""
        with mkfile(self.multipidfile.path) as fd:
            print(1, file=fd)
            print(2, file=fd)
        with self.multipidfile:
            self.assertEqual(set([1, 2]), self.multipidfile._read())

    @mock.patch("cdimage.osextras.pid_exists")
    def test_read_skips_dead_pids(self, mock_pid_exists):
        """Only live PIDs are returned."""
        with mkfile(self.multipidfile.path) as fd:
            print(1, file=fd)
            print(2, file=fd)
        mock_pid_exists.side_effect = lambda pid: pid == 2
        with self.multipidfile:
            self.assertEqual(set([2]), self.multipidfile._read())

    def test_write_requires_lock(self):
        """_write must be called within the lock."""
        self.assertRaises(AssertionError, self.multipidfile._write, 1)

    @mock.patch("cdimage.osextras.pid_exists", return_value=True)
    def test_write_missing(self, mock_pid_exists):
        """Writing to a missing MultiPIDFile works."""
        with self.multipidfile:
            self.multipidfile._write(set([1, 2]))
            self.assertEqual(set([1, 2]), self.multipidfile._read())

    @mock.patch("cdimage.osextras.pid_exists", return_value=True)
    def test_write_existing(self, mock_pid_exists):
        """Writing to an existing MultiPIDFile adjusts its contents."""
        with mkfile(self.multipidfile.path) as fd:
            print(1, file=fd)
        with self.multipidfile:
            self.multipidfile._write(set([1, 2]))
            self.assertEqual(set([1, 2]), self.multipidfile._read())

    def test_state(self):
        """The state can be fetched without explicit locking."""
        self.assertEqual(set(), self.multipidfile.state)

    @mock.patch("cdimage.osextras.pid_exists", return_value=True)
    def test_test_add(self, mock_pid_exists):
        self.assertFalse(self.multipidfile.test_add(1))
        self.assertEqual(set([1]), self.multipidfile.state)
        self.assertTrue(self.multipidfile.test_add(2))
        self.assertEqual(set([1, 2]), self.multipidfile.state)

    @mock.patch("cdimage.osextras.pid_exists", return_value=True)
    def test_test_add_error_on_existing_pid(self, mock_pid_exists):
        with mkfile(self.multipidfile.path) as fd:
            print(1, file=fd)
        self.assertRaises(MultiPIDFileError, self.multipidfile.test_add, 1)

    @mock.patch("cdimage.osextras.pid_exists", return_value=True)
    def test_remove_test(self, mock_pid_exists):
        with mkfile(self.multipidfile.path) as fd:
            print(1, file=fd)
            print(2, file=fd)
        self.assertTrue(self.multipidfile.remove_test(2))
        self.assertEqual(set([1]), self.multipidfile.state)
        self.assertFalse(self.multipidfile.remove_test(1))
        self.assertEqual(set(), self.multipidfile.state)

    def test_remove_test_error_on_empty(self):
        """remove_test raises MultiPIDFileError if already empty."""
        self.assertRaises(MultiPIDFileError, self.multipidfile.remove_test, 1)

    @mock.patch("cdimage.osextras.pid_exists", return_value=True)
    def test_remove_test_error_on_missing_pid(self, mock_pid_exists):
        with mkfile(self.multipidfile.path) as fd:
            print(1, file=fd)
        self.assertRaises(MultiPIDFileError, self.multipidfile.remove_test, 2)

    @mock.patch("cdimage.osextras.pid_exists", return_value=True)
    def test_round_trip(self, mock_pid_exists):
        """After a +/- round-trip, the MultiPIDFile path is missing."""
        self.assertFalse(self.multipidfile.test_add(1))
        self.assertFalse(self.multipidfile.remove_test(1))
        self.assertFalse(os.path.exists(self.multipidfile.path))

    @mock.patch("cdimage.osextras.pid_exists", return_value=True)
    def test_held(self, mock_pid_exists):
        self.assertEqual(set(), self.multipidfile.state)
        with self.multipidfile.held(1) as state_zero:
            self.assertFalse(state_zero)
            self.assertEqual(set([1]), self.multipidfile.state)
            with self.multipidfile.held(2) as state_one:
                self.assertTrue(state_one)
                self.assertEqual(set([1, 2]), self.multipidfile.state)
            self.assertEqual(set([1]), self.multipidfile.state)
        self.assertEqual(set(), self.multipidfile.state)
