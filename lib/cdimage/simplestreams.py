# -*- coding: utf-8 -*-

# Copyright (C) 2021 Canonical Ltd.
# Author: Łukasz 'sil2100' Zemczak <lukasz.zemczak@ubuntu.com>

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

"""Generation of simplestreams."""

import os
import re
import time
import hashlib
import simplestreams.generate_simplestreams as generate_simplestreams

from cdimage.config import Series
from cdimage.sign import sign_cdimage
from cdimage.checksums import ChecksumFile
from cdimage.tree import (DailyTreePublisher, FullReleasePublisher,
                          SimpleReleasePublisher, projects)


def timestamp(ts=None):
    """Helper function used for generating the simplestreams timestamp."""
    return time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(ts))


class SimpleStreams:
    """Base class for simplestreams generation. Not to be used directly."""

    @staticmethod
    def get_simplestreams(config, publisher):
        """Static function to easily get the right simplestream handler."""
        if isinstance(publisher, DailyTreePublisher):
            cls = DailySimpleStreams
        elif isinstance(publisher, FullReleasePublisher):
            cls = FullReleaseSimpleStreams
        elif isinstance(publisher, SimpleReleasePublisher):
            cls = SimpleReleaseSimpleStreams
        else:
            raise Exception("Unrecognised publisher for simplestreams")
        return cls(config)

    @staticmethod
    def get_simplestreams_by_name(config, type_name):
        """Static helper to get the right simplestream handler for type."""
        type_map = {
            'daily': DailySimpleStreams,
            'release': FullReleaseSimpleStreams,
            'official': SimpleReleaseSimpleStreams,
        }
        if type_name not in type_map:
            raise Exception("Unrecognised publisher for simplestreams")
        return type_map[type_name](config)

    def __init__(self, config):
        self.tree_dir = config.root
        self.streams_dir = self.tree_dir
        self.config = config
        self.content_id = "com.ubuntu.cdimage.base"
        self.setup()

    def setup(self):
        """Resets the SimpleStream object state."""
        self.cdimage_items = []
        self.cdimage_products = {}

    def prepare_product_info(self, product_name, project, series, image_type,
                             arch):
        """Prepares and stores detailed info about published products."""
        if product_name in self.cdimage_products:
            return
        product_info = {
            "arch": arch,
            "os": project,
            "release": series.name,
            "release_codename": series.displayname,
            "release_title": series.displayversion(project),
            "image_type": image_type,
            "version": series.version
            }
        # TODO: Add support_eol, aliases etc.
        self.cdimage_products[product_name] = product_info

    def extract_arch(self, item):
        """Return the arch string from a published item name.

        This can be overriden in derived classes, if needed.
        """
        match = re.match(r".*-([\+\w]+)(\.[\w]+){1,2}", item)
        return match.group(1) if match else None

    def extract_release_image_type(self, item, project="", arch=""):
        """Return an image_type, when needed to derive it from the filename.

        This basically only works on filenames from releases trees.
        """
        # XXX: Special case - ubuntu-server released images start with
        #  ubuntu-, not ubuntu-server-.
        if project == "ubuntu-server":
            project = "ubuntu"
        match = re.match(
            re.escape(project) + r"-[0-9\.]+-([\w-]+)-" + re.escape(arch),
            item)
        return match.group(1) if match else None

    def extract_release_project(self, item):
        """Return a project name, when needed to derive it from the filename.

        This basically only works on filenames from releases trees.
        """
        match = re.match(r"(.*)-[0-9]+(\.[0-9]+)*-", item)
        if not match:
            return None
        project = match.group(1)
        # Now some special-casing for ubuntu-server
        if project == "ubuntu" and "server" in item:
            project = "ubuntu-server"
        return project

    def extract_release_identifier(self, item, series):
        """Return a version number, deducting it from the filename.

        This basically only works on filenames from releases trees. If we
        cannot safely determine it from the filename, we return the series
        version.
        """
        match = re.match(r".*-([0-9]+(\.[0-9]+)*)-", item)
        if not match:
            return series.realversion
        return match.group(1)

    def scan_published_item(self, publishing_dir, sha256sums, file):
        """Scan and generate simplestream data for a published file."""
        for extension in ("iso", "img", "img.xz", "manifest", "list",
                          "iso.zsync", "img.zsync", "img.xz.zsync"):
            if file.endswith(extension):
                break
        else:
            return None
        full_path = os.path.join(publishing_dir, file)
        data = {}
        # Checksum
        # One of the image files, we can fetch the checksum from SHA256SUMS,
        # if it's available.
        data["sha256"] = sha256sums.entries.get(file)
        if data["sha256"] is None:
            data["sha256"] = sha256sums.checksum(full_path)
        # Size
        try:
            data["size"] = os.stat(full_path).st_size
        except OSError:
            # TODO: possibly actually error out
            return None
        # Relative stream path
        data["path"] = full_path[len(self.tree_dir) + 1:]
        # The file type
        data["ftype"] = extension
        # Add the given image to the list of stream contents
        return data

    def scan_target(self, target_dir, series, project, image_type, identifier):
        """Scan a published directory, recording all files of interest."""
        sha256sums = ChecksumFile(
            self.config, target_dir, "SHA256SUMS", hashlib.sha256)
        sha256sums.read()
        # Now let's convert those into pre-sstream items.
        item_project = project
        item_image_type = image_type
        # Look for supported published items.
        for file in os.listdir(target_dir):
            data = self.scan_published_item(
                target_dir, sha256sums, file)
            if not data:
                continue
            arch = self.extract_arch(file)
            if not project:
                # In case we couldn't guess the project before, let's infer
                # it from the filename.
                item_project = self.extract_release_project(file)
            if not image_type:
                # In case we couldn't guess the image type before, let's infer
                # it from the filename as well.
                item_image_type = self.extract_release_image_type(
                    file, item_project, arch)
            if not identifier:
                version_name = self.extract_release_identifier(file, series)
            else:
                version_name = identifier
            content_id = "%s:%s" % (self.content_id, item_project)
            product_name = '%s:%s:%s:%s' % (content_id, item_image_type,
                                            series.version, arch)
            self.cdimage_items.append(
                (content_id, product_name, version_name,
                 data['ftype'], data, ))
            self.prepare_product_info(product_name, item_project,
                                      series, item_image_type, arch)

    def scan_tree(self):
        """Base function called by generate() to scan a cdimage tree type."""
        raise NotImplementedError(
            "The scan_tree() method needs to be implemented.")

    def generate(self, sign=True):
        """Core function to generate simplestream data for the cdimage tree.

        This is the only function that needs to be called to get simplestreams
        generated for a selected cdimage tree and publish type. As a result.
        simplestream file structure is generated at the rightful place, ready
        for consumption.
        """

        # Types:
        # daily: project -> [series] -> image_type -> datestamp -> image
        # release: [project] -> 'releases' -> series -> 'release' -> image
        # simple: series -> image
        self.scan_tree()

        metadata = {"updated": timestamp(), "datatype": "image-downloads"}
        trees = generate_simplestreams.items2content_trees(
            self.cdimage_items, metadata)
        # Now we supplement that with additional product metadata that we
        # gathered when traversing through the cdimage tree.
        for content_id, content in trees.items():
            for product_id, product in content["products"].items():
                if product_id in self.cdimage_products:
                    product.update(self.cdimage_products[product_id])

        filenames = generate_simplestreams.write_streams(
            self.streams_dir, trees, metadata)
        if sign:
            for file in filenames:
                sign_cdimage(self.config, file)


