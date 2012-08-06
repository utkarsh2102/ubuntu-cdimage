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

"""Image publication trees."""

__metaclass__ = type

import os
import stat

from cdimage.config import Series
from cdimage.log import logger


# TODO: This should be in a configuration file.  ALL_PROJECTS is not
# currently suitable, because it only lists projects currently being built,
# but manifest generation needs to know about anything currently in a
# published tree.
projects = [
    "edubuntu",
    "gobuntu",
    "jeos",
    "kubuntu",
    "kubuntu-mobile",
    "kubuntu-netbook",
    "lubuntu",
    "mythbuntu",
    "ubuntu",
    "ubuntu-netbook",
    "ubuntu-server",
    "ubuntustudio",
    "xubuntu",
    ]


class Tree:
    def __init__(self, config, directory):
        self.config = config
        self.directory = directory

    def path_to_project(self, path):
        """Determine the project for a file based on its tree-relative path."""
        first_dir = path.split("/")[0]
        if first_dir in projects:
            return first_dir
        else:
            return "ubuntu"

    def name_to_series(self, name):
        """Return the series for a file basename."""
        raise NotImplementedError

    def path_to_manifest(self, path):
        """Return a manifest file entry for a tree-relative path.

        May raise ValueError for unrecognised file naming schemes.
        """
        if path.startswith("tocd"):
            return None
        project = self.path_to_project(path)
        base = os.path.basename(path)
        try:
            series = self.name_to_series(base)
        except ValueError:
            return None
        size = os.stat(os.path.join(self.directory, path)).st_size
        return "%s\t%s\t/%s\t%d" % (project, series, path, size)

    def manifest_file_allowed(self, path):
        """Return true if a given file is allowed in the manifest."""
        if path.endswith(".iso") or path.endswith(".img"):
            if stat.S_ISREG(os.stat(path).st_mode):
                return True
        return False

    def manifest_files(self):
        """Yield all the files to include in a manifest of this tree."""
        raise NotImplementedError

    def manifest(self):
        """Return a manifest of this tree as a sequence of lines."""
        return sorted(filter(
            lambda line: line is not None,
            (self.path_to_manifest(path) for path in self.manifest_files())))


class DailyTree(Tree):
    def name_to_series(self, name):
        """Return the series for a file basename."""
        dist = name.split("-")[0]
        return Series.find_by_name(dist)

    def manifest_files(self):
        """Yield all the files to include in a manifest of this tree."""
        seen_inodes = []
        for dirpath, dirnames, filenames in os.walk(
            self.directory, followlinks=True):
            # Detect loops.
            st = os.stat(dirpath)
            dev_ino = (st.st_dev, st.st_ino)
            seen_inodes.append(dev_ino)
            for i in range(len(dirnames) - 1, -1, -1):
                st = os.stat(os.path.join(dirpath, dirnames[i]))
                dev_ino = (st.st_dev, st.st_ino)
                if dev_ino in seen_inodes:
                    del dirnames[i]

            if "current" in dirpath.split(os.sep):
                relative_dirpath = dirpath[len(self.directory) + 1:]
                for filename in filenames:
                    path = os.path.join(dirpath, filename)
                    if self.manifest_file_allowed(path):
                        yield os.path.join(relative_dirpath, filename)

            if not dirnames:
                seen_inodes.pop()


class SimpleTree(Tree):
    def name_to_series(self, name):
        """Return the series for a file basename."""
        version = name.split("-")[1]
        try:
            return Series.find_by_version(".".join(version.split(".")[:2]))
        except ValueError:
            logger.warning("Unknown version: %s" % version)
            raise

    def manifest_files(self):
        """Yield all the files to include in a manifest of this tree."""
        main_filenames = set()
        for dirpath, dirnames, filenames in os.walk(self.directory):
            relative_dirpath = dirpath[len(self.directory) + 1:]
            try:
                del dirnames[dirnames.index(".pool")]
            except ValueError:
                pass
            for filename in filenames:
                path = os.path.join(dirpath, filename)
                if self.manifest_file_allowed(path):
                    main_filenames.add(filename)
                    yield os.path.join(relative_dirpath, filename)

        for dirpath, _, filenames in os.walk(self.directory):
            if os.path.basename(dirpath) == ".pool":
                relative_dirpath = dirpath[len(self.directory) + 1:]
                for filename in filenames:
                    if filename not in main_filenames:
                        path = os.path.join(dirpath, filename)
                        if self.manifest_file_allowed(path):
                            yield os.path.join(relative_dirpath, filename)
