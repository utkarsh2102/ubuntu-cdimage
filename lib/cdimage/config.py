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

__metaclass__ = type

from collections import defaultdict, namedtuple
import fnmatch
import operator
import os
import re
import subprocess
import sys


class UnknownSeries(Exception):
    pass


BaseSeries = namedtuple("BaseSeries", ["name", "version", "displayname"])
all_series = []


class Series(BaseSeries):
    def __init__(self, *args, **kwargs):
        self._index = None

    @classmethod
    def find_by_name(self, name):
        for series in all_series:
            if series.name == name:
                return series
        else:
            raise ValueError("No series named %s" % name)

    @classmethod
    def find_by_version(self, version):
        for series in all_series:
            if series.version == version:
                return series
        else:
            raise ValueError("No series with version %s" % version)

    @classmethod
    def latest(self):
        return all_series[-1]

    def __str__(self):
        return self.name

    @property
    def index(self):
        if self._index is None:
            self._index = [
                series.name for series in all_series].index(self.name)
        return self._index

    @property
    def is_latest(self):
        return self == all_series[-1]

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


# TODO: This should probably come from a configuration file.
all_series.extend([
    Series("warty", "4.10", "Warty Warthog"),
    Series("hoary", "5.04", "Hoary Hedgehog"),
    Series("breezy", "5.10", "Breezy Badger"),
    Series("dapper", "6.06", "Dapper Drake"),
    Series("edgy", "6.10", "Edgy Eft"),
    Series("feisty", "7.04", "Feisty Fawn"),
    Series("gutsy", "7.10", "Gutsy Gibbon"),
    Series("hardy", "8.04", "Hardy Heron"),
    Series("intrepid", "8.10", "Intrepid Ibex"),
    Series("jaunty", "9.04", "Jaunty Jackalope"),
    Series("karmic", "9.10", "Karmic Koala"),
    Series("lucid", "10.04", "Lucid Lynx"),
    Series("maverick", "10.10", "Maverick Meerkat"),
    Series("natty", "11.04", "Natty Narwhal"),
    Series("oneiric", "11.10", "Oneiric Ocelot"),
    Series("precise", "12.04", "Precise Pangolin"),
    Series("quantal", "12.10", "Quantal Quetzal"),
    Series("raring", "13.04", "Raring Ringtail"),
])


_whitelisted_keys = (
    "PROJECT",
    "CAPPROJECT",
    "ALL_DISTS",
    "DIST",
    "ALL_PROJECTS",
    "ARCHES",
    "CPUARCHES",
    "GNUPG_DIR",
    "SIGNING_KEYID",
    "BRITNEY",
    "LOCAL",
    "LOCALDEBS",
    "LOCAL_SEEDS",
    "TRIGGER_MIRRORS",
    "TRIGGER_MIRRORS_ASYNC",
    "DEBOOTSTRAPROOT",
    "DEBUG",
    "DATE",
    "DATE_SUFFIX",
    "IMAGE_TYPE",
    "LIVECD",
    "LIVECD_BASE",
    "SUBPROJECT",
    "UBUNTU_DEFAULTS_LOCALE",
)


class Config(defaultdict):
    def __init__(self, read=True, **kwargs):
        super(Config, self).__init__(str)
        if "CDIMAGE_ROOT" not in os.environ:
            os.environ["CDIMAGE_ROOT"] = "/srv/cdimage.ubuntu.com"
        self.root = os.environ["CDIMAGE_ROOT"]
        for key, value in kwargs.items():
            self[key] = value
        config_path = os.path.join(self.root, "etc", "config")
        if read:
            if os.path.exists(config_path):
                self.read(config_path)
            else:
                self.read()
            self.fix_paths()
        if "IMAGE_TYPE" in kwargs:
            if "ARCHES" not in os.environ:
                self.set_default_arches()
            if "CPUARCHES" not in os.environ:
                self.set_default_cpuarches()

    def _read_nullsep_output(self, command):
        raw = subprocess.Popen(
            command, stdout=subprocess.PIPE,
            universal_newlines=True).communicate()[0]
        out = {}
        for line in raw.split("\0"):
            try:
                key, value = line.split("=", 1)
                out[key] = value
            except ValueError:
                continue
        return out

    def _shell_escape(self, arg):
        if re.match(r"^[a-zA-Z0-9+,./:=@_-]+$", arg):
            return arg
        else:
            return "'%s'" % arg.replace("'", "'\\''")

    def read(self, config_path=None):
        commands = []
        if config_path is not None:
            commands.append(". %s" % self._shell_escape(config_path))
        commands.append("cat /proc/self/environ")
        for key in _whitelisted_keys:
            commands.append(
                "test -z \"${KEY+x}\" || printf '%s\\0' \"KEY=$KEY\"".replace(
                    "KEY", key))
        env = self._read_nullsep_output(["sh", "-c", "; ".join(commands)])
        for key, value in env.items():
            if key.startswith("CDIMAGE_") or key in _whitelisted_keys:
                super(Config, self).__setitem__(key, value)

        # Special entries.
        if "DIST" in self:
            super(Config, self).__setitem__(
                "DIST", Series.find_by_name(self["DIST"]))

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

    def _default_arches_match_series(self, series):
        if series == "*":
            return True
        elif "-" in series:
            series_start, series_end = series.split("-", 1)
            in_range = False
            if not series_start:
                in_range = True
            for tryseries in self.all_series:
                if tryseries == series_start:
                    in_range = True
                if tryseries == self.series:
                    return in_range
                if tryseries == series_end:
                    in_range = False
            else:
                return False
        else:
            return series == self.series

    def set_default_arches(self):
        default_arches = os.path.join(self.root, "etc", "default-arches")
        if not os.path.exists(default_arches):
            return None
        with open(default_arches) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    project, image_type, series, arches = line.split(None, 3)
                except ValueError:
                    continue
                if not fnmatch.fnmatchcase(self.project, project):
                    continue
                if not fnmatch.fnmatchcase(self.image_type, image_type):
                    continue
                if not self._default_arches_match_series(series):
                    continue
                self["ARCHES"] = arches
                return arches
        return None

    def set_default_cpuarches(self):
        self["CPUARCHES"] = " ".join(
            sorted(set(arch.split("+")[0] for arch in self.arches)))

    @property
    def project(self):
        return self["PROJECT"]

    @property
    def capproject(self):
        return self["CAPPROJECT"]

    @property
    def series(self):
        return str(self["DIST"])

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

    @property
    def all_series(self):
        return self["ALL_DISTS"].split()

    def export(self):
        ret = dict(os.environ)
        for key, value in self.items():
            if key == "DIST":
                ret[key] = value.name
            else:
                ret[key] = value
        return ret


config = Config()