class DailySimpleStreams(SimpleStreams):
    """Class for generating simplestreams for cdimage daily images."""

    def __init__(self, config):
        super(DailySimpleStreams, self).__init__(config)
        self.content_id = "com.ubuntu.cdimage.daily"
        self.tree_dir = self.streams_dir = os.path.join(
            self.config.root, "www", "full", self.config.subtree).rstrip("/")
        self.publish_id_re = re.compile(r'^[0-9]{8}(\.[0-9]+)?$')

    def scan_daily_project(self, base_dir, project, series=None):
        """Helper function for recursive daily tree scanning."""
        if not series:
            series = Series.latest()
        for entry in os.listdir(base_dir):
            try:
                check_series = Series.find_by_name(entry)
                # If we're here, it means we found a per-series directory.
                # We need to parse it recursively.
                self.scan_daily_project(os.path.join(base_dir, entry), project,
                                        check_series)
                continue
            except ValueError:
                # This is actually the expected outcome
                pass
            # This means it's an image type, not a series - so let's continue
            image_type = entry
            image_type_dir = os.path.join(base_dir, image_type)
            if not os.path.isdir(image_type_dir):
                continue
            # Optional check for subtrees, not to recursively scan those
            if os.path.exists(os.path.join(image_type_dir, ".is_subtree")):
                continue
            for publish_id in os.listdir(image_type_dir):
                # XXX: Should we also list 'current' and 'pending'? Is there
                #  any use in doing that? For now we don't.
                if not self.publish_id_re.match(publish_id):
                    continue
                target_dir = os.path.join(image_type_dir, publish_id)
                self.scan_target(target_dir, series, project, image_type,
                                 publish_id)

    def scan_tree(self):
        """Scan the dailies image tree."""
        for project in os.listdir(self.tree_dir):
            # Check if the given directory is a project we know.
            if project not in projects:
                continue
            # We also skip Ubuntu as we handle it in a separate step, as it's
            # actually hosted in the root directory (ubuntu is just a symlink).
            if project == "ubuntu":
                continue
            project_dir = os.path.join(self.tree_dir, project)
            # Inside the project directory we can have either image types or
            # series.
            self.scan_daily_project(project_dir, project)
        # Now, handle Ubuntu from the tree root directory.
        self.scan_daily_project(self.tree_dir, "ubuntu")


