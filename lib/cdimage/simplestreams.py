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
from cdimage.tree import (
    DailyTreePublisher,
    FullReleasePublisher,
    SimpleReleasePublisher,
    projects,
)


def timestamp(ts=None):
    """Helper function used for generating the simplestreams timestamp."""
    return time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime(ts))


class SimpleStreams:
    """Base class for simplestreams generation. Not to be used directly."""

    @staticmethod
    def get_simplestreams(config, publisher):
        """Static function to easily get the right simplestream handler."""
        if isinstance(publisher, DailyTreePublisher):
            if config.project == "ubuntu-core":
                cls = CoreSimpleStreams
            else:
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
            "daily": DailySimpleStreams,
            "release": FullReleaseSimpleStreams,
            "official": SimpleReleaseSimpleStreams,
            "core": CoreSimpleStreams,
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

    def get_series_name(self, series):
        """Get the series name for the given series object."""
        return series.name

    def get_series_version(self, series):
        """Get the series version for the given series object."""
        return series.version

    def get_series_displayname(self, series):
        """Get the series real version for the given series object."""
        return series.displayname

    def get_series_displayversion(self, series, project, image_type=None):
        """Get the series display name for the given series object."""
        return series.displayversion(project)

    def get_aliases(self, series, project, image_type):
        """Get aliases for the given series object."""
        return ""

    def prepare_product_info(self, product_name, project, series, image_type, arch):
        """Prepares and stores detailed info about published products."""
        if product_name in self.cdimage_products:
            return
        product_info = {
            "arch": arch,
            "os": project,
            "release": self.get_series_name(series),
            "release_codename": self.get_series_displayname(series),
            "release_title": self.get_series_displayversion(
                series, project, image_type
            ),
            "image_type": image_type,
            "version": self.get_series_version(series),
            "aliases": self.get_aliases(series, project, image_type),
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
            re.escape(project) + r"-[0-9\.]+-([\w-]+)-" + re.escape(arch), item
        )
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
        for extension in (
            "iso",
            "img",
            "img.xz",
            "manifest",
            "list",
            "iso.zsync",
            "img.zsync",
            "img.xz.zsync",
            "lxd.tar.xz",
            ".qcow2",
        ):
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
        data["path"] = full_path[len(self.tree_dir) + 1 :]
        # The file type
        data["ftype"] = extension
        # A special case for the lxd tarballs
        if extension == "lxd.tar.xz":
            # Let's find the .qcow2 (disk1.img) file corresponding to the
            # tarball and fetch its checksum.
            img_file = file.replace(extension, "qcow2")
            disk1_sum = sha256sums.entries.get(img_file)
            if disk1_sum is None:
                img_path = os.path.join(publishing_dir, img_file)
                if os.path.exists(img_path):
                    disk1_sum = sha256sums.checksum(img_path)
            if disk1_sum is not None:
                data["combined_disk1-img_sha256"] = disk1_sum
        elif extension == ".qcow2":
            # This is a special case for lxd purposes. LXD expects a qcow2
            # image as the disk1.img ftype.
            data["ftype"] = "disk1.img"
        # Add the given image to the list of stream contents
        return data

    def scan_target(self, target_dir, series, project, image_type, identifier):
        """Scan a published directory, recording all files of interest."""
        # For debugging, print out all method arguments
        sha256sums = ChecksumFile(self.config, target_dir, "SHA256SUMS", hashlib.sha256)
        sha256sums.read()
        # Now let's convert those into pre-sstream items.
        item_project = project
        item_image_type = image_type
        # Look for supported published items.
        for file in os.listdir(target_dir):
            data = self.scan_published_item(target_dir, sha256sums, file)
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
                    file, item_project, arch
                )
            if not identifier:
                version_name = self.extract_release_identifier(file, series)
            else:
                version_name = identifier
            content_id = "%s:%s" % (self.content_id, item_project)
            product_name = "%s:%s:%s:%s" % (
                content_id,
                item_image_type,
                self.get_series_version(series),
                arch,
            )
            self.cdimage_items.append(
                (
                    content_id,
                    product_name,
                    version_name,
                    data["ftype"],
                    data,
                )
            )
            self.prepare_product_info(
                product_name, item_project, series, item_image_type, arch
            )

    def scan_tree(self):
        """Base function called by generate() to scan a cdimage tree type."""
        raise NotImplementedError("The scan_tree() method needs to be implemented.")

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
        # core: series -> channel -> datestamp -> image
        self.scan_tree()

        updated = timestamp()
        metadata = {"updated": updated, "datatype": "image-downloads"}
        trees = generate_simplestreams.items2content_trees(self.cdimage_items, metadata)
        # Now we supplement that with additional product metadata that we
        # gathered when traversing through the cdimage tree.
        for content_id, content in trees.items():
            for product_id, product in content["products"].items():
                if product_id in self.cdimage_products:
                    product.update(self.cdimage_products[product_id])

        filenames = generate_simplestreams.write_streams(
            self.streams_dir, trees, updated
        )
        if sign:
            for file in filenames:
                sign_cdimage(self.config, file)


