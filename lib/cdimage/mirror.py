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

"""Logic to find which mirror to use."""

__metaclass__ = type

import os


class UnknownMirror(Exception):
    pass


def find_mirror(config, arch):
    if config["CDIMAGE_UNSUPPORTED"]:
        return os.path.join(config.root, "ftp-universe")

    cpuarch = arch.split("+")[0]
    if cpuarch in ("amd64", "i386"):
        return os.path.join(config.root, "ftp")
    elif cpuarch == "powerpc":
        # https://lists.ubuntu.com/archives/ubuntu-announce/2007-February/000098.html
        if config["DIST"] <= "edgy":
            return os.path.join(config.root, "ftp")
        else:
            return os.path.join(config.root, "ftp-ports")
    elif cpuarch == "sparc":
        # https://lists.ubuntu.com/archives/ubuntu-devel-announce/2008-March/000400.html
        if config["DIST"] >= "dapper" and config["DIST"] <= "gutsy":
            return os.path.join(config.root, "ftp")
        else:
            return os.path.join(config.root, "ftp-ports")
    elif cpuarch in ("armel", "hppa", "ia64", "lpia"):
        return os.path.join(config.root, "ftp-ports")
    else:
        raise UnknownMirror("No mirror known for %s" % arch)
