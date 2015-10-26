# Copyright (C) 2012 Canonical Ltd.
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

"""Read cdimage configuration.

Most of this is a transitional measure to permit shell and Python programs
to co-exist until such time as the whole of cdimage is rewritten.
"""

from collections import Iterable, defaultdict
import fnmatch
import operator
import os
import sys

from cdimage import osextras

__metaclass__ = type


class UnknownSeries(Exception):
    pass


all_series = []


class Series(Iterable):
    def __init__(self, name, version, displayname, distribution="ubuntu",
                 **kwargs):
        self.name = name
        self.version = version
        self.displayname = displayname
        self.distribution = distribution
        self._index = None
        for key, value in kwargs.items():
            setattr(self, key, value)

    @classmethod
    def find_by_name(self, name):
        if "/" in name:
            distribution, name = name.split("/", 1)
        else:
            distribution = "ubuntu"
        for series in all_series:
            if series.distribution == distribution and series.name == name:
                return series
        else:
            raise ValueError("No series named %s/%s" % (distribution, name))

    @classmethod
    def find_by_version(self, version):
        if "/" in version:
            distribution, version = version.split("/", 1)
        else:
            distribution = "ubuntu"
        for series in all_series:
            if (series.distribution == distribution and
                    series.version == version):
                return series
        else:
            raise ValueError(
                "No series with version %s/%s" % (distribution, version))

    @classmethod
    def latest(self, distribution="ubuntu"):
        for series in reversed(all_series):
            if series.distribution == distribution:
                return series
        raise ValueError("No series with distribution %s" % distribution)

    def __str__(self):
        return self.name

    @property
    def full_name(self):
        if self.distribution == "ubuntu":
            return self.name
        else:
            return "%s/%s" % (self.distribution, self.name)

    def __iter__(self):
        yield self.name
        yield self.version
        yield self.displayname

    @property
    def index(self):
        if self._index is None:
            self._index = [
                series.name for series in all_series].index(self.name)
        return self._index

    @property
    def is_latest(self):
        for series in reversed(all_series):
            if self.distribution == series.distribution:
                return self == series
        return False

    def _compare(self, other, method):
        if not isinstance(other, Series):
            other = self.find_by_name(other)
        return method(self.index, other.index)

    def __lt__(self, other):
        return self._compare(other, operator.lt)

    def __le__(self, other):
        return self._compare(other, operator.le)

    def __eq__(self, other):
        return self._compare(other, operator.eq)

    def __ne__(self, other):
        return self._compare(other, operator.ne)

    def __ge__(self, other):
        return self._compare(other, operator.ge)

    def __gt__(self, other):
        return self._compare(other, operator.gt)

    def displayversion(self, project):
        version = getattr(self, "pointversion", self.version)
        if (project in getattr(self, "lts_projects", []) or
                getattr(self, "all_lts_projects", False)):
            version += " LTS"
        return version


