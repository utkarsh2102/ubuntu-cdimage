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
# odd-looking hyphens.
# For projects that use debian-cd, it will construct an ISO9660 volume ID
# as "$(CAPPROJECT) $(DEBVERSION) $(ARCH)", e.g. "Ubuntu 14.10 amd64"; for
# powerpc, $(ARCH) is abbreviated to "ppc".  The volume ID is limited to 32
# characters.  This therefore imposes a limit on the length of project_map
# values of 25 - (length of longest relevant architecture name).
project_map = {
    "ubuntu": "Ubuntu",
    "kubuntu": "Kubuntu",
    "edubuntu": "Edubuntu",
    "xubuntu": "Xubuntu",
    "ubuntu-server": "Ubuntu-Server",
    "ubuntustudio": "Ubuntu-Studio",
    "lubuntu": "Lubuntu",
    "lubuntu-next": "Lubuntu-Next",
    "ubuntukylin": "Ubuntu-Kylin",
    "ubuntu-gnome": "Ubuntu-GNOME",
    "ubuntu-budgie": "Ubuntu-Budgie",
    "ubuntu-mate": "Ubuntu-MATE",
    "ubuntu-unity": "Ubuntu-Unity",
    "ubuntucinnamon": "Ubuntu-Cinnamon",
    "livecd-base": "LiveCD-Base",
    "ubuntu-core": "Ubuntu Core",
    "ubuntu-core-desktop": "Ubuntu Core Desktop",
    "ubuntu-appliance": "Ubuntu Appliance",
    "ubuntu-base": "Ubuntu-Base",
    "ubuntu-mini-iso": "Ubuntu-Mini-ISO",
}


def setenv_for_project(project):
    if project not in project_map:
        return False
    os.environ["PROJECT"] = project
    os.environ["CAPPROJECT"] = project_map[project]
    return True
