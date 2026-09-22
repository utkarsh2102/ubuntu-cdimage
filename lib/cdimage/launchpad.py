# Copyright (C) 2014 Canonical Ltd.
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

"""Basics of Launchpad interaction."""

from __future__ import print_function

from collections import defaultdict
import os

try:
    from collections.abc import Mapping
except ImportError:
    from collections import Mapping

from launchpadlib.launchpad import Launchpad
from lazr.restfulclient.resource import Resource

from cdimage import retry

__metaclass__ = type


DEFAULT_TIMEOUT = 120
DEFAULT_RETRIES = 5
DEFAULT_RETRY_DELAY = 10

# Where the CDIMAGE_LP_* knobs are read from.  Set from the run's Config when
# we are given one, so that they work from etc/config as well as from the
# environment.  A module global is as good a home for this as launchpad_cache
# itself: there is one Launchpad connection per run.
_tunables = os.environ


def _setting(key, default):
    return retry.int_setting(_tunables, key, default)


def retry_lp(func, *args, description, **kwargs):
    """Call a Launchpad API method, waiting out Launchpad's bad moments.

    Public because livefs works directly with build objects, which are plain
    lazr entries rather than one of the caching wrappers below.

    lazr.restfulclient retries 502 and 503 by itself, but its whole budget is
    0+1+2+4+8+16 = 31 seconds, and it covers neither 500, 504 and 429 nor a
    dropped connection.  Real outages outlast that comfortably: the 503 that
    killed a server build on 2026-09-16 was still going two and a half minutes
    in, by which point lazr had long since given up.

    Every Launchpad round trip in the livefs code passes through one of the
    caching wrappers below, so wrapping them covers the lot -- and because
    they cache, a retry only ever happens on a real cache miss.
    """
    return retry.retry_call(
        func,
        *args,
        description="Launchpad %s" % description,
        tries=_setting("CDIMAGE_LP_RETRIES", DEFAULT_RETRIES),
        delay=_setting("CDIMAGE_LP_RETRY_DELAY", DEFAULT_RETRY_DELAY),
        **kwargs,
    )


class _CachingDict(Mapping):
    def __init__(self, lp_mapping, item_factory=None):
        self._lp_mapping = lp_mapping
        if item_factory is None:

            def item_factory(v):
                return v

        self._item_factory = item_factory
        self._cache = {}

    def __getitem__(self, key):
        if key not in self._cache:
            self._cache[key] = self._item_factory(
                retry_lp(lambda: self._lp_mapping[key], description="lookup")
            )
        return self._cache[key]

    def __iter__(self):
        return iter(self._lp_mapping)

    def __len__(self):
        return len(self._lp_mapping)


class _CachingDistroSeries(Resource):
    def __init__(self, lp_distribution, lp_distroseries):
        self._lp_distribution = lp_distribution
        self._lp_distroseries = lp_distroseries
        self._das_cache = {}

    def __getattr__(self, name):
        return getattr(self._lp_distroseries, name)

    @property
    def distribution(self):
        return self._lp_distribution

    def getDistroArchSeries(self, archtag=None):
        if archtag not in self._das_cache:
            self._das_cache[archtag] = retry_lp(
                self._lp_distroseries.getDistroArchSeries,
                archtag=archtag,
                description="getDistroArchSeries",
            )
        return self._das_cache[archtag]


class _CachingDistribution(Resource):
    def __init__(self, lp_distribution):
        self._lp_distribution = lp_distribution
        self._series_cache = {}

    def __getattr__(self, name):
        return getattr(self._lp_distribution, name)

    def getSeries(self, name_or_version=None):
        if name_or_version not in self._series_cache:
            self._series_cache[name_or_version] = _CachingDistroSeries(
                self,
                retry_lp(
                    self._lp_distribution.getSeries,
                    name_or_version=name_or_version,
                    description="getSeries",
                ),
            )
        return self._series_cache[name_or_version]


