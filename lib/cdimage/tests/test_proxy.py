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

"""Unit tests for cdimage.proxy."""

from __future__ import print_function

import os
import subprocess

from cdimage.config import Config
from cdimage.proxy import _select_proxy, proxy_call, proxy_check_call
from cdimage.tests.helpers import TestCase, mkfile


class TestProxy(TestCase):
    def setUp(self):
        super(TestProxy, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.config_path = os.path.join(self.temp_dir, "production", "proxies")

    def test_select_proxy(self):
        self.assertIsNone(_select_proxy(self.config, "any-caller"))
        with mkfile(self.config_path) as f:
            print("test1\thttp://foo.example.org:3128/", file=f)
            print("test2\thttp://bar.example.org:3128/", file=f)
        self.assertEqual(
            "http://foo.example.org:3128/",
            _select_proxy(self.config, "test1"))
        self.assertEqual(
            "http://bar.example.org:3128/",
            _select_proxy(self.config, "test2"))
        self.assertIsNone(_select_proxy(self.config, "other-caller"))

    def test_call_set_proxy(self):
        http_proxy = "http://foo.example.org:3128/"
        with mkfile(self.config_path) as f:
            print("caller\t%s" % http_proxy, file=f)
        path = os.path.join(self.temp_dir, "proxy")
        with mkfile(path) as fp:
            self.assertEqual(
                0,
                proxy_call(
                    self.config, "caller",
                    "echo \"$http_proxy\"", stdout=fp, shell=True))
        with open(path) as fp:
            self.assertEqual(http_proxy, fp.read().rstrip("\n"))

    def test_call_unset_proxy(self):
        os.environ["http_proxy"] = "http://set.example.org:3128/"
        with mkfile(self.config_path) as f:
            print("caller\tunset", file=f)
        path = os.path.join(self.temp_dir, "proxy")
        with mkfile(path) as fp:
            self.assertEqual(
                0,
                proxy_call(
                    self.config, "caller",
                    "echo \"$http_proxy\"", stdout=fp, shell=True))
        with open(path) as fp:
            self.assertEqual("", fp.read().rstrip("\n"))

    def test_call_unchanged(self):
        http_proxy = "http://set.example.org:3128/"
        os.environ["http_proxy"] = http_proxy
        with mkfile(self.config_path) as f:
            print("caller\tunset", file=f)
        path = os.path.join(self.temp_dir, "proxy")
        with mkfile(path) as fp:
            self.assertEqual(
                0,
                proxy_call(
                    self.config, "other-caller",
                    "echo \"$http_proxy\"", stdout=fp, shell=True))
        with open(path) as fp:
            self.assertEqual(http_proxy, fp.read().rstrip("\n"))

    def test_check_call_checks(self):
        self.assertRaises(
            subprocess.CalledProcessError,
            proxy_check_call, self.config, "any-caller", ["false"])
