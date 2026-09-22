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

"""Unit tests for cdimage.launchpad."""

import os

try:
    from unittest import mock
except ImportError:
    import mock

from lazr.restfulclient.errors import NotFound, ServerError

from cdimage import launchpad
from cdimage.tests.helpers import TestCase

__metaclass__ = type


class LPResponse(dict):
    """Enough of an httplib2 response for lazr's HTTPError to carry."""

    def __init__(self, status):
        super(LPResponse, self).__init__()
        self.status = status


class FlakyMapping(dict):
    """A Launchpad collection that refuses the first few times it's asked."""

    def __init__(self, failures, error):
        super(FlakyMapping, self).__init__()
        self.failures = failures
        self.error = error
        self.lookups = []

    def __getitem__(self, key):
        self.lookups.append(key)
        if len(self.lookups) <= self.failures:
            raise self.error
        return "resolved:%s" % key


class TestCachingDict(TestCase):
    def setUp(self):
        super(TestCachingDict, self).setUp()
        patcher = mock.patch("cdimage.retry._sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        # The tunable source is a module global; don't inherit another test's.
        self.addCleanup(setattr, launchpad, "_tunables", launchpad._tunables)
        launchpad._tunables = os.environ

    def test_lookup_retries_a_server_error(self):
        # From ubuntu-server/stonking/daily-live-20260916.log: a 503 from
        # api.launchpad.net during lp.people[owner] aborted the entire run.
        # lazr's own retry budget -- 0+1+2+4+8+16 seconds -- had already been
        # spent by the time it gave up, two and a half minutes in.
        mapping = FlakyMapping(2, ServerError(LPResponse(503), b""))
        caching = launchpad._CachingDict(mapping)
        self.assertEqual("resolved:ubuntu-cdimage", caching["ubuntu-cdimage"])
        self.assertEqual(3, len(mapping.lookups))

    def test_lookup_caches_the_answer(self):
        mapping = FlakyMapping(0, ServerError(LPResponse(503), b""))
        caching = launchpad._CachingDict(mapping)
        self.assertEqual("resolved:ubuntu", caching["ubuntu"])
        self.assertEqual("resolved:ubuntu", caching["ubuntu"])
        self.assertEqual(1, len(mapping.lookups))

    def test_lookup_does_not_retry_a_client_error(self):
        # A name that isn't there won't be there in five minutes either, and
        # lazr raises NotFound as a subclass of HTTPError, so classifying on
        # the class rather than the status would sit here retrying for nothing.
        mapping = FlakyMapping(2, NotFound(LPResponse(404), b""))
        caching = launchpad._CachingDict(mapping)
        self.assertRaises(NotFound, lambda: caching["nonexistent"])
        self.assertEqual(1, len(mapping.lookups))

    def test_retry_budget_is_configurable(self):
        launchpad._tunables = {"CDIMAGE_LP_RETRIES": "2"}
        mapping = FlakyMapping(5, ServerError(LPResponse(503), b""))
        caching = launchpad._CachingDict(mapping)
        self.assertRaises(ServerError, lambda: caching["ubuntu-cdimage"])
        self.assertEqual(2, len(mapping.lookups))


class FlakyLiveFS:
    """A livefs whose completed_builds listing fails the first few times."""

    def __init__(self, failures, builds):
        self.failures = failures
        self.builds = builds
        self.listings = 0

    @property
    def completed_builds(self):
        self.listings += 1
        if self.listings <= self.failures:
            raise ServerError(LPResponse(503), b"")
        return self.builds


class FakeDAS:
    architecture_tag = "amd64"


class FakeBuild:
    def __init__(self, das):
        self.distro_arch_series = das
        self.unique_key = None
        self.buildstate = "Successfully built"


class TestCachingLiveFS(TestCase):
    def setUp(self):
        super(TestCachingLiveFS, self).setUp()
        patcher = mock.patch("cdimage.retry._sleep")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, launchpad, "_tunables", launchpad._tunables)
        launchpad._tunables = os.environ

    def test_get_latest_build_retries_the_listing(self):
        # completed_builds is paged, so this walks the network as it goes and a
        # blip part-way through has to restart the search.
        das = FakeDAS()
        lp_livefs = FlakyLiveFS(2, [FakeBuild(das)])
        livefs = launchpad._CachingLiveFS(mock.Mock(), lp_livefs)
        build = livefs.getLatestBuild(das)
        self.assertEqual("Successfully built", build.buildstate)
        self.assertEqual(3, lp_livefs.listings)

    def test_get_latest_build_caches(self):
        das = FakeDAS()
        lp_livefs = FlakyLiveFS(0, [FakeBuild(das)])
        livefs = launchpad._CachingLiveFS(mock.Mock(), lp_livefs)
        first = livefs.getLatestBuild(das)
        self.assertIs(first, livefs.getLatestBuild(das))
        self.assertEqual(1, lp_livefs.listings)

    def test_no_successful_build_is_not_retried(self):
        # "there is no such build" is an answer, not a blip.
        das = FakeDAS()
        lp_livefs = FlakyLiveFS(0, [])
        livefs = launchpad._CachingLiveFS(mock.Mock(), lp_livefs)
        self.assertRaises(Exception, livefs.getLatestBuild, das)
        self.assertEqual(1, lp_livefs.listings)


class TestLogin(TestCase):
    def setUp(self):
        super(TestLogin, self).setUp()
        self.addCleanup(setattr, launchpad, "_tunables", launchpad._tunables)
        launchpad._tunables = {}

    @mock.patch("cdimage.launchpad.Launchpad")
    def test_login_passes_a_timeout(self, mock_launchpad):
        # Without one, httplib2 blocks forever on a connection that never
        # answers, with the build-image-set lock held throughout.
        launchpad.login("production")
        self.assertEqual(
            launchpad.DEFAULT_TIMEOUT,
            mock_launchpad.login_with.call_args[1]["timeout"],
        )

    @mock.patch("cdimage.launchpad.Launchpad")
    def test_login_timeout_is_configurable(self, mock_launchpad):
        launchpad._tunables = {"CDIMAGE_LP_TIMEOUT": "7"}
        launchpad.login("production")
        self.assertEqual(7, mock_launchpad.login_with.call_args[1]["timeout"])

    @mock.patch("cdimage.launchpad.Launchpad")
    def test_login_still_passes_a_timeout_on_the_old_api(self, mock_launchpad):
        # Before focal the fallback= kwarg didn't exist; that path needs the
        # timeout just as much.
        mock_launchpad.login_with.side_effect = [TypeError, mock.sentinel.lp]
        self.assertIs(mock.sentinel.lp, launchpad.login("production"))
        self.assertEqual(2, mock_launchpad.login_with.call_count)
        self.assertEqual(
            launchpad.DEFAULT_TIMEOUT,
            mock_launchpad.login_with.call_args[1]["timeout"],
        )
