#! /usr/bin/python

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

"""Unit tests for cdimage.semaphore."""

from __future__ import print_function

__metaclass__ = type

import os

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.semaphore import Semaphore, SemaphoreError
from cdimage.tests.helpers import TestCase


class TestSemaphore(TestCase):
    def setUp(self):
        super(TestSemaphore, self).setUp()
        self.use_temp_dir()
        self.semaphore = Semaphore(os.path.join(self.temp_dir, "sem"))

    def test_str(self):
        """A Semaphore stringifies to 'semaphore PATH'."""
        self.assertEqual(
            "semaphore %s" % self.semaphore.path, str(self.semaphore))

    def test_context_manager(self):
        """A Semaphore operates as a context manager, with locking."""
        self.assertFalse(os.path.exists(self.semaphore.lock_path))
        with self.semaphore:
            self.assertTrue(os.path.exists(self.semaphore.lock_path))
        self.assertFalse(os.path.exists(self.semaphore.lock_path))

    @mock.patch("subprocess.call", return_value=1)
    def test_lock_failure(self, mock_call):
        """__enter__ raises SemaphoreError if the lock is already held."""
        self.assertRaises(SemaphoreError, self.semaphore.__enter__)

    def test_read_requires_lock(self):
        """_read must be called within the lock."""
        self.assertRaises(AssertionError, self.semaphore._read)

    def test_read_missing(self):
        """A missing semaphore file reads as zero."""
        with self.semaphore:
            self.assertEqual(0, self.semaphore._read())

    def test_read_existing(self):
        """An existing semaphore file reads as its integer value."""
        with open(self.semaphore.path, "w") as fd:
            print(10, file=fd)
        with self.semaphore:
            self.assertEqual(10, self.semaphore._read())

    def test_add_requires_lock(self):
        """_add must be called within the lock."""
        self.assertRaises(AssertionError, self.semaphore._add, 1)

    def test_add_missing(self):
        """Adding to a missing semaphore file treats it as initially zero."""
        with self.semaphore:
            self.assertEqual(1, self.semaphore._add(1))
            self.assertEqual(1, self.semaphore._read())

    def test_add_existing(self):
        """Adding to an existing semaphore file adjusts its contents."""
        with open(self.semaphore.path, "w") as fd:
            print(10, file=fd)
        with self.semaphore:
            self.assertEqual(9, self.semaphore._add(-1))
            self.assertEqual(9, self.semaphore._read())

    def test_test_increment(self):
        self.assertEqual(0, self.semaphore.test_increment())
        with self.semaphore:
            self.assertEqual(1, self.semaphore._read())
        self.assertEqual(1, self.semaphore.test_increment())
        with self.semaphore:
            self.assertEqual(2, self.semaphore._read())

    def test_decrement_test(self):
        with open(self.semaphore.path, "w") as fd:
            print(2, file=fd)
        self.assertEqual(1, self.semaphore.decrement_test())
        with self.semaphore:
            self.assertEqual(1, self.semaphore._read())
        self.assertEqual(0, self.semaphore.decrement_test())
        with self.semaphore:
            self.assertEqual(0, self.semaphore._read())

    def test_decrement_test_error_on_zero(self):
        """decrement_test raises SemaphoreError if already zero."""
        self.assertRaises(SemaphoreError, self.semaphore.decrement_test)

    def test_round_trip(self):
        """After a +/- round-trip, the semaphore path is missing."""
        self.assertEqual(0, self.semaphore.test_increment())
        self.assertEqual(0, self.semaphore.decrement_test())
        self.assertFalse(os.path.exists(self.semaphore.path))
