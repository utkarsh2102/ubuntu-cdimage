# Copyright (C) 2013 Canonical Ltd.
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

"""Set project-specific environment variables."""

import os


# Be careful about the values here; in most cases they are passed to
# debian-cd, which will get upset if they contain a space, hence all the
# odd-looking hyphens.  ubuntu-zh_CN is an exception to this.
project_map = {
    "ubuntu": "Ubuntu",
    "ubuntu-zh_CN": "Ubuntu Chinese Edition",
    "kubuntu": "Kubuntu",
    "kubuntu-active": "Kubuntu-Active",
    "edubuntu": "Edubuntu",
    "xubuntu": "Xubuntu",
    "gobuntu": "Gobuntu",
    "ubuntu-server": "Ubuntu-Server",
    "jeos": "Ubuntu-JeOS",
    "ubuntu-mid": "Ubuntu-MID",
    "ubuntu-netbook": "Ubuntu-Netbook",
    "ubuntu-headless": "Ubuntu-Headless",
    "ubuntustudio": "Ubuntu-Studio",
    "mythbuntu": "Mythbuntu",
    "lubuntu": "Lubuntu",
    "ubuntukylin": "UbuntuKylin",
    "ubuntu-gnome": "Ubuntu-GNOME",
    "ubuntu-moblin-remix": "Ubuntu-Moblin-Remix",
    "livecd-base": "LiveCD-Base",
    "ubuntu-core": "Ubuntu-Core",
    "tocd3": "TheOpenCDv3",
    "tocd3.1": "TheOpenCDv3.1",
}


def setenv_for_project(project):
    full_project = project
    if "UBUNTU_DEFAULTS_LOCALE" in os.environ:
        full_project = "-".join([
            full_project, os.environ["UBUNTU_DEFAULTS_LOCALE"]])
    if full_project not in project_map:
        return False
    os.environ["PROJECT"] = project
    os.environ["CAPPROJECT"] = project_map[full_project]
    return True
