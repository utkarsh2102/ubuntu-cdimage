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

This is a transitional measure to permit shell and Python programs to
co-exist until such time as the whole of cdimage is rewritten.
"""

__metaclass__ = type

from collections import defaultdict
import os
import re
import subprocess


_whitelisted_keys = (
    "CDIMAGE_ROOT",
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
    "LOCAL_SEEDS",
    "TRIGGER_MIRRORS",
    "TRIGGER_MIRRORS_ASYNC",
    "DEBOOTSTRAPROOT",
    )


class Config(defaultdict):
    def __init__(self):
        super(Config, self).__init__(str)
        if "CDIMAGE_ROOT" not in os.environ:
            os.environ["CDIMAGE_ROOT"] = "/srv/cdimage.ubuntu.com"
        self.root = os.environ["CDIMAGE_ROOT"]
        self._read()

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

    def _read(self):
        config_path = os.path.join(self.root, "etc", "config")
        commands = [". %s" % self._shell_escape(config_path)]
        for key in _whitelisted_keys:
            commands.append("printf '%%s\\0' \"%s=$%s\"" % (key, key))
        env = self._read_nullsep_output(["sh", "-c", "; ".join(commands)])
        self.update(env)


config = Config()
