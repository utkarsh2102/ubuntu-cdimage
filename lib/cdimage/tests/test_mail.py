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

"""Unit tests for cdimage.mail."""

from __future__ import print_function

__metaclass__ = type

import os
import subprocess

import mock

from cdimage.config import Config
from cdimage.mail import (
    _notify_addresses_path,
    get_notify_addresses,
    send_mail,
)
from cdimage.tests.helpers import TestCase


class TestNotify(TestCase):
    def setUp(self):
        super(TestNotify, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()

    def test_notify_addresses_path(self):
        self.assertIsNone(_notify_addresses_path(self.config))

        path = os.path.join(self.temp_dir, "etc", "notify-addresses")
        os.makedirs(os.path.dirname(path))
        with open(path, "w"):
            pass
        self.assertEqual(path, _notify_addresses_path(self.config))

        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        os.makedirs(os.path.dirname(path))
        with open(path, "w"):
            pass
        self.assertEqual(path, _notify_addresses_path(self.config))

    def test_get_notify_addresses_no_config(self):
        self.assertEqual([], get_notify_addresses(self.config))

    def test_get_notify_addresses_all_matches_any_project(self):
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            print("ALL\tfoo@example.org bar@example.org", file=f)
        self.assertEqual(
            ["foo@example.org", "bar@example.org"],
            get_notify_addresses(self.config))
        self.assertEqual(
            ["foo@example.org", "bar@example.org"],
            get_notify_addresses(self.config, "ubuntu"))
        self.assertEqual(
            ["foo@example.org", "bar@example.org"],
            get_notify_addresses(self.config, "kubuntu"))

    def test_get_notify_addresses_projects_match_exactly(self):
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            print("ubuntu\tubuntu@example.org", file=f)
            print("kubuntu\tkubuntu@example.org", file=f)
        self.assertEqual(
            ["ubuntu@example.org"],
            get_notify_addresses(self.config, "ubuntu"))
        self.assertEqual(
            ["kubuntu@example.org"],
            get_notify_addresses(self.config, "kubuntu"))
        self.assertEqual([], get_notify_addresses(self.config, "edubuntu"))

    def test_send_mail_dry_run_from_file(self):
        path = os.path.join(self.temp_dir, "body")
        with open(path, "w") as body:
            print("Body", file=body)
            print("Text", file=body)
        self.capture_logging()
        with open(path) as body:
            send_mail(
                "Test subject", "test_notify", ["foo@example.org"], body,
                dry_run=True)
        self.assertLogEqual([
            "Would send mail to: foo@example.org",
            "",
            "Subject: Test subject",
            "X-Generated-By: test_notify",
            "",
            "Body",
            "Text",
            "",
        ])

    def test_send_mail_dry_run_from_string(self):
        self.capture_logging()
        send_mail(
            "Test subject", "test_notify",
            ["foo@example.org", "bar@example.org"], "Body\nText\n",
            dry_run=True)
        self.assertLogEqual([
            "Would send mail to: foo@example.org, bar@example.org",
            "",
            "Subject: Test subject",
            "X-Generated-By: test_notify",
            "",
            "Body",
            "Text",
            "",
        ])

    @mock.patch("subprocess.Popen")
    def test_send_mail_from_file(self, mock_popen):
        path = os.path.join(self.temp_dir, "body")
        with open(path, "w") as body:
            print("Body", file=body)
            print("Text", file=body)
        with open(path) as body:
            send_mail(
                "Test subject", "test_notify", ["foo@example.org"], body)
            expected_command = [
                "mail", "-s", "Test subject",
                "-a", "X-Generated-By: test_notify",
                "foo@example.org",
            ]
            mock_popen.assert_called_once_with(expected_command, stdin=body)

    @mock.patch("subprocess.Popen")
    def test_send_mail_from_string(self, mock_popen):
        send_mail(
            "Test subject", "test_notify",
            ["foo@example.org", "bar@example.org"], "Body\nText\n")
        expected_command = [
            "mail", "-s", "Test subject", "-a", "X-Generated-By: test_notify",
            "foo@example.org", "bar@example.org",
        ]
        mock_popen.assert_called_once_with(
            expected_command, stdin=subprocess.PIPE)
        mock_popen.return_value.stdin.write.assert_has_calls(
            [mock.call("Body\nText\n"), mock.call("")])
