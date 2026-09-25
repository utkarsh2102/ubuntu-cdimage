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

"""Retrying remote calls that failed for transient reasons.

Launchpad goes away for minutes at a time.  Almost nothing cdimage asks it for
is urgent enough that giving up on the first refusal is right, and some of it --
fetching the artifacts of a livefs build that has *already succeeded* -- is
expensive enough that giving up costs a whole day's image for a project.

lazr.restfulclient does retry HTTP 502 and 503 by itself, but its budget is
0+1+2+4+8+16 = 31 seconds, which is shorter than a real Launchpad outage, and
it covers neither 500, 504 and 429 nor any socket-level failure.  What this
module adds is an outer budget measured in minutes.
"""

import collections
import errno
import http.client
import random
import socket
import ssl
import time
import urllib.error

from cdimage.log import logger

__metaclass__ = type


# Indirection so that tests can run without actually sleeping.
_sleep = time.sleep

DEFAULT_TRIES = 5
DEFAULT_DELAY = 10.0
DEFAULT_BACKOFF = 2.0
DEFAULT_MAX_DELAY = 120.0

# HTTP statuses that mean "ask again later" rather than "you asked wrongly".
TRANSIENT_STATUSES = frozenset([408, 429, 500, 502, 503, 504])

_TRANSIENT_ERRNOS = frozenset(
    [
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETDOWN,
        errno.ENETRESET,
        errno.ENETUNREACH,
        errno.EPIPE,
        errno.ETIMEDOUT,
    ]
)

# How often we had to retry each kind of call, for the end-of-build summary.
# Launchpad flakiness is far easier to argue about with a number attached.
stats = collections.Counter()


# These are all optional: retry.py has to stay importable from bin/ scripts and
# from a test environment that may not have the full Launchpad stack installed.
try:
    from lazr.restfulclient.errors import HTTPError as _LazrHTTPError
except ImportError:
    _LazrHTTPError = None

try:
    import httplib2 as _httplib2
except ImportError:
    _httplib2 = None

try:
    import requests as _requests
except ImportError:
    _requests = None


def int_setting(mapping, key, default):
    """Read an integer tunable from a Config or from os.environ.

    Config is a defaultdict(str), so a missing key arrives here as "" rather
    than raising; os.environ raises KeyError.  Either way we want the default.
    """
    try:
        return int(mapping[key])
    except (KeyError, TypeError, ValueError):
        return default


def _status_is_transient(status):
    try:
        return int(status) in TRANSIENT_STATUSES
    except (TypeError, ValueError):
        return False


def _status_of(exc):
    """The HTTP status exc carries, if it carries one."""
    # urllib.error.HTTPError is a subclass of URLError, so it has to be
    # checked before any URLError handling.
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code
    # lazr raises NotFound and Unauthorized as subclasses of HTTPError too, so
    # the status is the only thing worth matching on.
    if _LazrHTTPError is not None and isinstance(exc, _LazrHTTPError):
        return getattr(exc.response, "status", None)
    if _requests is not None and isinstance(exc, _requests.HTTPError):
        return getattr(exc.response, "status_code", None)
    return None


def describe(exc):
    """A one-line description of exc, for the retry log.

    lazr's HTTPError stringifies to the whole response, headers and body
    included, and Launchpad's 503 body is two kilobytes of HTML apology.
    Logged once per retry per call, that buries the rest of the build log.
    """
    status = _status_of(exc)
    if status is not None:
        return "%s: HTTP %s" % (type(exc).__name__, status)
    try:
        lines = str(exc).strip().splitlines()
        summary = lines[0] if lines else ""
    except Exception:
        summary = ""
    if not summary:
        return type(exc).__name__
    return "%s: %s" % (type(exc).__name__, summary)


def is_transient(exc):
    """Is this exception worth making the same call again for?

    Deliberately conservative.  Anything that looks like the remote end saying
    "no" rather than "not now" -- 400, 401, 403, 404 -- is not transient, and
    retrying it only wastes the build's time.
    """
    status = _status_of(exc)
    if status is not None:
        return _status_is_transient(status)

    if isinstance(exc, urllib.error.URLError):
        # urlopen wraps the underlying socket error in .reason.
        if isinstance(exc.reason, BaseException):
            return is_transient(exc.reason)
        return False

    # requests' ConnectionError and Timeout derive from OSError, so they have
    # to be classified before the generic errno check below.
    if _requests is not None and isinstance(
        exc, (_requests.ConnectionError, _requests.Timeout)
    ):
        return True

    if _httplib2 is not None and isinstance(exc, _httplib2.HttpLib2Error):
        # Includes ServerNotFoundError, i.e. DNS didn't answer.
        return True

    if isinstance(exc, ssl.SSLCertVerificationError):
        # A certificate that fails to verify will still fail in five minutes.
        return False
    if isinstance(exc, ssl.SSLError):
        return True
    if isinstance(exc, socket.gaierror):
        # DNS is a network service like any other, and blips like one.
        return True
    if isinstance(exc, socket.timeout):
        # socket.timeout is TimeoutError from 3.10 on.
        return True
    if isinstance(exc, http.client.HTTPException):
        # BadStatusLine, IncompleteRead, RemoteDisconnected: the connection
        # died in the middle of the conversation.
        return True
    if isinstance(exc, OSError):
        return exc.errno in _TRANSIENT_ERRNOS
    return False


def retry_call(
    func,
    *args,
    description=None,
    tries=DEFAULT_TRIES,
    delay=DEFAULT_DELAY,
    backoff=DEFAULT_BACKOFF,
    max_delay=DEFAULT_MAX_DELAY,
    transient=is_transient,
    **kwargs,
):
    """Call func(*args, **kwargs), retrying it if it fails transiently.

    `transient` is either a predicate taking the exception, or a tuple of
    exception classes to treat as transient.

    `description` is a short, stable label used for the log line and for the
    retry counter; keep the varying detail (URLs and so on) out of it, or the
    counter fills up with singletons.

    Whatever finally escapes is the exception `func` raised, not a wrapper, so
    callers can keep catching exactly what they caught before.
    """
    if description is None:
        description = getattr(func, "__name__", repr(func))
    if isinstance(transient, tuple):
        transient_classes = transient

        def transient(exc):
            return isinstance(exc, transient_classes)

    tries = max(1, int(tries))
    wait = float(delay)
    for attempt in range(1, tries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt >= tries or not transient(exc):
                raise
            stats[description] += 1
            # Jitter, so that several arches failing at the same moment don't
            # come back in lockstep and hit Launchpad as one thundering herd.
            sleep_for = min(wait, max_delay) * (0.5 + random.random())
            logger.warning(
                "%s failed (attempt %d/%d): %s; retrying in %ds",
                description,
                attempt,
                tries,
                describe(exc),
                round(sleep_for),
            )
            _sleep(sleep_for)
            wait = min(wait * backoff, max_delay)


def log_stats():
    """Log a one-line summary of everything we had to retry, if anything."""
    if not stats:
        return
    logger.info(
        "Retried transient remote failures: %s",
        ", ".join("%s x%d" % (key, count) for key, count in sorted(stats.items())),
    )
