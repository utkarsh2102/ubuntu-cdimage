#!/usr/bin/python
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

"""
Test Observer integration functions.
https://github.com/canonical/test_observer/

This require some configuration to activate:

```
[service]
# The URL of the Test Observer API endpoint
url: https://tests-api.ubuntu.com/v1/
# The API key to authenticate with
api_key: to_mytopsecretapikey
```
"""

import configparser
import json
import requests
from pathlib import Path

from cdimage.log import logger

TO_ENVIRONMENT = "cdimage.ubuntu.com"


class TestObserver:
    def __init__(self, cdimage_config):
        config = configparser.ConfigParser()
        config.read(cdimage_config["TO_CONFIG"])
        self.url = config["service"]["url"]
        self.api_key = config["service"]["api_key"]

    def _request(self, _func, path, **kw):
        response = _func(
            f"{self.url}{path}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            **kw,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            logger.info(e)
            logger.info(json.dumps(response.json(), indent=2))
            raise e
        return response

    def _delete(self, path, **kw):
        return self._request(requests.delete, path, **kw)

    def _get(self, path, **kw):
        return self._request(requests.get, path, **kw)

    def _patch(self, path, **kw):
        return self._request(requests.patch, path, **kw)

    def _post(self, path, **kw):
        return self._request(requests.post, path, **kw)

    def _put(self, path, **kw):
        return self._request(requests.put, path, **kw)

    def _get_sha256(self, path: Path) -> str:
        for line in (path.parent / "SHA256SUMS").read_text().splitlines():
            if path.name in line:
                return line.split(" ")[0]
        raise RuntimeError(f"Couldn't find sha256 for {path.name} in {path.parent}")

    def get_owner(self, os: str):
        OS_OWNER_MAPPING = {
            "edubuntu": "edubuntu-release",
            "kubuntu": "kubuntu-release",
            "lubuntu": "lubuntu-iso-managers",
            "ubuntu-desktop": "canonical-desktop-team",
            "ubuntu-base": "canonical-foundations",
            "ubuntu-budgie": "ubuntubudgie-release",
            "ubuntu-mate": "ubuntu-mate-release",
            "ubuntu-mini-iso": "canonical-foundations",
            "ubuntu-server": "canonical-server",
            "ubuntu-unity": "ubuntu-unity-devs",
            "ubuntu-wsl": "canonical-desktop-team",
            "ubuntucinnamon": "ubuntucinnamon-release",
            "ubuntukylin": "ubuntukylin-members",
            "ubuntustudio": "ubuntustudio-release",
            "xubuntu": "xubuntu-release",
        }
        return OS_OWNER_MAPPING.get(os, "ubuntu-cdimage")

    def publish_image(self, publisher, path: str, date: str):
        logger.info("Submitting images to Test Observer")

        full_path = Path(path)
        artifact_name = full_path.name
        cdimage_rel_path = full_path.relative_to(publisher.tree.directory)
        full_url = "https://cdimage.ubuntu.com/" + str(cdimage_rel_path)
        arch = artifact_name.split(".")[0].split("-")[-1]
        os = cdimage_rel_path.parts[0]
        release = full_path.stem.split("-")[0]
        sha256 = self._get_sha256(full_path)

        # Hack around `daily-dangerous` having the exact same name as
        # `daily-live`, thus showing only one row in TO
        if os == "daily-dangerous":
            artifact_name = "dangerous-" + artifact_name

        # Hack around desktop images having special treatement in tree.py
        if os in [
            "noble",
            "resolute",
            "daily-live",
            "daily-preinstalled",
            "daily-dangerous",
        ]:
            os = "ubuntu-desktop"

        # Hack around ubuntu-core images having their naming different
        if os == "ubuntu-core":
            release = full_path.stem.split("-")[2]

        response = self._put(
            "test-executions/start-test",
            data=json.dumps(
                {
                    "name": artifact_name,
                    "version": date,
                    "arch": arch,
                    "environment": TO_ENVIRONMENT,
                    "ci_link": full_url,  # TODO: get a better link here (livefs build)
                    "test_plan": "Image build",
                    "initial_status": "IN_PROGRESS",
                    "relevant_links": [],
                    "needs_assignment": False,
                    "family": "image",
                    "execution_stage": "pending",
                    "os": os,
                    "release": release,
                    "sha256": sha256,
                    "owner": self.get_owner(os),
                    "image_url": full_url,
                }
            ),
        )
        test_execution_id = response.json()["id"]
        self._post(
            f"test-executions/{test_execution_id}/test-results",
            data=json.dumps(
                [
                    {
                        "name": "build-image",
                        "status": "PASSED",
                        "comment": "Build ISO on Launchpad and cdimage",
                        "io_log": "TODO: find a way to send out the build logs here",
                    }
                ]
            ),
        )
        self._patch(
            f"test-executions/{test_execution_id}",
            data=json.dumps(
                {
                    "status": "COMPLETED",
                }
            ),
        )

        # Open a new generic text execution for manual tests reports
        response = self._put(
            "test-executions/start-test",
            data=json.dumps(
                {
                    "name": artifact_name,
                    "version": date,
                    "arch": arch,
                    "environment": "user manual tests",
                    "test_plan": "Manual Testing",
                    "initial_status": "IN_PROGRESS",
                    "relevant_links": [
                        {
                            "label": "Manual test suite instructions",
                            "url": f"https://github.com/ubuntu/ubuntu-manual-tests/tree/main/{release}/products",
                        }
                    ],
                    "needs_assignment": False,
                    "family": "image",
                    "execution_stage": "pending",
                    "os": os,
                    "release": release,
                    "sha256": sha256,
                    "owner": self.get_owner(os),
                    "image_url": full_url,
                }
            ),
        )

    def get_reruns(self, series: str):
        response = self._get(
            "test-executions/reruns",
            params={"family": "image", "environment": TO_ENVIRONMENT},
        )
        for rerun in response.json():
            try:
                if (  # Some basic sanitizing
                    rerun["artefact"]["archived"]
                    or rerun["artefact"]["family"] != "image"
                    or rerun["artefact"]["release"] != series
                ):
                    continue
                yield rerun
            except KeyError:
                continue

    def delete_rerun(self, test_execution_id: int):
        self._delete(
            "test-executions/reruns",
            data=json.dumps({"test_execution_ids": [test_execution_id]}),
        )