# TODO: This should probably come from a configuration file.
all_series.extend([
    Series("warty", "4.10", "Warty Warthog"),
    Series("hoary", "5.04", "Hoary Hedgehog"),
    Series("breezy", "5.10", "Breezy Badger"),
    Series(
        "dapper", "6.06", "Dapper Drake",
        pointversion="6.06.2",
        lts_projects=["ubuntu", "kubuntu", "edubuntu", "ubuntu-server"]),
    Series("edgy", "6.10", "Edgy Eft"),
    Series("feisty", "7.04", "Feisty Fawn"),
    Series("gutsy", "7.10", "Gutsy Gibbon"),
    Series(
        "hardy", "8.04", "Hardy Heron",
        pointversion="8.04.4", lts_projects=["ubuntu", "ubuntu-server"]),
    Series("intrepid", "8.10", "Intrepid Ibex"),
    Series("jaunty", "9.04", "Jaunty Jackalope"),
    Series("karmic", "9.10", "Karmic Koala"),
    Series(
        "lucid", "10.04", "Lucid Lynx",
        pointversion="10.04.4",
        lts_projects=["ubuntu", "kubuntu", "ubuntu-server"]),
    Series("maverick", "10.10", "Maverick Meerkat"),
    Series("natty", "11.04", "Natty Narwhal"),
    Series("oneiric", "11.10", "Oneiric Ocelot"),
    Series(
        "precise", "12.04", "Precise Pangolin",
        pointversion="12.04.5",
        lts_projects=[
            "ubuntu", "kubuntu", "ubuntu-server", "edubuntu", "xubuntu",
            "mythbuntu", "ubuntustudio",
        ]),
    Series("quantal", "12.10", "Quantal Quetzal"),
    Series("raring", "13.04", "Raring Ringtail"),
    Series("saucy", "13.10", "Saucy Salamander"),
    Series(
        "trusty", "14.04", "Trusty Tahr",
        pointversion="14.04.3",
        all_lts_projects=True),
    Series("utopic", "14.10", "Utopic Unicorn"),
    Series("vivid", "15.04", "Vivid Vervet"),
    Series("wily", "15.10", "Wily Werewolf"),
    Series("xenial", "16.04", "Xenial Xerus"),

    Series("14.09", "14.09", "RTM 14.09", distribution="ubuntu-rtm"),
    Series(
        "14.09-factory", "14.09.1", "RTM 14.09-factory",
        distribution="ubuntu-rtm"),
])

all_touch_targets = []


class Touch:
    def __init__(self, subarch, android_arch, ubuntu_arch):
        self.subarch = subarch
        self.android_arch = android_arch
        self.ubuntu_arch = ubuntu_arch

    @classmethod
    def list_android_arches(self):
        return list(set([touch.android_arch for touch in all_touch_targets]))

    @classmethod
    def list_ubuntu_arches(self):
        return list(set([touch.ubuntu_arch for touch in all_touch_targets]))

    @classmethod
    def list_targets_by_ubuntu_arch(self, arch):
        return [target for target in all_touch_targets
                if target.ubuntu_arch == arch]


# TODO: This should probably come from a configuration file.
all_touch_targets.extend([
    Touch("mako", "armel", "armhf"),
    Touch("manta", "armel", "armhf"),
    Touch("generic", "armel", "armhf"),
    Touch("generic_x86", "i386", "i386"),
    Touch("flo", "armel", "armhf"),
])

_whitelisted_keys = (
    "PROJECT",
    "CAPPROJECT",
    "DIST",
    "PROPOSED",
    "ALL_PROJECTS",
    "ARCHES",
    "CPUARCHES",
    "GNUPG_DIR",
    "SIGNING_KEYID",
    "LOCAL",
    "LOCALDEBS",
    "LOCAL_SEEDS",
    "TRIGGER_MIRRORS",
    "TRIGGER_MIRRORS_ASYNC",
    "DEBUG",
    "DATE",
    "DATE_SUFFIX",
    "IMAGE_TYPE",
    "LIVECD",
    "LIVECD_BASE",
    "SUBPROJECT",
    "UBUNTU_DEFAULTS_LOCALE",
    "SSH_ORIGINAL_COMMAND",
    "EXTRA_PPAS",
)


