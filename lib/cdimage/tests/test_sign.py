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

"""Unit tests for cdimage.sign."""

import os
import subprocess

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.config import Config
from cdimage.sign import sign_cdimage
from cdimage.tests.helpers import TestCase, touch


class TestSign(TestCase):
    @mock.patch("subprocess.check_call")
    def test_sign_cdimage_conf_missing(self, mock_check_call):
        config = Config(read=False)
        temp_dir = self.use_temp_dir()
        conf_path = os.path.join(temp_dir, "lp-signing.conf")
        config["LP_SIGN_CONFIG"] = conf_path
        sign_path = os.path.join(temp_dir, "to-sign")
        touch(sign_path)
        self.capture_logging()
        self.assertFalse(sign_cdimage(config, sign_path))
        self.assertLogEqual(["LP_SIGN_CONFIG set but not found."])
        mock_check_call.assert_not_called()

    @mock.patch("subprocess.check_call")
    def test_sign_cdimage(self, mock_check_call):
        config = Config(read=False)
        temp_dir = self.use_temp_dir()
        conf_path = os.path.join(temp_dir, "lp-signing.conf")
        config["LP_SIGN_CONFIG"] = conf_path
        sign_path = os.path.join(temp_dir, "to-sign")
        for path in conf_path, sign_path:
            touch(path)
        self.capture_logging()
        self.assertTrue(sign_cdimage(config, sign_path))
        self.assertLogEqual(["Signing %s using LP signing service" % sign_path])
        expected_command = ["lp-sign", "--config-file", conf_path, sign_path]
        mock_check_call.assert_called_once_with(expected_command, stdout=mock.ANY)
        call = mock_check_call.call_args
        self.assertEqual("%s.gpg" % sign_path, call[1]["stdout"].name)

    @mock.patch("subprocess.check_call")
    def test_sign_cdimage_subprocess_error(self, mock_check_call):
        mock_check_call.side_effect = subprocess.CalledProcessError(1, "")
        temp_dir = self.use_temp_dir()
        conf_path = os.path.join(temp_dir, "lp-signing.conf")
        config = Config(read=False)
        config["LP_SIGN_CONFIG"] = conf_path
        sign_path = os.path.join(temp_dir, "to-sign")
        for path in conf_path, sign_path:
            touch(path)
        touch("%s.gpg" % sign_path)
        self.capture_logging()
        with self.assertRaises(subprocess.CalledProcessError):
            sign_cdimage(config, sign_path)
            mock_check_call.assert_called()
        self.assertLogEqual(
            ["Signing %s using LP signing service" % sign_path]
            )
        self.assertFalse(os.path.exists("%s.gpg" % sign_path))