class DailySimpleStreams(SimpleStreams):
    """Class for generating simplestreams for cdimage daily images."""

    def __init__(self, config):
        super(DailySimpleStreams, self).__init__(config)
        self.content_id = "com.ubuntu.cdimage.daily"
        self.tree_dir = self.streams_dir = os.path.join(
            self.config.root, "www", "full", self.config.subtree
        ).rstrip("/")
        self.publish_id_re = re.compile(r"^[0-9]{8}(\.[0-9]+)?$")

    def scan_daily_project(self, base_dir, project, series=None):
        """Helper function for recursive daily tree scanning."""
        if not series:
            series = Series.latest()
        for entry in os.listdir(base_dir):
            try:
                check_series = Series.find_by_name(entry)
                # If we're here, it means we found a per-series directory.
                # We need to parse it recursively.
                self.scan_daily_project(
                    os.path.join(base_dir, entry), project, check_series
                )
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
                self.scan_target(target_dir, series, project, image_type, publish_id)

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

    def get_aliases(self, series, project, image_type):
        """Get aliases for the given series object."""
        return ",".join([series.version, series.name])

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
            self.config.root, "www", "simple"
        )

    def get_aliases(self, series, project, image_type):
        """Get aliases for the given series object."""
        return ",".join([series.version, series.name])

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


class CoreSimpleStreams(SimpleStreams):
    """Class for generating simplestreams for cdimage ubuntu-core images."""

    def __init__(self, config):
        super(CoreSimpleStreams, self).__init__(config)
        self.content_id = "com.ubuntu.cdimage"
        self.tree_dir = self.streams_dir = os.path.join(
            self.config.root, "www", "full", self.config.subtree, "ubuntu-core"
        ).rstrip("/")
        # At least for core, for now, we want to include the 'current'
        # images as well, since those are basically the 'release' ones.
        self.publish_id_re = re.compile(r"(^[0-9]{8}(\.[0-9]+)?$)|(^current$)")

    def get_series_name(self, series):
        """Get the series name for the core series object."""
        return series.core_series

    def get_series_version(self, series):
        """Get the series version for the core series object."""
        return series.core_series

    def get_series_displayversion(self, series, project, image_type=None):
        """Get the series real version for the core series object."""
        return "%s %s" % (series.core_series, image_type)

    def get_series_displayname(self, series):
        """Get the series display name for the core series object."""
        return series.core_series

    def get_aliases(self, series, project, image_type):
        """Get aliases for the given series object."""
        aliases = []
        if image_type in ("stable", "dangerous-stable"):
            aliases.append(series.core_series)
        if Series.latest_core() == series:
            aliases.append("default")
        return ",".join(aliases)

    def scan_tree(self):
        """Scan the dailies image tree."""
        for series_name in os.listdir(self.tree_dir):
            try:
                series = Series.find_by_core_series(series_name)
            except ValueError:
                # Unrecognized series directory in the series tree, ignore
                continue
            # Now look through all the channels
            series_dir = os.path.join(self.tree_dir, series_name)
            if not os.path.isdir(series_dir):
                continue
            for channel in os.listdir(series_dir):
                channel_dir = os.path.join(series_dir, channel)
                if not os.path.isdir(channel_dir):
                    continue
                for publish_id in os.listdir(channel_dir):
                    if not self.publish_id_re.match(publish_id):
                        continue
                    target_dir = os.path.join(channel_dir, publish_id)
                    if not os.path.isdir(target_dir):
                        continue
                    self.scan_target(
                        target_dir, series, "ubuntu-core", channel, publish_id
                    )
