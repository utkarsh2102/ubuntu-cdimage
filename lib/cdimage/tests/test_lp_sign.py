#! /usr/bin/python

# Copyright (C) 2026 Canonical Ltd.
# Author: Utkarsh Gupta <utkarsh@ubuntu.com>

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

"""Unit tests for bin/lp-sign."""

import importlib.machinery
import importlib.util
import os
import sys

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.tests.helpers import TestCase

__metaclass__ = type


def load_lp_sign():
    """Import bin/lp-sign, which has no .py extension to import normally.

    Bytecode writing is disabled while we do it: a stray .pyc in bin/ makes
    test_scripts try to execute it as if it were one of the scripts.
    """
    path = os.path.join(
        os.path.dirname(__file__), os.pardir, os.pardir, os.pardir, "bin", "lp-sign"
    )
    loader = importlib.machinery.SourceFileLoader("lp_sign", os.path.realpath(path))
    spec = importlib.util.spec_from_loader("lp_sign", loader)
    module = importlib.util.module_from_spec(spec)
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = saved
    return module


lp_sign = load_lp_sign()


class TestLPSigningConnection(TestCase):
    def setUp(self):
        super(TestLPSigningConnection, self).setUp()
        self.conn = lp_sign.LPSigningConnection("http://signing.example")

    @mock.patch("requests.get")
    def test_get_passes_a_timeout(self, mock_get):
        # Signing runs during publication, before the bookkeep() safety net,
        # so a request with no timeout can hang a publish indefinitely.
        self.conn._get("/service-key")
        self.assertEqual(lp_sign.REQUEST_TIMEOUT, mock_get.call_args[1]["timeout"])
        self.assertEqual("http://signing.example/service-key", mock_get.call_args[0][0])

    @mock.patch("requests.post")
    def test_post_passes_a_timeout(self, mock_post):
        self.conn._post("/nonce")
        self.assertEqual(lp_sign.REQUEST_TIMEOUT, mock_post.call_args[1]["timeout"])

    @mock.patch("requests.post")
    def test_post_keeps_its_other_arguments(self, mock_post):
        self.conn._post("/sign", headers={"X-Nonce": "abc"}, data=b"body")
        self.assertEqual({"X-Nonce": "abc"}, mock_post.call_args[1]["headers"])
        self.assertEqual(b"body", mock_post.call_args[1]["data"])

    @mock.patch("requests.get")
    def test_get_raises_for_status(self, mock_get):
        mock_get.return_value.raise_for_status.side_effect = Exception("503")
        self.assertRaises(Exception, self.conn._get, "/service-key")

    def test_timeout_is_a_sane_number(self):
        self.assertIsInstance(lp_sign.REQUEST_TIMEOUT, int)
        self.assertGreater(lp_sign.REQUEST_TIMEOUT, 0)
