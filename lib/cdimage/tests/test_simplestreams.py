#! /usr/bin/python

# Copyright (C) 2021 Canonical Ltd.
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

import os
import json
import shutil
import tempfile

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.simplestreams import (
    SimpleStreams, DailySimpleStreams, FullReleaseSimpleStreams,
    SimpleReleaseSimpleStreams)
from cdimage.config import Config, Series
from cdimage.tree import (
    Tree, Publisher, DailyTreePublisher, FullReleasePublisher,
    FullReleaseTree, SimpleReleasePublisher, SimpleReleaseTree)
from cdimage.tests.helpers import TestCase

__metaclass__ = type


class TestSimpleStreams(TestCase):
    def setUp(self):
        super(TestSimpleStreams, self).setUp()
        os.environ["CDIMAGE_ROOT"] = "/tmp/cdimage/test"
        self.config = Config(read=False)

    def test_prepare_product_info(self):
        """Check if we are extracting the right product information."""
        # We'll be testing xenial as we know for sure there will be no more
        # point-releases for it.
        series = Series.find_by_name("xenial")
        streams = SimpleStreams(self.config)
        # Execute the addition twice, to make sure we don't get
        # any weird duplocated entries.
        streams.prepare_product_info(
            "test:test-product",
            "ubuntu-server",
            series,
            "daily-preinstalled",
            "arm64+raspi")
        self.assertIn("test:test-product",
                      streams.cdimage_products)
        # ...and the second execution.
        streams.prepare_product_info(
            "test:test-product",
            "ubuntu-server",
            series,
            "daily-preinstalled",
            "arm64+raspi")
        self.assertDictEqual(
            streams.cdimage_products,
            {
                "test:test-product": {
                    "arch": "arm64+raspi",
                    "os": "ubuntu-server",
                    "release": "xenial",
                    "release_codename": "Xenial Xerus",
                    "release_title": "16.04.7 LTS",
                    "image_type": "daily-preinstalled",
                    "version": "16.04"
                }
            })

    def test_extract_arch(self):
        """Check if extraction of the arch string works as expected."""
        streams = SimpleStreams(self.config)
        # Test various filenames
        test_cases = {
            "ubuntu-20.04.3-live-server-amd64.iso": "amd64",
            "focal-preinstalled-server-arm64+raspi.img.xz": "arm64+raspi",
            "focal-desktop-amd64.iso": "amd64",
            "irrelevant-file": None,
        }
        for filename, arch in test_cases.items():
            self.assertEqual(streams.extract_arch(filename), arch)

    def test_extract_release_image_type(self):
        """Check if extraction of the image_type works as expected."""
        streams = SimpleStreams(self.config)
        # Test various filenames
        test_cases = {
            "ubuntu-20.04.3-live-server-amd64.iso": (
                "ubuntu", "amd64", "live-server"),
            "ubuntu-20.04.3-preinstalled-server-arm64+raspi.img.xz": (
                "ubuntu-server", "arm64+raspi", "preinstalled-server"),
            "kubuntu-20.04.3-desktop-amd64.iso": (
                "kubuntu", "amd64", "desktop"),
            "irrelevant-file": (
                "kubuntu", "amd64", None)
        }
        for filename, test_data in test_cases.items():
            project, arch, image_type = test_data
            self.assertEqual(streams.extract_release_image_type(
                                filename, project, arch),
                             image_type)

    def test_extract_release_project(self):
        """Check if extraction of the project string works as expected."""
        streams = SimpleStreams(self.config)
        # Test various filenames
        test_cases = {
            "ubuntu-20.04.3-live-server-amd64.iso": "ubuntu-server",
            "kubuntu-20.04.3-desktop-amd64.iso": "kubuntu",
            "ubuntu-mate-20.04.3-desktop-amd64.iso": "ubuntu-mate",
            "ubuntu-20.04.3-desktop-amd64.iso": "ubuntu",
            "ubuntu-20.04.3-preinstalled-server-arm64+raspi.img.xz":
                "ubuntu-server",
            "irrelevant-file": None,
        }
        for filename, project in test_cases.items():
            self.assertEqual(streams.extract_release_project(filename),
                             project)

    def test_extract_release_identifier(self):
        """Check if extraction of the release version works as expected."""
        streams = SimpleStreams(self.config)
        series = Series.find_by_name("hirsute")  # for checking defaults
        # Test various filenames
        test_cases = {
            "ubuntu-20.04.3-live-server-amd64.iso": "20.04.3",
            "kubuntu-21.10-desktop-amd64.iso": "21.10",
            "ubuntu-20.04.2.0-preinstalled-server-arm64+raspi.img.xz":
                "20.04.2.0",
            "ubuntu-mate-18.04.6-live-server-amd64.iso": "18.04.6",
            "ubuntu-core-20-amd64.img": "20",
            "ubuntu-custom-image-amd64.iso": "21.04",
        }
        for filename, version in test_cases.items():
            self.assertEqual(streams.extract_release_identifier(
                                 filename, series),
                             version)

    @mock.patch("os.stat")
    def test_scan_published_item(self, osstat):
        """Check if published items are detected and parsed correctly."""
        # We'll return a constant size from os.stat
        osstat.return_value.st_size = 1234
        # For checksums, test both cases of pre-calculated sums and cases
        # where the entry is not in SHA256SUMS.
        sha256sums = mock.Mock()
        sha256sums.entries = {
            "focal-test-server-amd64.iso": "1234123412"
            }
        sha256sums.checksum.return_value = "51deeffec7"
        streams = SimpleStreams(self.config)
        # All possible test cases for scanning published files
        test_cases = {
            "SHA256SUMS": None,
            "focal-test-server-amd64.iso": {
                "sha256": "1234123412",
                "size": 1234,
                "path": "ubuntu-server/release/focal-test-server-amd64.iso",
                "ftype": "iso"
            },
            "focal-test-server-amd64.img": {
                "sha256": "51deeffec7",
                "size": 1234,
                "path": "ubuntu-server/release/focal-test-server-amd64.img",
                "ftype": "img"
            },
            "focal-test-server-amd64.img.xz": {
                "sha256": "51deeffec7",
                "size": 1234,
                "path": "ubuntu-server/release/focal-test-server-amd64.img.xz",
                "ftype": "img.xz"
            },
            "focal-test-server-amd64.manifest": {
                "sha256": "51deeffec7",
                "size": 1234,
                "path": "ubuntu-server/release/focal-test-server-amd64"
                        ".manifest",
                "ftype": "manifest"
            },
            "focal-test-server-amd64.list": {
                "sha256": "51deeffec7",
                "size": 1234,
                "path": "ubuntu-server/release/focal-test-server-amd64.list",
                "ftype": "list"
            },
            "focal-test-server-amd64.tar.gz": None
        }

        for file, expected_data in test_cases.items():
            data = streams.scan_published_item(
                "/tmp/cdimage/test/ubuntu-server/release",
                sha256sums,
                file)
            if not expected_data:
                self.assertEqual(data, expected_data)
            else:
                # ...so we can get more detailed information about which
                # elements differ.
                self.assertDictEqual(data, expected_data)

    def test_get_simplestreams(self):
        """Check if get_simplestreams() returns the right class object."""
        # All possible simple streams cases
        daily_publisher = DailyTreePublisher(
            Tree(self.config, None), None)
        full_publisher = FullReleasePublisher(
            FullReleaseTree(self.config, None), None, "named")
        simple_publisher = SimpleReleasePublisher(
            SimpleReleaseTree(self.config, None), None, "yes")
        test_cases = [
            (daily_publisher, DailySimpleStreams),
            (full_publisher, FullReleaseSimpleStreams),
            (simple_publisher, SimpleReleaseSimpleStreams),
            (None, None),
        ]

        for publisher, cls_streams in test_cases:
            if cls_streams:
                streams = SimpleStreams.get_simplestreams(
                    self.config, publisher)
                self.assertIsInstance(streams, cls_streams)
            else:
                with self.assertRaises(Exception) as e:
                    SimpleStreams.get_simplestreams(
                        self.config, publisher)

    def test_get_simplestreams_by_name(self):
        """Check if get_simplestreams_by_name() also works."""
        # All possible simple streams cases
        test_cases = {
            'daily': DailySimpleStreams,
            'release': FullReleaseSimpleStreams,
            'official': SimpleReleaseSimpleStreams,
            'wrong': None,
        }

        for name, cls_streams in test_cases.items():
            if cls_streams:
                streams = SimpleStreams.get_simplestreams_by_name(
                    self.config, name)
                self.assertIsInstance(streams, cls_streams)
            else:
                with self.assertRaises(Exception) as e:
                    SimpleStreams.get_simplestreams_by_name(
                        self.config, name)