class _CachingLiveFS(Resource):
    def __init__(self, lp_distroseries, lp_livefs):
        self._lp_distroseries = lp_distroseries
        self._lp_livefs = lp_livefs
        # [architecture][subarchitecture]
        self._current_build_cache = defaultdict(dict)

    def __getattr__(self, name):
        return getattr(self._lp_livefs, name)

    @property
    def distro_series(self):
        return self._lp_distroseries

    def requestBuild(self, distro_arch_series=None, unique_key=None, **kwargs):
        archtag = distro_arch_series.architecture_tag
        # Requesting a build isn't idempotent, so retrying it could in
        # principle ask for the same build twice.  Launchpad rejects that with
        # LiveFSBuildAlreadyPending, which is a 400 and so not retried again,
        # meaning the worst case is a clear error rather than two builds --
        # a good trade for surviving a 503 at request time.
        self._current_build_cache[archtag][unique_key] = retry_lp(
            self._lp_livefs.requestBuild,
            distro_arch_series=distro_arch_series,
            unique_key=unique_key,
            description="requestBuild",
            **kwargs,
        )
        return self._current_build_cache[archtag][unique_key]

    def _find_latest_build(self, distro_arch_series, unique_key):
        # completed_builds is paged, so this walks the network as it goes and
        # a blip part-way through restarts the search.  That's fine; it's a
        # read, and the retry wrapper is around the whole loop for exactly
        # that reason.
        for build in self.completed_builds:
            if (
                build.distro_arch_series == distro_arch_series
                and (not unique_key or build.unique_key == unique_key)
                and build.buildstate == "Successfully built"
            ):
                return build
        raise Exception("No successful builds found")

    def getLatestBuild(self, distro_arch_series, unique_key=None):
        archtag = distro_arch_series.architecture_tag
        if unique_key not in self._current_build_cache[archtag]:
            # If we didn't run the build ourselves, then use the latest
            # completed build for this DAS.
            self._current_build_cache[archtag][unique_key] = retry_lp(
                self._find_latest_build,
                distro_arch_series,
                unique_key,
                description="getLatestBuild",
            )
        return self._current_build_cache[archtag][unique_key]


class _CachingLiveFSes:
    def __init__(self, lp_livefses):
        self._lp_livefses = lp_livefses
        # [owner][distribution][distroseries][livefs]
        self._cache = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))

    def __getattr__(self, name):
        return getattr(self._lp_livefses, name)

    def getByName(self, owner=None, distro_series=None, name=None):
        cache = self._cache[owner.name][distro_series.distribution.name][
            distro_series.name
        ]
        if name not in cache:
            cache[name] = _CachingLiveFS(
                distro_series,
                retry_lp(
                    self._lp_livefses.getByName,
                    owner=owner,
                    distro_series=distro_series,
                    name=name,
                    description="getByName",
                ),
            )
        return cache[name]


def login(instance):
    # Without a timeout, httplib2 blocks forever on a connection that never
    # answers, and the run sits there holding the build lock for hours.  A
    # stall we can see is a stall we can retry.
    timeout = _setting("CDIMAGE_LP_TIMEOUT", DEFAULT_TIMEOUT)
    try:
        import launchpadlib.credentials

        store = launchpadlib.credentials.KeyringCredentialStore(None, fallback=True)
        lp = Launchpad.login_with(
            "ubuntu-cdimage",
            instance,
            version="devel",
            credential_store=store,
            timeout=timeout,
        )
    except TypeError:
        # prior to focal (1.10.13), the fallback= kwarg didn't exist and
        # Launchpadlib worked under sudo
        lp = Launchpad.login_with(
            "ubuntu-cdimage", instance, version="devel", timeout=timeout
        )
    return lp


class _LaunchpadCache:
    def __init__(self, instance=None):
        if not instance:
            instance = "production"
        if instance == "dogfood":
            # Work around old service root in some versions of launchpadlib.
            instance = "https://api.dogfood.paddev.net/"
        self.lp = login(instance)
        self.people = _CachingDict(self.lp.people)
        self.distributions = _CachingDict(self.lp.distributions, _CachingDistribution)
        self.livefses = _CachingLiveFSes(self.lp.livefses)

    def __getattr__(self, name):
        return getattr(self.lp, name)


launchpad_cache = None


def get_launchpad(instance=None, config=None):
    global launchpad_cache, _tunables
    if config is not None:
        _tunables = config
    if launchpad_cache is None:
        # Logging in talks to Launchpad too, so it can fail the same way.
        launchpad_cache = retry_lp(
            _LaunchpadCache, instance=instance, description="login"
        )
    return launchpad_cache
