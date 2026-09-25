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

"""Unit tests for cdimage.retry."""

import errno
import http.client
import socket
import ssl
import urllib.error

try:
    from unittest import mock
except ImportError:
    import mock

import httplib2
import requests
from lazr.restfulclient.errors import NotFound, ServerError, Unauthorized

from cdimage import retry
from cdimage.config import Config
from cdimage.osextras import FetchError
from cdimage.tests.helpers import TestCase

__metaclass__ = type


# What Launchpad actually serves when it's unwell; the point is that it's long.
LP_503_BODY = (
    b"<!DOCTYPE html>\n<html>\n<head>\n"
    b"  <title>Launchpad is temporarily unavailable</title>\n</head>\n<body>\n"
    b"  <h1>Uh oh!</h1>\n  <h2>Something has gone wrong. We're sorry!</h2>\n"
    b"  <p>Technically, the load balancer took too long to connect to an "
    b"application server.</p>\n</body>\n</html>\n"
)


class LPResponse(dict):
    """Enough of an httplib2 response for lazr's HTTPError to carry."""

    def __init__(self, status, reason="Service Unavailable"):
        super(LPResponse, self).__init__()
        self.status = status
        self.reason = reason


def requests_http_error(status_code):
    response = requests.Response()
    response.status_code = status_code
    return requests.HTTPError(response=response)


class TestIsTransient(TestCase):
    def assertTransient(self, exc):
        self.assertTrue(retry.is_transient(exc), "%r should be transient" % (exc,))

    def assertNotTransient(self, exc):
        self.assertFalse(retry.is_transient(exc), "%r should not be transient" % (exc,))

    def test_launchpad_server_errors(self):
        # The 503 that killed a server build on 2026-09-16 arrived as a lazr
        # ServerError; 500, 504 and 429 are the ones lazr doesn't retry itself.
        for status in 500, 502, 503, 504:
            self.assertTransient(ServerError(LPResponse(status), b""))

    def test_launchpad_client_errors_are_not_transient(self):
        # lazr raises these as subclasses of HTTPError too, so classifying on
        # the class rather than the status would retry them for no reason.
        self.assertNotTransient(NotFound(LPResponse(404), b""))
        self.assertNotTransient(Unauthorized(LPResponse(401), b""))

    def test_socket_errors(self):
        self.assertTransient(socket.timeout())
        self.assertTransient(socket.gaierror(-2, "Name or service not known"))
        self.assertTransient(ConnectionResetError(errno.ECONNRESET, "reset"))
        self.assertTransient(OSError(errno.ETIMEDOUT, "timed out"))
        self.assertNotTransient(OSError(errno.ENOENT, "no such file"))

    def test_http_protocol_errors(self):
        self.assertTransient(http.client.BadStatusLine(""))
        self.assertTransient(http.client.RemoteDisconnected("bye"))
        self.assertTransient(httplib2.ServerNotFoundError("no dns"))

    def test_urllib_errors(self):
        self.assertTransient(urllib.error.HTTPError("u", 503, "x", {}, None))
        self.assertNotTransient(urllib.error.HTTPError("u", 404, "x", {}, None))
        # urlopen wraps the underlying socket failure in .reason.
        self.assertTransient(urllib.error.URLError(socket.timeout()))
        self.assertNotTransient(urllib.error.URLError("unknown url type"))

    def test_requests_errors(self):
        self.assertTransient(requests.ConnectionError())
        self.assertTransient(requests.Timeout())
        self.assertTransient(requests_http_error(503))
        self.assertNotTransient(requests_http_error(404))

    def test_tls_errors(self):
        # A handshake can fail transiently, but a certificate that fails to
        # verify will fail just the same in five minutes.
        self.assertTransient(ssl.SSLError("handshake failure"))
        self.assertNotTransient(ssl.SSLCertVerificationError())

    def test_programming_errors_are_not_transient(self):
        self.assertNotTransient(ValueError("this is a bug"))
        self.assertNotTransient(KeyError("nope"))