def mock_sign_cdimage(tree, path):
    """A mock of sign_cdimage()"""
    with open("%s.gpg" % path, "w") as fp:
        fp.write("TEST SIGNATURE")
    return True


def _sort_index_product_list(streams):
    """Helper function sorting index file's product lists."""
    if "index" in streams and isinstance(streams["index"], list):
        for i in streams["index"]:
            if "products" in expected["index"][i]:
                streams["index"][i]["products"].sort()


# We need to make sure we have predictable series and point-releases for
# testing purposes.
test_all_series = [
    Series(
        "bionic", "18.04", "Bionic Beaver",
        pointversion="18.04.6",
        all_lts_projects=True,
        _core_series="18"),
    Series("cosmic", "18.10", "Cosmic Cuttlefish"),
    Series("disco", "19.04", "Disco Dingo"),
    Series(
        "eoan", "19.10", "Eoan Ermine",
        pointversion="19.10.1"),
    Series(
        "focal", "20.04", "Focal Fossa",
        pointversion="20.04.3",
        all_lts_projects=True,
        _core_series="20"),
    Series("groovy", "20.10", "Groovy Gorilla"),
    Series("hirsute", "21.04", "Hirsute Hippo"),
    Series("impish", "21.10", "Impish Indri"),
]


class TestSimpleStreamsTree(TestCase):
    def setUp(self):
        super(TestSimpleStreamsTree, self).setUp()
        self.temp_root = tempfile.TemporaryDirectory(prefix="cdimage-")
        os.environ["CDIMAGE_ROOT"] = self.temp_root.name
        self.config = Config(read=False)

    @mock.patch("cdimage.config.all_series", test_all_series)
    @mock.patch("cdimage.simplestreams.sign_cdimage")
    @mock.patch("cdimage.simplestreams.timestamp")
    def test_daily_tree(self, timestamp, sign_cdimage):
        """Check if we get a right simplestream for a daily tree."""
        timestamp.return_value = "TIMESTAMP"
        sign_cdimage.side_effect = mock_sign_cdimage
        # Setup the tree
        tree_source = os.path.join(
            os.path.dirname(__file__), "data", "www")
        shutil.copytree(tree_source, os.path.join(self.temp_root.name, "www"),
                        symlinks=True)
        # Now the object under test
        streams = DailySimpleStreams(self.config)
        streams.generate()
        # Now compare it with the expected tree
        streams_dir = os.path.join(
            self.temp_root.name, "www", "full", "streams", "v1")
        expected_dir = os.path.join(
            os.path.dirname(__file__), "data", "result", "daily")
        streams_contents = sorted(os.listdir(streams_dir))
        expected_contents = sorted(os.listdir(expected_dir))
        self.assertListEqual(streams_contents, expected_contents)
        for file in streams_contents:
            if file.endswith(".gpg"):
                continue
            streams_path = os.path.join(streams_dir, file)
            expected_path = os.path.join(expected_dir, file)
            with open(streams_path) as sf, open(expected_path) as ef:
                streams = json.load(sf)
                expected = json.load(ef)
            # Work-around the fact that product lists can have different
            # order depending on the locale used.
            if file == "index.json":
                _sort_index_product_list(streams)
                _sort_index_product_list(expected)
            self.assertDictEqual(
                streams, expected,
                "SimpleStreams file %s has unexpected contents." % file)

    @mock.patch("cdimage.config.all_series", test_all_series)
    @mock.patch("cdimage.simplestreams.sign_cdimage")
    @mock.patch("cdimage.simplestreams.timestamp")
    def test_release_tree(self, timestamp, sign_cdimage):
        """Check if we get a right simplestream for a release tree."""
        timestamp.return_value = "TIMESTAMP"
        sign_cdimage.side_effect = mock_sign_cdimage
        # Setup the tree
        tree_source = os.path.join(
            os.path.dirname(__file__), "data", "www")
        shutil.copytree(tree_source, os.path.join(self.temp_root.name, "www"),
                        symlinks=True)
        # Now the object under test
        streams = FullReleaseSimpleStreams(self.config)
        streams.generate()
        # Now compare it with the expected tree
        streams_dir = os.path.join(
            self.temp_root.name, "www", "full", "releases", "streams", "v1")
        expected_dir = os.path.join(
            os.path.dirname(__file__), "data", "result", "release")
        streams_contents = sorted(os.listdir(streams_dir))
        expected_contents = sorted(os.listdir(expected_dir))
        self.assertListEqual(streams_contents, expected_contents)
        for file in streams_contents:
            if file.endswith(".gpg"):
                continue
            streams_path = os.path.join(streams_dir, file)
            expected_path = os.path.join(expected_dir, file)
            with open(streams_path) as sf, open(expected_path) as ef:
                streams = json.load(sf)
                expected = json.load(ef)
            # Work-around the fact that product lists can have different
            # order depending on the locale used.
            if file == "index.json":
                _sort_index_product_list(streams)
                _sort_index_product_list(expected)
            self.assertDictEqual(
                streams, expected,
                "SimpleStreams file %s has unexpected contents." % file)

    @mock.patch("cdimage.config.all_series", test_all_series)
    @mock.patch("cdimage.simplestreams.sign_cdimage")
    @mock.patch("cdimage.simplestreams.timestamp")
    def test_simple_tree(self, timestamp, sign_cdimage):
        """Check if we get a right simplestream for a simple tree."""
        timestamp.return_value = "TIMESTAMP"
        sign_cdimage.side_effect = mock_sign_cdimage
        # Setup the tree
        tree_source = os.path.join(
            os.path.dirname(__file__), "data", "www")
        shutil.copytree(tree_source, os.path.join(self.temp_root.name, "www"),
                        symlinks=True)
        # Now the object under test
        streams = SimpleReleaseSimpleStreams(self.config)
        streams.generate()
        # Now compare it with the expected tree
        streams_dir = os.path.join(
            self.temp_root.name, "www", "simple", "streams", "v1")
        expected_dir = os.path.join(
            os.path.dirname(__file__), "data", "result", "simple")
        streams_contents = sorted(os.listdir(streams_dir))
        expected_contents = sorted(os.listdir(expected_dir))
        self.assertListEqual(streams_contents, expected_contents)
        for file in streams_contents:
            if file.endswith(".gpg"):
                continue
            streams_path = os.path.join(streams_dir, file)
            expected_path = os.path.join(expected_dir, file)
            with open(streams_path) as sf, open(expected_path) as ef:
                streams = json.load(sf)
                expected = json.load(ef)
            # Work-around the fact that product lists can have different
            # order depending on the locale used.
            if file == "index.json":
                _sort_index_product_list(streams)
                _sort_index_product_list(expected)
            self.assertDictEqual(
                streams, expected,
                "SimpleStreams file %s has unexpected contents." % file)
