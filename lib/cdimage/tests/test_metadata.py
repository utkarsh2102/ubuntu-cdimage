#! /usr/bin/python

# Copyright (C) 2023 Canonical Ltd.
# Author: Łukasz 'sil2100' Zemczak <lukasz.zemczak@ubuntu.com>

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

"""Unit tests for cdimage.simplestreams."""

import json
import os
import shutil
import tarfile
import tempfile

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.metadata import (
    arch_to_lxd_arch,
    lxd_metadata_from_assertion,
    generate_ubuntu_core_image_lxd_metadata)

from cdimage.tests.helpers import TestCase


class TestMetadata(TestCase):
    def setUp(self):
        super(TestMetadata, self).setUp()

    def test_arch_to_lxd_arch(self):
        self.assertEqual(arch_to_lxd_arch("amd64"), "x86_64")
        self.assertEqual(arch_to_lxd_arch("armhf"), "armv7l")
        self.assertEqual(arch_to_lxd_arch("arm64"), "aarch64")
        self.assertEqual(arch_to_lxd_arch("ppc64el"), "ppc64le")
        self.assertEqual(arch_to_lxd_arch("riscv64"), "riscv64")
        self.assertEqual(arch_to_lxd_arch("s390x"), "s390x")
        self.assertEqual(arch_to_lxd_arch("arm64+raspi"), "aarch64")
        self.assertEqual(arch_to_lxd_arch("newarch"), "newarch")
        self.assertEqual(arch_to_lxd_arch("newarch+v1"), "newarch")

    @mock.patch("cdimage.metadata.datetime.datetime")
    def test_lxd_metadata_from_assertion(self, mock_datetime):
        mock_datetime.now.return_value.timestamp.return_value = \
            1631088000
        assertion_path = os.path.join(
            os.path.dirname(__file__), "data",
            "ubuntu-core-22-amd64.model-assertion")
        metadata = lxd_metadata_from_assertion(assertion_path)
        self.assertDictEqual(
            metadata,
            {
                "architecture": "x86_64",
                "creation_date": 1631088000,
                "properties": {
                    "architecture": "amd64",
                    "description": "ubuntu-core-22-amd64",
                    "os": "Ubuntu",
                    "series": "core22",
                },
            })

    @mock.patch("cdimage.metadata.datetime.datetime")
    def test_lxd_metadata_from_assertion_description(self, mock_datetime):
        mock_datetime.now.return_value.timestamp.return_value = \
            1631088000
        assertion_path = os.path.join(
            os.path.dirname(__file__), "data",
            "ubuntu-core-18-amd64+appliance-lxd-core18-amd64.model-assertion")
        metadata = lxd_metadata_from_assertion(assertion_path)
        self.assertDictEqual(
            metadata,
            {
                "architecture": "x86_64",
                "creation_date": 1631088000,
                "properties": {
                    "architecture": "amd64",
                    "description": "LXD core18 Appliance (amd64)",
                    "os": "Ubuntu",
                    "series": "core18",
                },
            })

    def test_generate_ubuntu_core_image_lxd_metadata(self):
        source_path = os.path.join(
            os.path.dirname(__file__), "data",
            "ubuntu-core-22-amd64.model-assertion")
        with tempfile.TemporaryDirectory() as tmpdir:
            # Copy manifest to the temporary directory
            shutil.copy(source_path, tmpdir)
            image_path = os.path.join(tmpdir, "ubuntu-core-22-amd64.img.xz")
            generate_ubuntu_core_image_lxd_metadata(image_path)
            lxd_metadata = os.path.join(
                tmpdir, "ubuntu-core-22-amd64.lxd.tar.xz")
            self.assertTrue(lxd_metadata)
            with tarfile.open(lxd_metadata) as inf:
                # Check if the metadata.yaml is there
                self.assertIn("metadata.yaml", inf.getnames())
                # Check if the metadata.yaml is valid json
                metadata = json.loads(
                    inf.extractfile("metadata.yaml").read())
                self.assertTrue(metadata)
                # No need to validate the whole metadata as other unit tests
                # are already doing that