class TestRetryCall(TestCase):
    def setUp(self):
        super(TestRetryCall, self).setUp()
        retry.stats.clear()
        self.addCleanup(retry.stats.clear)
        patcher = mock.patch("cdimage.retry._sleep")
        self.mock_sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_returns_result_without_retrying(self):
        self.assertEqual("done", retry.retry_call(lambda: "done", description="thing"))
        self.mock_sleep.assert_not_called()

    def test_retries_until_it_succeeds(self):
        calls = []

        def flaky():
            calls.append(None)
            if len(calls) < 3:
                raise socket.timeout()
            return "done"

        self.assertEqual("done", retry.retry_call(flaky, description="thing"))
        self.assertEqual(3, len(calls))
        self.assertEqual(2, self.mock_sleep.call_count)

    def test_reraises_the_original_exception(self):
        # Callers keep catching what they always caught, so the budget running
        # out must not change the exception's type.
        def always_fails():
            raise socket.timeout("gave up")

        self.assertRaises(
            socket.timeout,
            retry.retry_call,
            always_fails,
            description="thing",
            tries=3,
        )
        self.assertEqual(2, self.mock_sleep.call_count)

    def test_does_not_retry_a_permanent_failure(self):
        def broken():
            raise ValueError("this is a bug")

        self.assertRaises(ValueError, retry.retry_call, broken, description="thing")
        self.mock_sleep.assert_not_called()

    def test_backoff_grows_and_is_capped(self):
        def always_fails():
            raise socket.timeout()

        self.assertRaises(
            socket.timeout,
            retry.retry_call,
            always_fails,
            description="thing",
            tries=6,
            delay=10,
            backoff=2,
            max_delay=40,
        )
        # Jittered to half-to-one-and-a-half of the nominal delay, so assert
        # the band rather than the exact value.
        slept = [call[0][0] for call in self.mock_sleep.call_args_list]
        self.assertEqual(5, len(slept))
        for nominal, actual in zip([10, 20, 40, 40, 40], slept):
            self.assertGreaterEqual(actual, nominal * 0.5)
            self.assertLessEqual(actual, nominal * 1.5)

    def test_transient_may_be_a_tuple_of_classes(self):
        class Mine(Exception):
            pass

        calls = []

        def flaky():
            calls.append(None)
            if len(calls) < 2:
                raise Mine()
            return "done"

        self.assertEqual(
            "done",
            retry.retry_call(flaky, description="thing", transient=(Mine,)),
        )
        self.assertEqual(2, len(calls))

    def test_passes_arguments_through(self):
        self.assertEqual(
            (1, 2, {"c": 3}),
            retry.retry_call(
                lambda a, b, **kw: (a, b, kw), 1, 2, c=3, description="thing"
            ),
        )

    def test_counts_retries_for_the_summary(self):
        calls = []

        def flaky():
            calls.append(None)
            if len(calls) < 3:
                raise socket.timeout()

        retry.retry_call(flaky, description="Launchpad lookup")
        self.assertEqual({"Launchpad lookup": 2}, dict(retry.stats))
        self.capture_logging()
        retry.log_stats()
        self.assertLogEqual(["Retried transient remote failures: Launchpad lookup x2"])

    def test_log_stats_says_nothing_when_nothing_was_retried(self):
        self.capture_logging()
        retry.log_stats()
        self.assertLogEqual([])


class TestDescribe(TestCase):
    def test_launchpad_error_is_reduced_to_one_line(self):
        # lazr's HTTPError stringifies to the whole response, body included,
        # and Launchpad's 503 body is two kilobytes of HTML.  Logging that on
        # every retry of every call would bury the rest of the build log.
        exc = ServerError(LPResponse(503), LP_503_BODY)
        self.assertGreater(len(str(exc)), 300)
        self.assertEqual("ServerError: HTTP 503", retry.describe(exc))

    def test_requests_error_is_reduced_to_one_line(self):
        self.assertEqual(
            "HTTPError: HTTP 502", retry.describe(requests_http_error(502))
        )

    def test_plain_exception_keeps_its_first_line(self):
        self.assertEqual(
            "FetchError: wget returned 8",
            retry.describe(FetchError("wget returned 8\nand some detail")),
        )

    def test_exception_with_no_message(self):
        self.assertEqual("TimeoutError", retry.describe(socket.timeout()))

    def test_unstringifiable_exception_does_not_explode(self):
        # Whatever we're describing, the retry itself must not be what fails.
        class Awkward(Exception):
            def __str__(self):
                raise RuntimeError("no")

        self.assertEqual("Awkward", retry.describe(Awkward()))


class TestIntSetting(TestCase):
    def test_reads_from_a_config(self):
        config = Config(read=False)
        config["CDIMAGE_LP_RETRIES"] = "9"
        self.assertEqual(9, retry.int_setting(config, "CDIMAGE_LP_RETRIES", 5))

    def test_missing_config_key_falls_back(self):
        # Config is a defaultdict(str), so a missing key arrives as "".
        config = Config(read=False)
        self.assertEqual(5, retry.int_setting(config, "CDIMAGE_LP_RETRIES", 5))

    def test_missing_environment_key_falls_back(self):
        self.assertEqual(5, retry.int_setting({}, "CDIMAGE_LP_RETRIES", 5))

    def test_nonsense_falls_back(self):
        self.assertEqual(5, retry.int_setting({"K": "soon"}, "K", 5))


class TestStatusHandling(TestCase):
    def test_a_status_we_cannot_read_is_not_transient(self):
        # A response object that doesn't carry a usable status shouldn't be
        # mistaken for a 503.
        self.assertFalse(retry._status_is_transient(None))
        self.assertFalse(retry._status_is_transient("upstream exploded"))
        self.assertTrue(retry._status_is_transient("503"))

    def test_description_defaults_to_the_callable_name(self):
        calls = []

        def fetch_the_thing():
            calls.append(None)
            if len(calls) < 2:
                raise socket.timeout()

        with mock.patch("cdimage.retry._sleep"):
            retry.stats.clear()
            self.addCleanup(retry.stats.clear)
            retry.retry_call(fetch_the_thing)
        self.assertEqual({"fetch_the_thing": 1}, dict(retry.stats))