class Config(defaultdict):
    def __init__(self, read=True, **kwargs):
        super(Config, self).__init__(str)
        if "CDIMAGE_ROOT" not in os.environ:
            root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            root = os.path.realpath(root)
            os.environ["CDIMAGE_ROOT"] = root
        self.root = os.environ["CDIMAGE_ROOT"]
        self.fix_paths()
        for key, value in kwargs.items():
            self[key] = value
        config_path = os.path.join(self.root, "etc", "config")
        if read:
            if os.path.exists(config_path):
                self.read(config_path)
            else:
                self.read()

    def read(self, config_path=None):
        for key, value in osextras.read_shell_config(
                config_path, _whitelisted_keys):
            if key.startswith("CDIMAGE_") or key in _whitelisted_keys:
                super(Config, self).__setitem__(key, value)

        # Special entries.
        if "DIST" in self:
            super(Config, self).__setitem__(
                "DIST", Series.find_by_name(self["DIST"]))
        if "ARCHES" not in self:
            self.set_default_arches()
        if "CPUARCHES" not in self:
            self.set_default_cpuarches()

    def __setitem__(self, key, value):
        config_value = value
        env_value = value
        if key == "DIST":
            if isinstance(value, Series):
                env_value = value.name
            elif value:
                config_value = Series.find_by_name(value)
        super(Config, self).__setitem__(key, config_value)
        os.environ[key] = env_value

    def __delitem__(self, key):
        super(Config, self).__delitem__(key)
        os.environ.pop(key, None)

    def _add_package(self, package):
        path = os.path.join(self.root, package)
        if os.path.isdir(path):
            sys.path.insert(0, path)

    def fix_paths(self):
        bin_dir = os.path.join(self.root, "bin")
        path_elements = os.environ.get("PATH", "").split(os.pathsep)
        if bin_dir not in path_elements:
            path_elements.insert(0, bin_dir)
            os.environ["PATH"] = os.pathsep.join(path_elements)
        self._add_package("germinate")
        self._add_package("ubuntu-archive-tools")

    def match_series(self, series):
        if "/" in series:
            distribution, series = series.split("/", 1)
            if distribution != self.distribution:
                return False
        else:
            distribution = "ubuntu"

        if series == "*":
            return True
        elif "-" in series:
            series_start, series_end = series.split("-", 1)
            in_range = False
            if not series_start:
                in_range = True
            for tryseries in all_series:
                if tryseries.distribution != distribution:
                    continue
                if tryseries.name == series_start:
                    in_range = True
                if tryseries.name == self.series:
                    return in_range
                if tryseries.name == series_end:
                    in_range = False
            else:
                return False
        else:
            return series == self.series

    def set_default_arches(self):
        default_arches = os.path.join(self.root, "etc", "default-arches")
        if not os.path.exists(default_arches):
            return None
        want_project_bits = [self.project]
        if self.subproject:
            want_project_bits.append(self.subproject)
        if self["UBUNTU_DEFAULTS_LOCALE"]:
            want_project_bits.append(self["UBUNTU_DEFAULTS_LOCALE"])
        want_project = "-".join(want_project_bits)
        with open(default_arches) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    project, image_type, series, arches = line.split(None, 3)
                except ValueError:
                    continue
                if not fnmatch.fnmatchcase(want_project, project):
                    continue
                if not fnmatch.fnmatchcase(self.image_type, image_type):
                    continue
                if not self.match_series(series):
                    continue
                self["ARCHES"] = arches
                return arches
        return None

    def set_default_cpuarches(self):
        self["CPUARCHES"] = " ".join(
            sorted(set(arch.split("+")[0] for arch in self.arches)))

    def limit_arches(self, new_arches):
        self["ARCHES"] = " ".join(
            arch for arch in self.arches if arch in new_arches)
        new_cpuarches = " ".join(
            sorted(set(arch.split("+")[0] for arch in new_arches)))
        self["CPUARCHES"] = " ".join(
            cpuarch for cpuarch in self.cpuarches if cpuarch in new_cpuarches)

    @property
    def project(self):
        return self["PROJECT"]

    @property
    def capproject(self):
        return self["CAPPROJECT"]

    @property
    def subproject(self):
        return self["SUBPROJECT"]

    @property
    def distribution(self):
        return self["DIST"].distribution

    @property
    def series(self):
        return str(self["DIST"])

    @property
    def full_series(self):
        return self["DIST"].full_name

    @property
    def arches(self):
        return self["ARCHES"].split()

    @property
    def cpuarches(self):
        return self["CPUARCHES"].split()

    @property
    def image_type(self):
        return self["IMAGE_TYPE"]

    @property
    def all_projects(self):
        return self["ALL_PROJECTS"].split()

    def export(self):
        ret = dict(os.environ)
        for key, value in self.items():
            if key == "DIST":
                ret[key] = value.name
            else:
                ret[key] = value
        return ret
