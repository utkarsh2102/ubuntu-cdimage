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

__metaclass__ = type

from collections import defaultdict, Mapping

try:
    from launchpadlib.launchpad import Launchpad
    from lazr.restfulclient.errors import NotFound
    from lazr.restfulclient.resource import Resource
    launchpad_available = True
except ImportError:
    Resource = type
    launchpad_available = False


class _CachingDict(Mapping):
    def __init__(self, lp_mapping, item_factory=None):
        self._lp_mapping = lp_mapping
        if item_factory is None:
            item_factory = lambda v: v
        self._item_factory = item_factory
        self._cache = {}

    def __getitem__(self, key):
        if key not in self._cache:
            self._cache[key] = self._item_factory(self._lp_mapping[key])
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
            self._das_cache[archtag] = (
                self._lp_distroseries.getDistroArchSeries(archtag=archtag))
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
                self._lp_distribution.getSeries(
                    name_or_version=name_or_version))
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
        self._current_build_cache[archtag][unique_key] = (
            self._lp_livefs.requestBuild(
                distro_arch_series=distro_arch_series, unique_key=unique_key,
                **kwargs))
        return self._current_build_cache[archtag][unique_key]

    def getLatestBuild(self, distro_arch_series, unique_key=None):
        archtag = distro_arch_series.architecture_tag
        if unique_key not in self._current_build_cache[archtag]:
            # If we didn't run the build ourselves, then use the latest
            # completed build.
            for build in self.completed_builds:
                if build.buildstate == "Successfully built":
                    self._current_build_cache[archtag][unique_key] = build
                    break
            else:
                raise NotFound("No successful builds found")
        return self._current_build_cache[archtag][unique_key]


class _CachingLiveFSes:
    def __init__(self, lp_livefses):
        self._lp_livefses = lp_livefses
        # [owner][distribution][distroseries][livefs]
        self._cache = defaultdict(
            lambda: defaultdict(lambda: defaultdict(dict)))

    def __getattr__(self, name):
        return getattr(self._lp_livefses, name)

    def getByName(self, owner=None, distro_series=None, name=None):
        cache = self._cache[owner.name][distro_series.distribution.name][
            distro_series.name]
        if name not in cache:
            cache[name] = _CachingLiveFS(
                distro_series,
                self._lp_livefses.getByName(
                    owner=owner, distro_series=distro_series, name=name))
        return cache[name]


def login(instance):
    return Launchpad.login_with("ubuntu-cdimage", instance, version="devel")


class _LaunchpadCache:
    def __init__(self, instance=None):
        assert launchpad_available
        if not instance:
            instance = "production"
        self.lp = login(instance)
        self.people = _CachingDict(self.lp.people)
        self.distributions = _CachingDict(
            self.lp.distributions, _CachingDistribution)
        self.livefses = _CachingLiveFSes(self.lp.livefses)

    def __getattr__(self, name):
        return getattr(self.lp, name)


launchpad_cache = None


def get_launchpad(instance=None):
    global launchpad_cache
    if launchpad_cache is None:
        launchpad_cache = _LaunchpadCache(instance=instance)
    return launchpad_cache