class FullReleaseSimpleStreams(SimpleStreams):
    """Class for generating simplestreams for cdimage releases/ images."""

    def __init__(self, config):
        super(FullReleaseSimpleStreams, self).__init__(config)
        self.content_id = "com.ubuntu.cdimage"
        self.tree_dir = os.path.join(self.config.root, "www", "full")
        self.streams_dir = os.path.join(self.tree_dir, "releases")

    def scan_releases_project(self, base_dir, project):
        """Helper function to scan a project releases/ directory."""
        releases_dir = os.path.join(base_dir, "releases")
        for entry in os.listdir(releases_dir):
            try:
                series = Series.find_by_name(entry)
            except ValueError:
                # Unrecognized series directory in the releases tree.
                # TODO: let's log this and continue
                continue
            target_dir = os.path.join(releases_dir, entry, "release")
            # The image ID is the point version for now, since those should
            # be unique. This might still be up for discussion.
            self.scan_target(target_dir, series, project, None, None)

    def scan_tree(self):
        """Scan all releases/ directories on cdimage."""
        for entry in os.listdir(self.tree_dir):
            # Check if the given directory is a project we know.
            if entry not in projects:
                continue
            # We also skip Ubuntu Server, since releases of the server flavor
            # are batched up with regular Ubuntu.
            if entry == "ubuntu-server":
                continue
            project_dir = os.path.join(self.tree_dir, entry)
            # Inside the project directory we can have either image types or
            # series.
            self.scan_releases_project(project_dir, entry)
        # The releases/ directory is also basically the release tree for the
        # main Ubuntu flavor
        # XXX: I think this is not needed, due to the fact that we have the
        #  ubuntu/ directory with some of the needed symlinks.
        # self.scan_releases_project(self.tree_dir, None)


class SimpleReleaseSimpleStreams(SimpleStreams):
    """Class for generating simplestreams for releases.ubuntu.com."""

    def __init__(self, config):
        super(SimpleReleaseSimpleStreams, self).__init__(config)
        self.content_id = "com.ubuntu.releases"
        self.tree_dir = self.streams_dir = os.path.join(
            self.config.root, "www", "simple")

    def scan_tree(self):
        """Scan the releases.ubuntu.com (simple) tree."""
        for entry in os.listdir(self.tree_dir):
            try:
                series = Series.find_by_name(entry)
            except ValueError:
                # Unrecognized series directory in the releases tree.
                # TODO: let's log this and continue
                continue
            target_dir = os.path.join(self.tree_dir, entry)
            self.scan_target(target_dir, series, None, None, None)
