#! /usr/bin/python

# Copyright (C) 2026 Canonical Ltd.
# Author: Skia <skia@ubuntu.com>

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

from pathlib import Path
from unittest import mock
import tempfile

from cdimage.config import Config
from cdimage.tree import (
    Publisher,
    Tree,
)
from cdimage.test_observer import TestObserver
from cdimage.tests.helpers import TestCase


class MockResponse:
    def __init__(self, json_data, status_code):
        self.json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if 200 <= self.status_code < 400:
            return
        raise RuntimeError("Oops, HTTP issue")

    def json(self):
        return self.json_data


def mocked_requests_put(*args, **kwargs):
    if args[0].endswith("test-executions/start-test"):
        return MockResponse({"id": "4000"}, 200)

    return MockResponse(None, 404)


def mocked_requests_post(*args, **kwargs):
    if args[0].endswith("test-executions/4000/test-results"):
        return MockResponse(None, 200)

    return MockResponse(None, 404)


def mocked_requests_patch(*args, **kwargs):
    if args[0].endswith("test-executions/4000"):
        return MockResponse(None, 200)

    return MockResponse(None, 404)


class TestTestObserver(TestCase):
    @mock.patch("cdimage.test_observer.requests.put", side_effect=mocked_requests_put)
    @mock.patch("cdimage.test_observer.requests.post", side_effect=mocked_requests_post)
    @mock.patch(
        "cdimage.test_observer.requests.patch", side_effect=mocked_requests_patch
    )
    def test_submit(self, mock_patch, mock_post, mock_put):
        config = Config(read=False)
        config.root = self.use_temp_dir()

        with tempfile.NamedTemporaryFile() as to_conf:
            to_conf_p = Path(to_conf.name)
            to_conf_p.write_text("""
[service]
url: https://tests-api.test.cdimage/v1/
api_key: to_mytopsecretapikey
""")
            config["TO_CONFIG"] = to_conf.name
            to = TestObserver(config)

        date = "20260127"
        directory = Path(config.root) / "www" / "full" / "xubuntu" / "daily" / date
        directory.mkdir(exist_ok=True, parents=True)

        tree = Tree.get_for_directory(config, str(directory), "daily")
        publisher = Publisher.get_daily(tree, "daily")

        entry_path = directory / "resolute-xubuntu-amd64.iso"
        (directory / "SHA256SUMS").write_text(
            "realsha256sum *resolute-xubuntu-amd64.iso"
        )
        entry_path.touch()

        to.publish_image(
            publisher,
            str(entry_path),
            date,
        )
        mock_put.assert_has_calls(
            [
                mock.call(
                    "https://tests-api.test.cdimage/v1/test-executions/start-test",
                    headers={"Authorization": "Bearer to_mytopsecretapikey"},
                    data='{"name": "resolute-xubuntu-amd64.iso", "version": "20260127", "arch": "amd64", "environment": "cdimage.ubuntu.com", "ci_link": "https://cdimage.ubuntu.com/xubuntu/daily/20260127/resolute-xubuntu-amd64.iso", "test_plan": "Image build", "initial_status": "IN_PROGRESS", "relevant_links": [], "needs_assignment": false, "family": "image", "execution_stage": "pending", "os": "xubuntu", "release": "resolute", "sha256": "realsha256sum", "owner": "xubuntu-release", "image_url": "https://cdimage.ubuntu.com/xubuntu/daily/20260127/resolute-xubuntu-amd64.iso"}',
                ),
                mock.call(
                    "https://tests-api.test.cdimage/v1/test-executions/start-test",
                    headers={"Authorization": "Bearer to_mytopsecretapikey"},
                    data='{"name": "resolute-xubuntu-amd64.iso", "version": "20260127", "arch": "amd64", "environment": "user manual tests", "test_plan": "Manual Testing", "initial_status": "IN_PROGRESS", "relevant_links": [{"label": "Manual test suite instructions", "url": "https://github.com/ubuntu/ubuntu-manual-tests/tree/main/resolute/products"}], "needs_assignment": false, "family": "image", "execution_stage": "pending", "os": "xubuntu", "release": "resolute", "sha256": "realsha256sum", "owner": "xubuntu-release", "image_url": "https://cdimage.ubuntu.com/xubuntu/daily/20260127/resolute-xubuntu-amd64.iso"}',
                ),
            ]
        )
        mock_post.assert_has_calls(
            [
                mock.call(
                    "https://tests-api.test.cdimage/v1/test-executions/4000/test-results",
                    headers={"Authorization": "Bearer to_mytopsecretapikey"},
                    data='[{"name": "build-image", "status": "PASSED", "comment": "Build ISO on Launchpad and cdimage", "io_log": "TODO: find a way to send out the build logs here"}]',
                )
            ]
        )
        mock_patch.assert_has_calls(
            [
                mock.call(
                    "https://tests-api.test.cdimage/v1/test-executions/4000",
                    headers={"Authorization": "Bearer to_mytopsecretapikey"},
                    data='{"status": "COMPLETED"}',
                )
            ]
        )

        date = "20260128"
        directory = Path(config.root) / "www" / "full" / "daily-live" / date
        directory.mkdir(exist_ok=True, parents=True)

        tree = Tree.get_for_directory(config, str(directory), "daily")
        publisher = Publisher.get_daily(tree, "daily")

        entry_path = directory / "resolute-ubuntu-amd64.iso"
        (directory / "SHA256SUMS").write_text(
            "anotherrealsha256sum *resolute-ubuntu-amd64.iso"
        )
        entry_path.touch()

        to.publish_image(
            publisher,
            str(entry_path),
            date,
        )
        mock_put.assert_has_calls(
            [
                mock.call(
                    "https://tests-api.test.cdimage/v1/test-executions/start-test",
                    headers={"Authorization": "Bearer to_mytopsecretapikey"},
                    data='{"name": "resolute-ubuntu-amd64.iso", "version": "20260128", "arch": "amd64", "environment": "cdimage.ubuntu.com", "ci_link": "https://cdimage.ubuntu.com/daily-live/20260128/resolute-ubuntu-amd64.iso", "test_plan": "Image build", "initial_status": "IN_PROGRESS", "relevant_links": [], "needs_assignment": false, "family": "image", "execution_stage": "pending", "os": "ubuntu-desktop", "release": "resolute", "sha256": "anotherrealsha256sum", "owner": "canonical-desktop-team", "image_url": "https://cdimage.ubuntu.com/daily-live/20260128/resolute-ubuntu-amd64.iso"}',
                ),
                mock.call(
                    "https://tests-api.test.cdimage/v1/test-executions/start-test",
                    headers={"Authorization": "Bearer to_mytopsecretapikey"},
                    data='{"name": "resolute-ubuntu-amd64.iso", "version": "20260128", "arch": "amd64", "environment": "user manual tests", "test_plan": "Manual Testing", "initial_status": "IN_PROGRESS", "relevant_links": [{"label": "Manual test suite instructions", "url": "https://github.com/ubuntu/ubuntu-manual-tests/tree/main/resolute/products"}], "needs_assignment": false, "family": "image", "execution_stage": "pending", "os": "ubuntu-desktop", "release": "resolute", "sha256": "anotherrealsha256sum", "owner": "canonical-desktop-team", "image_url": "https://cdimage.ubuntu.com/daily-live/20260128/resolute-ubuntu-amd64.iso"}',
                ),
            ]
        )
        mock_post.assert_has_calls(
            [
                mock.call(
                    "https://tests-api.test.cdimage/v1/test-executions/4000/test-results",
                    headers={"Authorization": "Bearer to_mytopsecretapikey"},
                    data='[{"name": "build-image", "status": "PASSED", "comment": "Build ISO on Launchpad and cdimage", "io_log": "TODO: find a way to send out the build logs here"}]',
                )
            ]
        )
        mock_patch.assert_has_calls(
            [
                mock.call(
                    "https://tests-api.test.cdimage/v1/test-executions/4000",
                    headers={"Authorization": "Bearer to_mytopsecretapikey"},
                    data='{"status": "COMPLETED"}',
                )
            ]
        )
