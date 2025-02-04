#! /usr/bin/python

# Copyright (C) 2012, 2013 Canonical Ltd.
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

"""Unit tests for cdimage.tree."""

from __future__ import print_function

from functools import wraps
try:
    from html.parser import HTMLParser
except ImportError:
    from HTMLParser import HTMLParser
import io
import os
import shutil
import sys
from textwrap import dedent
import tarfile
import traceback

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage import osextras
from cdimage.config import Config, Series
from cdimage.tests.helpers import TestCase, date_to_time, mkfile, touch
from cdimage.tree import (
    DailyTree,
    DailyTreePublisher,
    FullReleasePublisher,
    FullReleaseTree,
    Link,
    Paragraph,
    Publisher,
    SimpleReleasePublisher,
    SimpleReleaseTree,
    Span,
    TorrentTree,
    Tree,
    UnorderedList,
)

__metaclass__ = type


class TestTree(TestCase):
    def setUp(self):
        super(TestTree, self).setUp()
        self.use_temp_dir()
        self.config = Config(read=False)
        self.tree = Tree(self.config, self.temp_dir)

    def test_get_daily(self):
        tree = Tree.get_daily(self.config, self.temp_dir)
        self.assertIsInstance(tree, DailyTree)
        self.assertEqual(self.config, tree.config)
        self.assertEqual(self.temp_dir, tree.directory)

    def test_get_release(self):
        for official, cls in (
            ("yes", SimpleReleaseTree),
            ("poolonly", SimpleReleaseTree),
            ("named", FullReleaseTree),
            ("no", FullReleaseTree),
        ):
            tree = Tree.get_release(self.config, official, self.temp_dir)
            self.assertIsInstance(tree, cls)
            self.assertEqual(self.config, tree.config)
            self.assertEqual(self.temp_dir, tree.directory)
        self.assertRaisesRegex(
            Exception, r"Unrecognised OFFICIAL setting: 'x'",
            Tree.get_release, self.config, "x")

    def test_get_for_directory(self):
        self.config.root = self.temp_dir
        path = os.path.join(self.temp_dir, "www", "full", "foo")
        os.makedirs(path)
        for status, cls in (
            ("daily", DailyTree),
            ("release", FullReleaseTree),
        ):
            tree = Tree.get_for_directory(self.config, path, status)
            self.assertIsInstance(tree, cls)
            self.assertEqual(
                os.path.join(self.temp_dir, "www", "full"), tree.directory)
        tree = Tree.get_for_directory(self.config, self.temp_dir, "daily")
        self.assertIsInstance(tree, Tree)
        self.assertEqual("/", tree.directory)

    def test_path_to_project(self):
        self.assertEqual("kubuntu", self.tree.path_to_project("kubuntu/foo"))
        self.assertEqual("ubuntu", self.tree.path_to_project("foo"))
        self.assertEqual("ubuntu", self.tree.path_to_project("ubuntu/foo/bar"))

    def test_project_base(self):
        self.config.root = self.temp_dir
        self.config["PROJECT"] = "ubuntu"
        self.assertEqual(self.temp_dir, self.tree.project_base)
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            os.path.join(self.temp_dir, "kubuntu"), self.tree.project_base)

    def test_manifest_file_allowed_passes_good_extensions(self):
        paths = [
            os.path.join(self.temp_dir, name)
            for name in (
                "foo.iso", "foo.img", "foo.img.gz",
                "foo.tar.gz", "foo.tar.xz",
            )]
        for path in paths:
            touch(path)
            self.assertTrue(self.tree.manifest_file_allowed(path))

    def test_manifest_file_allowed_fails_bad_extensions(self):
        paths = [
            os.path.join(self.temp_dir, name)
            for name in ("foo.txt", "foo")]
        for path in paths:
            touch(path)
            self.assertFalse(self.tree.manifest_file_allowed(path))

    def test_manifest_file_allowed_fails_directories(self):
        path = os.path.join(self.temp_dir, "dir.iso")
        os.mkdir(path)
        self.assertFalse(self.tree.manifest_file_allowed(path))

    @mock.patch("time.strftime", return_value="2013-03-21 00:00:00")
    @mock.patch("cdimage.tree.trigger_mirrors")
    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_trigger(self, mock_polish_directory,
                                  mock_trigger_mirrors, *args):
        self.config.root = self.temp_dir
        publish_base = os.path.join(self.temp_dir, "www", "full", "daily-live")
        target_dir = os.path.join(publish_base, "20130321")
        series = Series.latest().name
        for name in (
            "%s-desktop-amd64.iso" % series,
            "%s-desktop-amd64.manifest" % series,
        ):
            touch(os.path.join(target_dir, name))
        current_triggers_path = os.path.join(
            self.temp_dir, "production", "current-triggers")
        with mkfile(current_triggers_path) as current_triggers:
            print(
                "ubuntu\tdaily-live\tbionic-\tamd64", file=current_triggers)
        self.config["SSH_ORIGINAL_COMMAND"] = (
            "mark-current --project=ubuntu --series=%s --publish-type=desktop "
            "--architecture=amd64 20130321" % series)
        pid = os.fork()
        if pid == 0:  # child
            try:
                Tree.mark_current_trigger(self.config, quiet=True)
                self.assertEqual(0, mock_polish_directory.call_count)
                mock_trigger_mirrors.assert_called_once_with(self.config)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                os._exit(1)
            os._exit(0)
        else:  # parent
            self.wait_for_pid(pid, 0)
            log_path = os.path.join(self.temp_dir, "log", "mark-current.log")
            with open(log_path) as log:
                self.assertEqual(
                    "[2013-03-21 00:00:00] %s\n" %
                    self.config["SSH_ORIGINAL_COMMAND"],
                    log.read())

            with open(os.path.join(publish_base, "20130321", ".marked_good"),
                      "r") as marked_good:
                self.assertEqual("plucky-desktop-amd64.iso\n",
                                 marked_good.read())

            publish_current = os.path.join(publish_base, "current")
            self.assertTrue(os.path.islink(publish_current))
            self.assertEqual("20130321", os.readlink(publish_current))


class TestTags(TestCase):
    def test_paragraph(self):
        tag = Paragraph(["Sentence one.", "Sentence two."])
        self.assertEqual("<p>Sentence one.  Sentence two.</p>", str(tag))

    def test_unordered_list(self):
        tag = UnorderedList(["one", "two"])
        self.assertEqual("<ul>\n<li>one</li>\n<li>two</li>\n</ul>", str(tag))

    def test_span(self):
        tag = Span("urgent", ["Sentence one.", "Sentence two."])
        self.assertEqual(
            "<span class=\"urgent\">Sentence one.  Sentence two.</span>",
            str(tag))

    def test_link(self):
        tag = Link("http://www.example.org/", "Example")
        self.assertEqual(
            "<a href=\"http://www.example.org/\">Example</a>", str(tag))
        tag = Link("http://www.example.org/", "Example", show_class=True)
        self.assertEqual(
            "<a class=\"http\" href=\"http://www.example.org/\">Example</a>",
            str(tag))


class TestPublisher(TestCase):
    def setUp(self):
        super(TestPublisher, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()

    def test_get_daily(self):
        tree = Tree.get_daily(self.config)
        publisher = Publisher.get_daily(tree, "daily")
        self.assertIsInstance(publisher, DailyTreePublisher)
        self.assertEqual(tree, publisher.tree)
        self.assertEqual("daily", publisher.image_type)

    def test_publish_type(self):
        for image_type, project, dist, publish_type in (
            ("daily-preinstalled", "ubuntu-server", "bionic",
             "preinstalled-server"),
            ("daily-preinstalled", "ubuntu", "bionic",
             "preinstalled-desktop"),
            ("daily-live", "edubuntu", "lunar", "desktop"),
            ("daily-live", "ubuntu-server", "bionic", "live-server"),
            ("daily-live", "ubuntu", "bionic", "desktop"),
            ("daily-live", "ubuntu-core", "bionic", "live-core"),
            ("daily-live", "ubuntu-core-desktop", "mantic",
             "live-core-desktop"),
            ("daily-live", "ubuntu-core-installer", "noble",
             "ubuntu-core-installer"),
            ("daily", "ubuntu-base", "bionic", "base"),
            ("daily", "ubuntu-server", "bionic", "server"),
            ("daily", "ubuntu-server", "focal", "legacy-server"),
            ("daily", "ubuntu", "bionic", "alternate"),
            ("daily-canary", "ubuntu", "jammy", "desktop-canary"),
            ("daily-legacy", "ubuntu", "lunar", "desktop-legacy"),
            ("daily-minimal", "xubuntu", "lunar", "minimal"),
        ):
            self.config["PROJECT"] = project
            self.config["DIST"] = dist
            tree = Tree(self.config, self.temp_dir)
            publisher = Publisher(tree, image_type)
            self.assertEqual(publish_type, publisher.publish_type)
            if "_" not in image_type:
                self.assertEqual(
                    image_type, Publisher._guess_image_type(publish_type))


class TestPublisherWebIndices(TestCase):
    """Test Publisher.make_web_indices and its subsidiary methods."""

    def setUp(self):
        super(TestPublisherWebIndices, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.directory = os.path.join(
            self.config.root, "www", "full", "daily", "20130326")
        os.makedirs(self.directory)
        self.tree = Tree.get_for_directory(
            self.config, self.directory, "daily")

    def test_titlecase(self):
        publisher = Publisher(self.tree, "daily-live")
        self.assertEqual("Desktop image", publisher.titlecase("desktop image"))

    def test_cssincludes(self):
        assets = 'https://assets.ubuntu.com/v1'
        vanilla = assets + "/vanilla-framework-version-1.8.0.min.css"
        for project, expected in (
            ("ubuntu", [vanilla]),
            ("kubuntu",
             [vanilla, "//cdimage.ubuntu.com/include/kubuntu/style.css"]),
            ("lubuntu",
             [vanilla, "//cdimage.ubuntu.com/include/lubuntu/style.css"]),
            ("lubuntu-next",
             [vanilla, "//cdimage.ubuntu.com/include/lubuntu/style.css"]),
            ("xubuntu",
             [assets + "/vanilla-framework-version-1.8.0.min.css",
              "//cdimage.ubuntu.com/include/xubuntu/style.css"]),
        ):
            self.config["PROJECT"] = project
            publisher = Publisher(self.tree, "daily")
            self.assertEqual(expected, publisher.cssincludes())

    def test_cdtypestr(self):
        self.config["DIST"] = "bionic"
        publisher = Publisher(self.tree, "daily-live")
        self.assertEqual(
            "desktop image", publisher.cdtypestr("desktop", "iso"))

    def test_cdtypedesc_desktop(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "bionic"
        publisher = Publisher(self.tree, "daily-live")
        desc = list(publisher.cdtypedesc("desktop", "iso"))
        self.assertEqual(
            "<p>The desktop image allows you to try Ubuntu without changing "
            "your computer at all, and at your option to install it "
            "permanently later.  This type of image is what most people will "
            "want to use.  You will need at least 1024MiB of RAM to install "
            "from this image.</p>", "\n".join(map(str, desc)))
        desc_second_time = list(publisher.cdtypedesc("desktop", "iso"))
        self.assertEqual(
            "<p>The desktop image allows you to try Ubuntu without changing "
            "your computer at all, and at your option to install it "
            "permanently later.  You will need at least 1024MiB of RAM to "
            "install from this image.</p>",
            "\n".join(map(str, desc_second_time)))

    def test_cdtypedesc_alternate(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "bionic"
        publisher = Publisher(self.tree, "daily")
        desc = list(publisher.cdtypedesc("alternate", "iso"))
        self.assertEqual(
            "<p>The alternate install image allows you to perform certain "
            "specialist installations of Ubuntu.  It provides for the "
            "following situations:</p>\n"
            "<ul>\n"
            "<li>setting up automated deployments;</li>\n"
            "<li>upgrading from older installations without network "
            "access;</li>\n"
            "<li>LVM and/or RAID partitioning;</li>\n"
            "<li>installs on systems with less than about 1024MiB of RAM "
            "(although note that low-memory systems may not be able to run "
            "a full desktop environment reasonably).</li>\n"
            "</ul>\n"
            "<p>In the event that you encounter a bug using the alternate "
            "installer, please file a bug on the <a "
            "href=\"https://bugs.launchpad.net/ubuntu/+source/"
            "debian-installer/+filebug\">debian-installer</a> package.</p>",
            "\n".join(map(str, desc)))

    def test_archdesc(self):
        self.config["ARCHES"] = "amd64 i386"
        self.config["DIST"] = "focal"
        publisher = Publisher(self.tree, "daily-live")
        self.assertEqual(
            "For almost all PCs.  This includes most machines with "
            "Intel/AMD/etc type processors and almost all computers that run "
            "Microsoft Windows, as well as newer Apple Macintosh systems "
            "based on Intel processors.",
            publisher.archdesc("i386", "desktop"))

        self.assertEqual(
            "Choose this if you have a computer based on the AMD64 or EM64T "
            "architecture (e.g., Athlon64, Opteron, EM64T Xeon, Core 2).  If "
            "you have a non-64-bit processor made by AMD, or if you need full "
            "support for 32-bit code, use the i386 images instead.",
            publisher.archdesc("amd64", "desktop"))

        self.config["ARCHES"] = "amd64"
        publisher = Publisher(self.tree, "daily-live")
        self.assertEqual(
            "Choose this if you have a computer based on the AMD64 or EM64T "
            "architecture (e.g., Athlon64, Opteron, EM64T Xeon, Core 2).  "
            "Choose this if you are at all unsure.",
            publisher.archdesc("amd64", "desktop"))

        # Test case for ppc64el series-conditional strings
        self.config["ARCHES"] = "ppc64el"
        publisher = Publisher(self.tree, "daily-live")
        self.assertEqual(
            "For POWER8 and POWER9 Little-Endian systems, especially "
            "the \"LC\" Linux-only servers.",
            publisher.archdesc("ppc64el", "live-server"))

        self.config["DIST"] = "jammy"
        publisher = Publisher(self.tree, "daily-live")
        self.assertEqual(
            "For POWER9 and POWER10 Little-Endian systems.",
            publisher.archdesc("ppc64el", "live-server"))

    def test_maybe_oversized(self):
        self.config["DIST"] = "bionic"
        oversized_path = os.path.join(
            self.directory, "bionic-desktop-i386.OVERSIZED")
        touch(oversized_path)
        publisher = Publisher(self.tree, "daily-live")
        desc = list(publisher.maybe_oversized(
            "daily", oversized_path, "desktop"))
        self.assertEqual(
            "<br>\n"
            "<span class=\"urgent\">Warning: This image is oversized (which "
            "is a bug) and will not fit onto a standard 703MiB CD.  However, "
            "you may still test it using a DVD, a USB drive, or a virtual "
            "machine.</span>",
            "\n".join(map(str, desc)))

    def test_mimetypestr(self):
        publisher = Publisher(self.tree, "daily")
        self.assertIsNone(publisher.mimetypestr("iso"))
        self.assertEqual(
            "application/octet-stream", publisher.mimetypestr("img"))

    def test_extensionstr(self):
        publisher = Publisher(self.tree, "daily")
        self.assertEqual("standard download", publisher.extensionstr("iso"))
        self.assertEqual(
            "<a href=\"https://help.ubuntu.com/community/BitTorrent\">"
            "BitTorrent</a> download",
            publisher.extensionstr("iso.torrent"))

    def test_web_heading(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "dapper"
        publisher = Publisher(self.tree, "daily")
        self.assertEqual(
            "Ubuntu 6.06.2 LTS (Dapper Drake)",
            publisher.web_heading("ubuntu-6.06.2"))
        self.config["DIST"] = "raring"
        self.assertEqual(
            "Ubuntu 13.04 (Raring Ringtail) Daily Build",
            publisher.web_heading("raring"))

    def test_find_images(self):
        for name in (
            "SHA256SUMS",
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.list",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.list",
        ):
            touch(os.path.join(self.directory, name))
        publisher = Publisher(self.tree, "daily-live")
        self.assertCountEqual(
            ["bionic-desktop-amd64.list", "bionic-desktop-i386.list"],
            publisher.find_images(self.directory, "bionic", "desktop"))

    def test_find_source_images(self):
        for name in (
            "SHA256SUMS",
            "bionic-src-1.iso", "bionic-src-2.iso", "bionic-src-3.iso",
        ):
            touch(os.path.join(self.directory, name))
        publisher = Publisher(self.tree, "daily-live")
        self.assertEqual(
            [1, 2, 3], publisher.find_source_images(self.directory, "bionic"))

    def test_find_any_with_extension(self):
        for name in (
            "SHA256SUMS",
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.iso.torrent",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.list",
        ):
            touch(os.path.join(self.directory, name))
        publisher = Publisher(self.tree, "daily-live")
        self.assertTrue(
            publisher.find_any_with_extension(self.directory, "iso"))
        self.assertTrue(
            publisher.find_any_with_extension(self.directory, "iso.torrent"))
        self.assertTrue(
            publisher.find_any_with_extension(self.directory, "list"))
        self.assertFalse(
            publisher.find_any_with_extension(self.directory, "manifest"))

    def test_make_web_indices(self):
        # We don't attempt to test the entire text here; that would be very
        # tedious.  Instead, we simply test that a sample run has no missing
        # substitutions and produces reasonably well-formed HTML.
        # HTMLParser is not very strict about this; we might be better off
        # upgrading to XHTML so that we can use an XML parser.
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "bionic"
        for name in (
            "SHA256SUMS",
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.iso.zsync",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.list",
        ):
            touch(os.path.join(self.directory, name))
        publisher = Publisher(self.tree, "daily-live")
        publisher.make_web_indices(self.directory, "bionic", status="daily")

        self.assertCountEqual([
            "HEADER.html", "FOOTER.html", ".htaccess",
            "SHA256SUMS",
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.iso.zsync",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.list",
        ], os.listdir(self.directory))

        header_path = os.path.join(self.directory, "HEADER.html")
        footer_path = os.path.join(self.directory, "FOOTER.html")
        htaccess_path = os.path.join(self.directory, ".htaccess")
        parser_kwargs = {}
        if sys.version >= "3.4":
            parser_kwargs["convert_charrefs"] = True
        parser = HTMLParser(**parser_kwargs)
        with open(header_path) as header:
            data = header.read()
            self.assertNotIn("%s", data)
            parser.feed(data)
        with open(footer_path) as footer:
            data = footer.read()
            self.assertNotIn("%s", data)
            parser.feed(data)
        parser.close()
        with open(htaccess_path) as htaccess:
            self.assertEqual(
                "AddDescription \"Desktop image for 64-bit PC (AMD64) "
                "computers (<a href=\\\"http://zsync.moria.org.uk/\\\">"
                "zsync</a> metafile)\" bionic-desktop-amd64.iso.zsync\n"
                "AddDescription \"Desktop image for 64-bit PC (AMD64) "
                "computers (standard download)\" bionic-desktop-amd64.iso\n"
                "AddDescription \"Desktop image for 32-bit PC (i386) "
                "computers (standard download)\" bionic-desktop-i386.iso\n"
                "AddDescription \"Desktop image for 32-bit PC (i386) "
                "computers (file listing)\" bionic-desktop-i386.list\n"
                "\n"
                "HeaderName HEADER.html\n"
                "ReadmeName FOOTER.html\n"
                "IndexIgnore .htaccess HEADER.html FOOTER.html "
                "published-ec2-daily.txt published-ec2-release.txt "
                ".*.tar.gz\n"
                "IndexOptions NameWidth=* DescriptionWidth=* "
                "SuppressHTMLPreamble FancyIndexing IconHeight=22 "
                "IconWidth=22 HTMLTable\n"
                "AddIcon ../../cdicons/folder.png ^^DIRECTORY^^\n"
                "AddIcon ../../cdicons/iso.png .iso\n"
                "AddIcon ../../cdicons/img.png .img .img.xz .tar.gz .tar.xz "
                ".wsl\n"
                "AddIcon ../../cdicons/list.png .list .manifest .html .zsync "
                "SHA256SUMS SHA256SUMS.gpg\n"
                "AddIcon ../../cdicons/torrent.png .torrent\n",
                htaccess.read())

    def test_make_web_indices_for_simple_release(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "jammy"
        # We use a custom directory as the testsuite one is for daily,
        # not release
        directory = os.path.join(
            self.config.root, "www", "simple", "jammy")
        os.makedirs(directory)
        for name in (
            "SHA256SUMS",
            "ubuntu-22.04.2-desktop-amd64.iso",
            "ubuntu-22.04.2-desktop-amd64.iso.zsync",
            "ubuntu-22.04.2-desktop-amd64.list",
            "ubuntu-22.04-live-server-amd64.iso",
            "ubuntu-22.04-live-server-amd64.list",
        ):
            touch(os.path.join(directory, name))
        tree = Tree.get_for_directory(self.config, directory, "daily")
        publisher = SimpleReleasePublisher(tree, "daily-live", "yes")
        publisher.make_web_indices(
            directory, "ubuntu-22.04.2", status="release")

        self.assertCountEqual([
            "HEADER.html", "FOOTER.html", ".htaccess",
            "SHA256SUMS",
            "ubuntu-22.04.2-desktop-amd64.iso",
            "ubuntu-22.04.2-desktop-amd64.iso.zsync",
            "ubuntu-22.04.2-desktop-amd64.list",
            "ubuntu-22.04-live-server-amd64.iso",
            "ubuntu-22.04-live-server-amd64.list",
        ], os.listdir(directory))

        header_path = os.path.join(directory, "HEADER.html")
        footer_path = os.path.join(directory, "FOOTER.html")
        htaccess_path = os.path.join(directory, ".htaccess")
        parser_kwargs = {}
        if sys.version >= "3.4":
            parser_kwargs["convert_charrefs"] = True
        parser = HTMLParser(**parser_kwargs)
        with open(header_path) as header:
            data = header.read()
            self.assertNotIn("%s", data)
            parser.feed(data)
        with open(footer_path) as footer:
            data = footer.read()
            self.assertNotIn("%s", data)
            parser.feed(data)
        parser.close()
        with open(htaccess_path) as htaccess:
            self.assertEqual(
                "AddDescription \"Desktop image for 64-bit PC (AMD64) "
                "computers (<a href=\\\"http://zsync.moria.org.uk/\\\">"
                "zsync</a> metafile)\" ubuntu-22.04.2-desktop-amd64.iso."
                "zsync\n"
                "AddDescription \"Desktop image for 64-bit PC (AMD64) "
                "computers (standard download)\" "
                "ubuntu-22.04.2-desktop-amd64.iso\n"
                "AddDescription \"Desktop image for 64-bit PC (AMD64) "
                "computers (file listing)\" "
                "ubuntu-22.04.2-desktop-amd64.list\n"
                "RedirectPermanent "
                "/jammy/ubuntu-22.04-latest-desktop-amd64.iso "
                "/jammy/ubuntu-22.04.2-desktop-amd64.iso\n"
                "AddDescription \"Server install image for 64-bit PC "
                "(AMD64) computers (standard download)\" "
                "ubuntu-22.04-live-server-amd64.iso\n"
                "AddDescription \"Server install image for 64-bit PC "
                "(AMD64) computers (file listing)\" "
                "ubuntu-22.04-live-server-amd64.list\n"
                "RedirectPermanent "
                "/jammy/ubuntu-22.04-latest-live-server-amd64.iso "
                "/jammy/ubuntu-22.04-live-server-amd64.iso\n"
                "\n"
                "HeaderName HEADER.html\n"
                "ReadmeName FOOTER.html\n"
                "IndexIgnore .htaccess HEADER.html FOOTER.html "
                "published-ec2-daily.txt published-ec2-release.txt "
                ".*.tar.gz\n"
                "IndexOptions NameWidth=* DescriptionWidth=* "
                "SuppressHTMLPreamble FancyIndexing IconHeight=22 "
                "IconWidth=22 HTMLTable\n"
                "AddIcon ../cdicons/folder.png ^^DIRECTORY^^\n"
                "AddIcon ../cdicons/iso.png .iso\n"
                "AddIcon ../cdicons/img.png .img .img.xz .tar.gz .tar.xz "
                ".wsl\n"
                "AddIcon ../cdicons/list.png .list .manifest .html .zsync "
                "SHA256SUMS SHA256SUMS.gpg\n"
                "AddIcon ../cdicons/torrent.png .torrent\n",
                htaccess.read())

    def test_make_web_indices_for_full_release(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "jammy"
        # We use a custom directory as the testsuite one is for daily,
        # not release
        directory = os.path.join(
            self.config.root, "www", "full", "releases", "jammy", "release")
        os.makedirs(directory)
        for name in (
            "SHA256SUMS",
            "ubuntu-22.04.2-desktop-amd64.iso",
            "ubuntu-22.04.2-desktop-amd64.iso.zsync",
            "ubuntu-22.04.2-desktop-amd64.list",
            "ubuntu-22.04-live-server-amd64.iso",
            "ubuntu-22.04-live-server-amd64.list",
        ):
            touch(os.path.join(directory, name))
        tree = Tree.get_for_directory(self.config, directory, "daily")
        publisher = FullReleasePublisher(tree, "daily-live", "named")
        publisher.make_web_indices(
            directory, "ubuntu-22.04.2", status="release")

        self.assertCountEqual([
            "HEADER.html", "FOOTER.html", ".htaccess",
            "SHA256SUMS",
            "ubuntu-22.04.2-desktop-amd64.iso",
            "ubuntu-22.04.2-desktop-amd64.iso.zsync",
            "ubuntu-22.04.2-desktop-amd64.list",
            "ubuntu-22.04-live-server-amd64.iso",
            "ubuntu-22.04-live-server-amd64.list",
        ], os.listdir(directory))

        header_path = os.path.join(directory, "HEADER.html")
        footer_path = os.path.join(directory, "FOOTER.html")
        htaccess_path = os.path.join(directory, ".htaccess")
        parser_kwargs = {}
        if sys.version >= "3.4":
            parser_kwargs["convert_charrefs"] = True
        parser = HTMLParser(**parser_kwargs)
        with open(header_path) as header:
            data = header.read()
            self.assertNotIn("%s", data)
            parser.feed(data)
        with open(footer_path) as footer:
            data = footer.read()
            self.assertNotIn("%s", data)
            parser.feed(data)
        parser.close()
        with open(htaccess_path) as htaccess:
            self.assertEqual(
                "AddDescription \"Desktop image for 64-bit PC (AMD64) "
                "computers (<a href=\\\"http://zsync.moria.org.uk/\\\">"
                "zsync</a> metafile)\" "
                "ubuntu-22.04.2-desktop-amd64.iso.zsync\n"
                "AddDescription \"Desktop image for 64-bit PC (AMD64) "
                "computers (standard download)\" "
                "ubuntu-22.04.2-desktop-amd64.iso\n"
                "AddDescription \"Desktop image for 64-bit PC (AMD64) "
                "computers (file listing)\" "
                "ubuntu-22.04.2-desktop-amd64.list\n"
                "RedirectPermanent "
                "/releases/jammy/release/ubuntu-22.04-latest-desktop-amd64.iso"
                " /releases/jammy/release/ubuntu-22.04.2-desktop-amd64.iso\n"
                "AddDescription \"Server install image for 64-bit PC (AMD64) "
                "computers (standard download)\" "
                "ubuntu-22.04-live-server-amd64.iso\n"
                "AddDescription \"Server install image for 64-bit PC (AMD64) "
                "computers (file listing)\" "
                "ubuntu-22.04-live-server-amd64.list\n"
                "RedirectPermanent "
                "/releases/jammy/release/ubuntu-22.04-latest-live-server-"
                "amd64.iso "
                "/releases/jammy/release/ubuntu-22.04-live-server-"
                "amd64.iso\n"
                "\n"
                "HeaderName HEADER.html\n"
                "ReadmeName FOOTER.html\n"
                "IndexIgnore .htaccess HEADER.html FOOTER.html "
                "published-ec2-daily.txt published-ec2-release.txt "
                ".*.tar.gz\n"
                "IndexOptions NameWidth=* DescriptionWidth=* "
                "SuppressHTMLPreamble FancyIndexing IconHeight=22 "
                "IconWidth=22 HTMLTable\n"
                "AddIcon ../../../cdicons/folder.png ^^DIRECTORY^^\n"
                "AddIcon ../../../cdicons/iso.png .iso\n"
                "AddIcon ../../../cdicons/img.png .img .img.xz .tar.gz "
                ".tar.xz .wsl\n"
                "AddIcon ../../../cdicons/list.png .list .manifest .html "
                ".zsync SHA256SUMS SHA256SUMS.gpg\n"
                "AddIcon ../../../cdicons/torrent.png .torrent\n",
                htaccess.read())


class TestDailyTree(TestCase):
    def setUp(self):
        super(TestDailyTree, self).setUp()
        self.use_temp_dir()
        self.config = Config(read=False)
        self.tree = DailyTree(self.config, self.temp_dir)

    def test_default_directory(self):
        self.config.root = self.temp_dir
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "full"),
            DailyTree(self.config).directory)

    def test_name_to_series(self):
        self.assertEqual(
            "warty", self.tree.name_to_series("warty-install-i386.iso"))
        self.assertRaises(ValueError, self.tree.name_to_series, "README")

    def test_site_name(self):
        self.assertEqual("cdimage.ubuntu.com", self.tree.site_name)

    def test_path_to_manifest(self):
        iso = "kubuntu/hoary-install-i386.iso"
        touch(os.path.join(self.temp_dir, iso))
        self.assertEqual(
            "kubuntu\thoary\t/%s\t0" % iso, self.tree.path_to_manifest(iso))

    def test_manifest_files_includes_current(self):
        daily = os.path.join(self.temp_dir, "daily")
        os.makedirs(os.path.join(daily, "20120806"))
        os.symlink("20120806", os.path.join(daily, "current"))
        touch(os.path.join(daily, "20120806", "warty-install-i386.iso"))
        self.assertEqual(
            ["daily/current/warty-install-i386.iso"],
            list(self.tree.manifest_files()))

    def test_manifest(self):
        daily = os.path.join(self.temp_dir, "daily")
        os.makedirs(os.path.join(daily, "20120806"))
        os.symlink("20120806", os.path.join(daily, "current"))
        touch(os.path.join(daily, "20120806", "hoary-install-i386.iso"))
        daily_live = os.path.join(self.temp_dir, "daily-live")
        os.makedirs(os.path.join(daily_live, "20120806"))
        os.symlink("20120806", os.path.join(daily_live, "current"))
        touch(os.path.join(daily_live, "20120806", "hoary-live-i386.iso"))
        self.assertEqual([
            "ubuntu\thoary\t/daily-live/current/hoary-live-i386.iso\t0",
            "ubuntu\thoary\t/daily/current/hoary-install-i386.iso\t0",
        ], self.tree.manifest())


# As well as simply mocking isotracker.ISOTracker, we have to go through
# some contortions to avoid needing ubuntu-archive-tools to be on sys.path
# while running unit tests.

class isotracker_module:
    tracker = None

    class ISOTracker:
        def __init__(self, target):
            isotracker_module.tracker = self
            self.target = target
            self.posted = []

        def post_build(self, product, date, note=""):
            self.posted.append([product, date, note])


def mock_isotracker(target):
    @wraps(target)
    def wrapper(*args, **kwargs):
        original_modules = sys.modules.copy()
        sys.modules["isotracker"] = isotracker_module
        try:
            return target(*args, **kwargs)
        finally:
            if "isotracker" in original_modules:
                sys.modules["isotracker"] = original_modules["isotracker"]
            else:
                del sys.modules["isotracker"]

    return wrapper


class TestDailyTreePublisher(TestCase):
    def setUp(self):
        super(TestDailyTreePublisher, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.config.subtree = ""
        self.config["DIST"] = Series.latest()

        # Can probably be done in a cleaner way
        if os.path.exists("etc/qa-products"):
            osextras.ensuredir(os.path.join(self.config.root, "etc"))
            product_list = os.path.join(self.config.root, "etc", "qa-products")
            shutil.copy("etc/qa-products", product_list)

    def make_publisher(self, project, image_type, **kwargs):
        self.config["PROJECT"] = project
        self.tree = DailyTree(self.config)
        osextras.ensuredir(self.tree.project_base)
        publisher = DailyTreePublisher(self.tree, image_type, **kwargs)
        osextras.ensuredir(publisher.image_output("i386"))
        osextras.ensuredir(publisher.britney_report)

        return publisher

    def test_image_output(self):
        self.config["DIST"] = "hoary"
        self.assertEqual(
            os.path.join(
                self.config.root, "scratch", "kubuntu", "hoary", "daily",
                "debian-cd", "i386"),
            self.make_publisher("kubuntu", "daily").image_output("i386"))

    def test_source_extension(self):
        self.assertEqual(
            "raw", self.make_publisher("ubuntu", "daily").source_extension)

    def test_britney_report(self):
        self.assertEqual(
            os.path.join(
                self.config.root, "britney", "report", "kubuntu", "daily"),
            self.make_publisher("kubuntu", "daily").britney_report)

    def test_image_type_dir(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        self.assertEqual("daily-live", publisher.image_type_dir)
        self.config["DIST"] = "hoary"
        self.assertEqual(
            os.path.join("hoary", "daily-live"), publisher.image_type_dir)

    def test_publish_base(self):
        self.assertEqual(
            os.path.join(
                self.config.root, "www", "full",
                "ubuntu-server", "daily-preinstalled"),
            self.make_publisher("ubuntu-server",
                                "daily-preinstalled").publish_base)
        self.assertEqual(
            os.path.join(
                self.config.root, "www", "full", "ubuntu-core",
                self.config.core_series, "edge"),
            self.make_publisher("ubuntu-core", "daily-live").publish_base)
        self.config["CHANNEL"] = 'stable'
        self.assertEqual(
            os.path.join(
                self.config.root, "www", "full", "ubuntu-core",
                self.config.core_series, "stable"),
            self.make_publisher("ubuntu-core", "daily-live").publish_base)
        self.assertEqual(
            os.path.join(
                self.config.root, "www", "full", "ubuntu-core-desktop",
                self.config.core_series, "stable"),
            self.make_publisher("ubuntu-core-desktop",
                                "daily-live").publish_base)
        self.assertEqual(
            os.path.join(
                self.config.root, "www", "full", "ubuntu-core-installer",
                "daily-live"),
            self.make_publisher(
                "ubuntu-core-installer", "daily-live").publish_base)
        self.assertEqual(
            os.path.join(
                self.config.root, "www", "full", "kubuntu", "daily-live"),
            self.make_publisher("kubuntu", "daily-live").publish_base)
        self.config["DIST"] = "hoary"
        self.assertEqual(
            os.path.join(
                self.config.root, "www", "full",
                "kubuntu", "hoary", "daily-live"),
            self.make_publisher("kubuntu", "daily-live").publish_base)

    def test_size_limit(self):
        for project, dist, image_type, arch, size_limit in (
            ("ubuntustudio", None, "daily-live", "amd64", 6800000000),
            ("ubuntustudio", "noble", "daily-live", "amd64", 7700000000),
            ("kubuntu", "jammy", "daily-live", "amd64", 4600000000),
            ("kubuntu", "oracular", "daily-live", "amd64", 4700000000),
            ("ubuntu", "noble", "daily-live", "amd64", 6400000000),
            ("ubuntu", "plucky", "daily-live", "amd64", 6100000000),
            ("ubuntukylin", "jammy", "daily-live", "amd64", 4294967296),
            ("ubuntukylin", "noble", "daily-live", "amd64", 5500000000),
            ("xubuntu", "bionic", "daily-live", "amd64", 2000000000),
            ("xubuntu", "focal", "daily-live", "amd64", 2000000000),
            ("xubuntu", "jammy", "daily-live", "amd64", 3000000000),
            ("xubuntu", "noble", "daily-live", "amd64", 4300000000),
            ("ubuntu-budgie", "bionic", "daily-live", "amd64", 2000000000),
            ("ubuntu-budgie", "focal", "daily-live", "amd64", 4294967296),
            ("ubuntu-budgie", "jammy", "daily-live", "amd64", 4294967296),
            ("ubuntu-mate", "focal", "daily-live", "amd64", 4000000000),
            ("ubuntu-mate", "jammy", "daily-live", "amd64", 4000000000),
            ("ubuntu-mate", "noble", "daily-live", "amd64", 5000000000),
            ("ubuntu-server", "bionic", "daily", "amd64", 1200000000),
            ("ubuntu-server", "focal", "daily", "amd64", 1500000000),
            ("ubuntu-server", "jammy", "daily", "amd64", 3300000000),
            ("ubuntu-server", "jammy", "daily", "ppc64el", 3300000000),
            ("ubuntu-server", "noble", "daily", "riscv64", 3600000000),
            ("ubuntucinnamon", "noble", "daily", "amd64", 5500000000),
        ):
            if dist is not None:
                self.config["DIST"] = dist
            publisher = self.make_publisher(project, image_type)
            self.assertEqual(size_limit, publisher.size_limit(arch),
                             "%s/%s" % (project, dist))

    def test_size_limit_extension(self):
        publisher = self.make_publisher("ubuntu", "daily")
        self.assertEqual(
            1024 * 1024 * 1024,
            publisher.size_limit_extension("armhf+omap4", "img"))
        self.assertEqual(
            1024 * 1024 * 1024,
            publisher.size_limit_extension("i386", "tar.gz"))
        self.assertEqual(
            publisher.size_limit("i386"),
            publisher.size_limit_extension("i386", "iso"))

    def test_new_publish_dir_honours_no_copy(self):
        self.config["CDIMAGE_NOCOPY"] = "1"
        publisher = self.make_publisher("ubuntu", "daily")
        publish_current = os.path.join(publisher.publish_base, "current")
        touch(os.path.join(
            publish_current, "%s-alternate-i386.iso" % self.config.series))
        publisher.new_publish_dir("20120807")
        self.assertEqual(
            [], os.listdir(os.path.join(publisher.publish_base, "20120807")))

    def test_new_publish_dir_copies_same_series(self):
        publisher = self.make_publisher("ubuntu", "daily")
        publish_current = os.path.join(publisher.publish_base, "current")
        image = "%s-alternate-i386.iso" % self.config.series
        touch(os.path.join(publish_current, image))
        publisher.new_publish_dir("20120807")
        self.assertEqual(
            [image],
            os.listdir(os.path.join(publisher.publish_base, "20120807")))

    def test_new_publish_dir_skips_different_series(self):
        publisher = self.make_publisher("ubuntu", "daily")
        publish_current = os.path.join(publisher.publish_base, "current")
        image = "warty-alternate-i386.iso"
        touch(os.path.join(publish_current, image))
        publisher.new_publish_dir("20120807")
        self.assertEqual(
            [], os.listdir(os.path.join(publisher.publish_base, "20120807")))

    def test_new_publish_dir_prefers_pending(self):
        publisher = self.make_publisher("ubuntu", "daily")
        publish_current = os.path.join(publisher.publish_base, "current")
        touch(os.path.join(
            publish_current, "%s-alternate-i386.iso" % self.config.series))
        publish_pending = os.path.join(publisher.publish_base, "pending")
        touch(os.path.join(
            publish_pending, "%s-alternate-amd64.iso" % self.config.series))
        publisher.new_publish_dir("20130319")
        self.assertEqual(
            ["%s-alternate-amd64.iso" % self.config.series],
            os.listdir(os.path.join(publisher.publish_base, "20130319")))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    def test_publish_binary(self, mock_zsyncmake, *args):
        publisher = self.make_publisher("ubuntu", "daily-live")
        source_dir = publisher.image_output("i386")
        touch(os.path.join(
            source_dir, "%s-desktop-i386.raw" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-i386.list" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-i386.manifest" % self.config.series))
        self.capture_logging()
        list(publisher.publish_binary("desktop", "i386", "20120807"))
        self.assertLogEqual([
            "Publishing i386 ...",
            "Unknown file type 'empty'; assuming .iso",
            "Publishing i386 live manifest ...",
            "Making i386 zsync metafile ...",
        ])
        target_dir = os.path.join(publisher.publish_base, "20120807")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            "%s-desktop-i386.iso" % self.config.series,
            "%s-desktop-i386.list" % self.config.series,
            "%s-desktop-i386.manifest" % self.config.series,
        ], os.listdir(target_dir))
        mock_zsyncmake.assert_called_once_with(
            os.path.join(
                target_dir, "%s-desktop-i386.iso" % self.config.series),
            os.path.join(
                target_dir, "%s-desktop-i386.iso.zsync" % self.config.series),
            "%s-desktop-i386.iso" % self.config.series)

    def test_publish_netboot(self):
        publisher = self.make_publisher("ubuntu-server", "daily-live")
        source_dir = publisher.image_output("amd64")
        tarname = "%s-netboot-amd64.tar.gz" % self.config.series
        save_tarname = ".%s" % tarname
        isoname = "%s-live-server-amd64.iso" % self.config.series
        date = "20201215"
        os.makedirs(source_dir)
        with tarfile.open(os.path.join(source_dir, tarname), 'w:gz') as tf:
            ti = tarfile.TarInfo('config.in')
            content = b'v=#ISOURL#'
            ti.size = len(content)
            tf.addfile(ti, io.BytesIO(content))
        target_dir = os.path.join(publisher.publish_base, date)
        target_image = os.path.join(target_dir, isoname)
        touch(target_image)

        publisher.publish_netboot("amd64", target_image)

        self.assertCountEqual([
            tarname,
            save_tarname,
            isoname,
            "netboot",
        ], os.listdir(target_dir))
        self.assertCountEqual([
            "config",
        ], os.listdir(os.path.join(target_dir, "netboot")))
        with open(os.path.join(target_dir, "netboot/config")) as fp:
            self.assertEqual(
                'v=https://%s/ubuntu-server/daily-live/%s/%s' % (
                    publisher.tree.site_name, date, isoname),
                fp.read())
        with tarfile.open(os.path.join(target_dir, tarname), 'r:gz') as tf:
            for ti in tf:
                self.assertEqual(ti.name, 'config')
                self.assertEqual(
                    ('v=https://%s/ubuntu-server/daily-live/%s/%s' % (
                        publisher.tree.site_name, date, isoname)).encode(
                        'utf-8'),
                    tf.extractfile(ti).read())

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.DailyTreePublisher.detect_image_extension",
                return_value="img.xz")
    @mock.patch("cdimage.tree.zsyncmake")
    def test_publish_core_binary(self, mock_zsyncmake, *args):
        self.config["DIST"] = "xenial"
        publisher = self.make_publisher("ubuntu-core", "daily-live")
        source_dir = publisher.image_output("amd64")
        touch(os.path.join(
            source_dir, "%s-live-core-amd64.raw" % self.config.series))
        touch(os.path.join(
            source_dir,
            "%s-live-core-amd64.model-assertion" % self.config.series))
        self.capture_logging()
        list(publisher.publish_binary("live-core", "amd64", "20170429"))
        self.assertLogEqual([
            "Publishing amd64 ...",
            "Publishing amd64 model assertion ...",
            "Making amd64 zsync metafile ...",
        ])
        target_dir = os.path.join(publisher.publish_base, "20170429")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            "ubuntu-core-16-amd64.img.xz",
            "ubuntu-core-16-amd64.model-assertion",
        ], os.listdir(target_dir))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.DailyTreePublisher.detect_image_extension",
                return_value="img.xz")
    @mock.patch("cdimage.tree.zsyncmake")
    def test_publish_appliance_binary(self, mock_zsyncmake, *args):
        self.config["DIST"] = "bionic"
        publisher = self.make_publisher("ubuntu-appliance", "daily-live")
        source_dir = publisher.image_output("amd64")
        touch(os.path.join(
            source_dir, "%s-live-core-amd64.raw" % self.config.series))
        touch(os.path.join(
            source_dir,
            "%s-live-core-amd64.model-assertion" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-live-core-amd64.qcow2" % self.config.series))
        self.capture_logging()
        list(publisher.publish_binary("live-core", "amd64", "20170429"))
        self.assertLogEqual([
            "Publishing amd64 ...",
            "Publishing amd64 model assertion ...",
            "Publishing amd64 qcow2 image ...",
            "Making amd64 zsync metafile ...",
        ])
        target_dir = os.path.join(publisher.publish_base, "20170429")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            "ubuntu-core-18-amd64.img.xz",
            "ubuntu-core-18-amd64.model-assertion",
            "ubuntu-core-18-amd64.qcow2",
        ], os.listdir(target_dir))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    def test_publish_canary_binary(self, mock_zsyncmake, *args):
        publisher = self.make_publisher("ubuntu", "daily-canary")
        source_dir = publisher.image_output("amd64")
        touch(os.path.join(
            source_dir, "%s-desktop-canary-amd64.raw" %
            self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-canary-amd64.list" %
            self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-canary-amd64.manifest" %
            self.config.series))
        self.capture_logging()
        list(publisher.publish_binary("desktop-canary", "amd64", "20201215"))
        self.assertLogEqual([
            "Publishing amd64 ...",
            "Unknown file type 'empty'; assuming .iso",
            "Publishing amd64 live manifest ...",
            "Making amd64 zsync metafile ...",
        ])
        target_dir = os.path.join(publisher.publish_base, "20201215")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            "%s-desktop-canary-amd64.iso" % self.config.series,
            "%s-desktop-canary-amd64.list" % self.config.series,
            "%s-desktop-canary-amd64.manifest" % self.config.series,
        ], os.listdir(target_dir))
        mock_zsyncmake.assert_called_once_with(
            os.path.join(
                target_dir, "%s-desktop-canary-amd64.iso" %
                self.config.series),
            os.path.join(
                target_dir, "%s-desktop-canary-amd64.iso.zsync" %
                self.config.series),
            "%s-desktop-canary-amd64.iso" % self.config.series)

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    def test_publish_legacy_binary(self, mock_zsyncmake, *args):
        publisher = self.make_publisher("ubuntu", "daily-legacy")
        source_dir = publisher.image_output("amd64")
        touch(os.path.join(
            source_dir, "%s-desktop-legacy-amd64.raw" %
            self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-legacy-amd64.list" %
            self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-legacy-amd64.manifest" %
            self.config.series))
        self.capture_logging()
        list(publisher.publish_binary("desktop-legacy", "amd64", "20201215"))
        self.assertLogEqual([
            "Publishing amd64 ...",
            "Unknown file type 'empty'; assuming .iso",
            "Publishing amd64 live manifest ...",
            "Making amd64 zsync metafile ...",
        ])
        target_dir = os.path.join(publisher.publish_base, "20201215")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            "%s-desktop-legacy-amd64.iso" % self.config.series,
            "%s-desktop-legacy-amd64.list" % self.config.series,
            "%s-desktop-legacy-amd64.manifest" % self.config.series,
        ], os.listdir(target_dir))
        mock_zsyncmake.assert_called_once_with(
            os.path.join(
                target_dir, "%s-desktop-legacy-amd64.iso" %
                self.config.series),
            os.path.join(
                target_dir, "%s-desktop-legacy-amd64.iso.zsync" %
                self.config.series),
            "%s-desktop-legacy-amd64.iso" % self.config.series)

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    def test_publish_minimal_binary(self, mock_zsyncmake, *args):
        publisher = self.make_publisher("xubuntu", "daily-minimal")
        source_dir = publisher.image_output("amd64")
        touch(os.path.join(
            source_dir, "%s-minimal-amd64.raw" %
            self.config.series))
        touch(os.path.join(
            source_dir, "%s-minimal-amd64.list" %
            self.config.series))
        touch(os.path.join(
            source_dir, "%s-minimal-amd64.manifest" %
            self.config.series))
        self.capture_logging()
        list(publisher.publish_binary("minimal", "amd64", "20201215"))
        self.assertLogEqual([
            "Publishing amd64 ...",
            "Unknown file type 'empty'; assuming .iso",
            "Publishing amd64 live manifest ...",
            "Making amd64 zsync metafile ...",
        ])
        target_dir = os.path.join(publisher.publish_base, "20201215")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            "%s-minimal-amd64.iso" % self.config.series,
            "%s-minimal-amd64.list" % self.config.series,
            "%s-minimal-amd64.manifest" % self.config.series,
        ], os.listdir(target_dir))
        mock_zsyncmake.assert_called_once_with(
            os.path.join(
                target_dir, "%s-minimal-amd64.iso" %
                self.config.series),
            os.path.join(
                target_dir, "%s-minimal-amd64.iso.zsync" %
                self.config.series),
            "%s-minimal-amd64.iso" % self.config.series)

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    def test_publish_mini_iso_binary(self, mock_zsyncmake, *args):
        publisher = self.make_publisher("ubuntu-mini-iso", "daily-live")
        source_dir = publisher.image_output("amd64")
        touch(os.path.join(
            source_dir, "%s-mini-iso-amd64.raw" %
            self.config.series))
        self.capture_logging()
        list(publisher.publish_binary("mini-iso", "amd64", "20201215"))
        self.assertLogEqual([
            "Publishing amd64 ...",
            "Unknown file type 'empty'; assuming .iso",
            "Making amd64 zsync metafile ...",
        ])
        target_dir = os.path.join(publisher.publish_base, "20201215")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            "%s-mini-iso-amd64.iso" % self.config.series,
        ], os.listdir(target_dir))
        mock_zsyncmake.assert_called_once_with(
            os.path.join(
                target_dir, "%s-mini-iso-amd64.iso" %
                self.config.series),
            os.path.join(
                target_dir, "%s-mini-iso-amd64.iso.zsync" %
                self.config.series),
            "%s-mini-iso-amd64.iso" % self.config.series)

    def test_publish_livecd_base(self):
        publisher = self.make_publisher("livecd-base", "livecd-base")
        source_dir = os.path.join(
            self.temp_dir, "scratch", "livecd-base", self.config.series,
            "livecd-base", "live")
        for ext in (
            "squashfs", "kernel", "initrd", "manifest", "manifest-remove",
            "manifest-minimal-remove",
        ):
            touch(os.path.join(source_dir, "i386.%s" % ext))
        self.capture_logging()
        self.assertEqual(
            ["livecd-base/livecd-base/i386"],
            list(publisher.publish_livecd_base("i386", "20130318")))
        self.assertLogEqual(["Publishing i386 ..."])
        target_dir = os.path.join(publisher.publish_base, "20130318")
        self.assertCountEqual([
            "i386.squashfs", "i386.kernel", "i386.initrd",
            "i386.manifest", "i386.manifest-remove",
            "i386.manifest-minimal-remove",
        ], os.listdir(target_dir))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    def test_publish_source(self, mock_zsyncmake, *args):
        publisher = self.make_publisher("ubuntu", "daily-live")
        source_dir = publisher.image_output("src")
        touch(os.path.join(source_dir, "%s-src-1.raw" % self.config.series))
        touch(os.path.join(source_dir, "%s-src-1.list" % self.config.series))
        touch(os.path.join(source_dir, "%s-src-2.raw" % self.config.series))
        touch(os.path.join(source_dir, "%s-src-2.list" % self.config.series))
        self.capture_logging()
        list(publisher.publish_source("20120807"))
        self.assertLogEqual([
            "Publishing source 1 ...",
            "Making source 1 zsync metafile ...",
            "Publishing source 2 ...",
            "Making source 2 zsync metafile ...",
        ])
        target_dir = os.path.join(publisher.publish_base, "20120807", "source")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            "%s-src-1.iso" % self.config.series,
            "%s-src-1.list" % self.config.series,
            "%s-src-2.iso" % self.config.series,
            "%s-src-2.list" % self.config.series,
        ], os.listdir(target_dir))
        mock_zsyncmake.assert_has_calls([
            mock.call(
                os.path.join(target_dir, "%s-src-1.iso" % self.config.series),
                os.path.join(
                    target_dir, "%s-src-1.iso.zsync" % self.config.series),
                "%s-src-1.iso" % self.config.series),
            mock.call(
                os.path.join(target_dir, "%s-src-2.iso" % self.config.series),
                os.path.join(
                    target_dir, "%s-src-2.iso.zsync" % self.config.series),
                "%s-src-2.iso" % self.config.series),
        ])

    def test_link(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        target_dir = os.path.join(publisher.publish_base, "20130319")
        os.makedirs(target_dir)
        publisher.link("20130319", "current")
        self.assertEqual(
            "20130319",
            os.readlink(os.path.join(publisher.publish_base, "current")))

    def test_published_images(self):
        self.config["DIST"] = "bionic"
        publisher = self.make_publisher("ubuntu", "daily-live")
        target_dir = os.path.join(publisher.publish_base, "20130321")
        for name in (
            "SHA256SUMS",
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
        ):
            touch(os.path.join(target_dir, name))
        self.assertEqual(
            set(["bionic-desktop-amd64.iso", "bionic-desktop-i386.iso"]),
            publisher.published_images("20130321"))

    def test_published_core_images(self):
        self.config["DIST"] = "bionic"
        self.config["ARCHES"] = "amd64 i386"
        publisher = self.make_publisher("ubuntu-core", "daily-live")
        target_dir = os.path.join(publisher.publish_base, "20170429")
        for name in (
            "SHA256SUMS",
            "ubuntu-core-16-amd64.img.xz",
            "ubuntu-core-16-amd64.model-assertion",
            "ubuntu-core-16-i386.img.xz",
            "ubuntu-core-16-i386.model-assertion",
        ):
            touch(os.path.join(target_dir, name))
        self.assertEqual(
            set(["ubuntu-core-16-amd64.img.xz", "ubuntu-core-16-i386.img.xz"]),
            publisher.published_images("20170429"))

    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_missing_to_single(self, mock_polish_directory):
        self.config["DIST"] = "bionic"
        publisher = self.make_publisher("ubuntu", "daily-live")
        target_dir = os.path.join(publisher.publish_base, "20130321")
        for name in (
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
        ):
            touch(os.path.join(target_dir, name))
        publisher.mark_current("20130321", ["amd64", "i386"])
        publish_current = os.path.join(publisher.publish_base, "current")
        self.assertTrue(os.path.islink(publish_current))
        self.assertEqual("20130321", os.readlink(publish_current))
        self.assertEqual(0, mock_polish_directory.call_count)

    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_missing_to_mixed(self, mock_polish_directory):
        self.config["DIST"] = "bionic"
        publisher = self.make_publisher("ubuntu", "daily-live")
        target_dir = os.path.join(publisher.publish_base, "20130321")
        for name in (
            "SHA256SUMS",
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
        ):
            touch(os.path.join(target_dir, name))
        publisher.mark_current("20130321", ["amd64"])
        publish_current = os.path.join(publisher.publish_base, "current")
        self.assertFalse(os.path.islink(publish_current))
        self.assertTrue(os.path.isdir(publish_current))
        self.assertCountEqual(
            ["bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest"],
            os.listdir(publish_current))
        for name in (
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
        ):
            path = os.path.join(publish_current, name)
            self.assertTrue(os.path.islink(path))
            self.assertEqual(
                os.path.join(os.pardir, "20130321", name), os.readlink(path))
        self.assertEqual([target_dir], publisher.checksum_dirs)
        mock_polish_directory.assert_called_once_with("current")

    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_single_to_single(self, mock_polish_directory):
        self.config["DIST"] = "bionic"
        publisher = self.make_publisher("ubuntu", "daily-live")
        for date in "20130320", "20130321":
            for name in (
                "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
                "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
            ):
                touch(os.path.join(publisher.publish_base, date, name))
        publish_current = os.path.join(publisher.publish_base, "current")
        os.symlink("20130320", publish_current)
        publisher.mark_current("20130321", ["amd64", "i386"])
        self.assertTrue(os.path.islink(publish_current))
        self.assertEqual("20130321", os.readlink(publish_current))
        self.assertEqual(0, mock_polish_directory.call_count)

    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_single_to_mixed(self, mock_polish_directory):
        self.config["DIST"] = "bionic"
        publisher = self.make_publisher("ubuntu", "daily-live")
        for date in "20130320", "20130321":
            for name in (
                "SHA256SUMS",
                "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
                "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
            ):
                touch(os.path.join(publisher.publish_base, date, name))
        publish_current = os.path.join(publisher.publish_base, "current")
        os.symlink("20130320", publish_current)
        publisher.mark_current("20130321", ["amd64"])
        self.assertFalse(os.path.islink(publish_current))
        self.assertTrue(os.path.isdir(publish_current))
        self.assertCountEqual([
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
        ], os.listdir(publish_current))
        for date, arch in (("20130320", "i386"), ("20130321", "amd64")):
            for name in (
                "bionic-desktop-%s.iso" % arch,
                "bionic-desktop-%s.manifest" % arch,
            ):
                path = os.path.join(publish_current, name)
                self.assertTrue(os.path.islink(path))
                self.assertEqual(
                    os.path.join(os.pardir, date, name), os.readlink(path))
        self.assertCountEqual([
            os.path.join(publisher.publish_base, "20130320"),
            os.path.join(publisher.publish_base, "20130321"),
        ], publisher.checksum_dirs)
        mock_polish_directory.assert_called_once_with("current")

    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_mixed_to_single(self, mock_polish_directory):
        self.config["DIST"] = "bionic"
        publisher = self.make_publisher("ubuntu", "daily-live")
        for date in "20130320", "20130321":
            for name in (
                "SHA256SUMS",
                "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
                "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
            ):
                touch(os.path.join(publisher.publish_base, date, name))
        publish_current = os.path.join(publisher.publish_base, "current")
        osextras.ensuredir(publish_current)
        for date, arch in (("20130320", "i386"), ("20130321", "amd64")):
            for name in (
                "bionic-desktop-%s.iso" % arch,
                "bionic-desktop-%s.manifest" % arch,
            ):
                os.symlink(
                    os.path.join(os.pardir, date, name),
                    os.path.join(publish_current, name))
        publisher.mark_current("20130321", ["i386"])
        self.assertTrue(os.path.islink(publish_current))
        self.assertEqual("20130321", os.readlink(publish_current))
        self.assertEqual(0, mock_polish_directory.call_count)

    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_mixed_to_mixed(self, mock_polish_directory):
        self.config["DIST"] = "bionic"
        publisher = self.make_publisher("ubuntu", "daily-live")
        for date in "20130320", "20130321":
            for name in (
                "SHA256SUMS",
                "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
                "bionic-desktop-arm64.iso", "bionic-desktop-arm64.manifest",
                "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
            ):
                touch(os.path.join(publisher.publish_base, date, name))
        publish_current = os.path.join(publisher.publish_base, "current")
        osextras.ensuredir(publish_current)
        for date, arch in (("20130320", "i386"), ("20130321", "amd64")):
            for name in (
                "bionic-desktop-%s.iso" % arch,
                "bionic-desktop-%s.manifest" % arch,
            ):
                os.symlink(
                    os.path.join(os.pardir, date, name),
                    os.path.join(publish_current, name))
        publisher.mark_current("20130321", ["i386"])
        self.assertFalse(os.path.islink(publish_current))
        self.assertTrue(os.path.isdir(publish_current))
        self.assertCountEqual([
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
        ], os.listdir(publish_current))
        for name in (
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
        ):
            path = os.path.join(publish_current, name)
            self.assertTrue(os.path.islink(path))
            self.assertEqual(
                os.path.join(os.pardir, "20130321", name), os.readlink(path))
        self.assertEqual(
            [os.path.join(publisher.publish_base, "20130321")],
            publisher.checksum_dirs)
        mock_polish_directory.assert_called_once_with("current")
        mock_polish_directory.reset_mock()
        publisher.checksum_dirs = []
        publisher.mark_current("20130320", ["arm64"])
        self.assertFalse(os.path.islink(publish_current))
        self.assertTrue(os.path.isdir(publish_current))
        self.assertCountEqual([
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
            "bionic-desktop-arm64.iso", "bionic-desktop-arm64.manifest",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
        ], os.listdir(publish_current))
        for date, arch in (
            ("20130320", "arm64"),
            ("20130321", "amd64"), ("20130321", "i386"),
        ):
            for name in (
                "bionic-desktop-%s.iso" % arch,
                "bionic-desktop-%s.manifest" % arch,
            ):
                path = os.path.join(publish_current, name)
                self.assertTrue(os.path.islink(path))
                self.assertEqual(
                    os.path.join(os.pardir, date, name), os.readlink(path))
        self.assertCountEqual([
            os.path.join(publisher.publish_base, "20130320"),
            os.path.join(publisher.publish_base, "20130321"),
        ], publisher.checksum_dirs)
        mock_polish_directory.assert_called_once_with("current")

    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_ignores_old_series(self, mock_polish_directory):
        self.config["DIST"] = "saucy"
        publisher = self.make_publisher("ubuntu", "daily-live")
        old_target_dir = os.path.join(publisher.publish_base, "20130321")
        for name in (
            "bionic-desktop-amd64.iso", "bionic-desktop-amd64.manifest",
            "bionic-desktop-i386.iso", "bionic-desktop-i386.manifest",
        ):
            touch(os.path.join(old_target_dir, name))
        target_dir = os.path.join(publisher.publish_base, "20130921")
        for name in (
            "saucy-desktop-amd64.iso", "saucy-desktop-amd64.manifest",
            "saucy-desktop-i386.iso", "saucy-desktop-i386.manifest",
        ):
            touch(os.path.join(target_dir, name))
        publish_current = os.path.join(publisher.publish_base, "current")
        os.symlink("20130321", publish_current)
        publisher.mark_current("20130921", ["amd64", "i386"])
        self.assertTrue(os.path.islink(publish_current))
        self.assertEqual("20130921", os.readlink(publish_current))
        self.assertEqual(0, mock_polish_directory.call_count)

    def test_set_link_descriptions(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        os.makedirs(publisher.publish_base)
        publisher.set_link_descriptions()
        htaccess_path = os.path.join(publisher.publish_base, ".htaccess")
        self.assertTrue(os.path.exists(htaccess_path))
        with open(htaccess_path) as htaccess:
            self.assertRegex(htaccess.read(), dedent("""\
                AddDescription "Latest.*" current
                AddDescription "Most recently built.*" pending
                IndexOptions FancyIndexing
                """))

    def test_qa_product_main_tracker(self):
        for project, image_type, publish_type, product in (
            ("ubuntu", "daily-live", "desktop", "Ubuntu Desktop"),
            ("kubuntu", "daily-live", "desktop", "Kubuntu Desktop"),
            ("xubuntu", "daily-live", "desktop", "Xubuntu Desktop"),
            ("ubuntu-server", "daily", "server", "Ubuntu Server"),
            ("ubuntustudio", "daily-live", "desktop", "Ubuntu Studio DVD"),
            ("lubuntu", "daily-live", "desktop", "Lubuntu Desktop"),
            ("ubuntu-base", "daily", "base", "Ubuntu Base"),
            ("ubuntukylin", "daily-live", "desktop", "Ubuntu Kylin Desktop"),
            ("ubuntu-budgie", "daily-live", "desktop",
                "Ubuntu Budgie Desktop"),
            ("ubuntu-mate", "daily-live", "desktop", "Ubuntu MATE Desktop"),
            ("ubuntucinnamon", "daily-live", "desktop",
                "Ubuntu Cinnamon Desktop"),
            ("ubuntu-mini-iso", "daily-live", "mini-iso", "Ubuntu Mini ISO"),
        ):
            # Use "daily" here to match bin/post-qa; qa_product shouldn't
            # use the publisher's image_type at all.
            publisher = self.make_publisher(project, "daily")
            self.assertEqual(
                ("%s amd64" % product, "iso"),
                publisher.qa_product(
                    project, image_type, publish_type, "amd64"))

    def test_qa_product_ubuntu_preinstalled(self):
        publisher = self.make_publisher("ubuntu", "daily")
        self.assertEqual(
            ("Ubuntu Desktop Preinstalled armhf+nexus7", "iso"),
            publisher.qa_product(
                "ubuntu", "daily-preinstalled", "preinstalled-desktop",
                "armhf+nexus7"))

    def test_qa_product_lubuntu_preinstalled(self):
        publisher = self.make_publisher("lubuntu", "daily")
        self.assertEqual(
            ("Lubuntu Desktop Preinstalled armhf+ac100", "iso"),
            publisher.qa_product(
                "lubuntu", "daily-preinstalled", "preinstalled-desktop",
                "armhf+ac100"))

    def test_cdimage_project_main_tracker(self):
        for project, image_type, publish_type, product in (
            ("ubuntu", "daily-live", "desktop", "Ubuntu Desktop"),
            ("kubuntu", "daily-live", "desktop", "Kubuntu Desktop"),
            ("xubuntu", "daily-live", "desktop", "Xubuntu Desktop"),
            ("ubuntu-server", "daily", "server", "Ubuntu Server"),
            ("ubuntustudio", "daily-live", "desktop", "Ubuntu Studio DVD"),
            ("lubuntu", "daily", "alternate", "Lubuntu Alternate"),
            ("lubuntu", "daily-live", "desktop", "Lubuntu Desktop"),
            ("lubuntu-next", "daily-live", "desktop", "Lubuntu Next Desktop"),
            ("ubuntu-base", "daily", "base", "Ubuntu Base"),
            ("ubuntukylin", "daily-live", "desktop", "Ubuntu Kylin Desktop"),
            ("ubuntu-budgie", "daily-live", "desktop",
                "Ubuntu Budgie Desktop"),
            ("ubuntu-mate", "daily-live", "desktop", "Ubuntu MATE Desktop"),
        ):
            # Use "daily" here to match bin/post-qa; qa_product shouldn't
            # use the publisher's image_type at all.
            publisher = self.make_publisher(project, "daily")
            self.assertEqual(
                (project, image_type, publish_type, "amd64"),
                publisher.cdimage_project(
                    "%s amd64" % product, "iso"))

    @mock_isotracker
    def test_post_qa(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        os.makedirs(os.path.join(publisher.publish_base, "20130221"))
        publisher.post_qa(
            "20130221", [
                "ubuntu/daily-live/bionic-desktop-i386",
                "ubuntu/daily-live/bionic-desktop-amd64",
            ])
        expected = [
            ["Ubuntu Desktop i386", "20130221", ""],
            ["Ubuntu Desktop amd64", "20130221", ""],
        ]
        self.assertEqual("iso-bionic", isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

        os.makedirs(os.path.join(
            self.tree.project_base, "bionic", "daily-live", "20130221"))
        publisher.post_qa(
            "20130221", [
                "ubuntu/bionic/daily-live/bionic-desktop-i386",
                "ubuntu/bionic/daily-live/bionic-desktop-amd64",
            ])
        expected = [
            ["Ubuntu Desktop i386", "20130221", ""],
            ["Ubuntu Desktop amd64", "20130221", ""],
        ]
        self.assertEqual("iso-bionic", isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

    @mock_isotracker
    def test_post_qa_oversized(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        touch(os.path.join(
            self.temp_dir, "www", "full", "daily-live", "20130315",
            "bionic-desktop-i386.OVERSIZED"))
        publisher.post_qa(
            "20130315", ["ubuntu/daily-live/bionic-desktop-i386"])
        expected_note = (
            "<strong>WARNING: This image is OVERSIZED. This should never "
            "happen during milestone testing.</strong>")
        expected = [["Ubuntu Desktop i386", "20130315", expected_note]]
        self.assertEqual("iso-bionic", isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

        publisher = self.make_publisher("kubuntu", "daily-live")
        touch(os.path.join(
            self.temp_dir, "www", "full", "kubuntu", "bionic", "daily-live",
            "20130315", "bionic-desktop-i386.OVERSIZED"))
        publisher.post_qa(
            "20130315", ["kubuntu/bionic/daily-live/bionic-desktop-i386"])
        expected_note = (
            "<strong>WARNING: This image is OVERSIZED. This should never "
            "happen during milestone testing.</strong>")
        expected = [["Kubuntu Desktop i386", "20130315", expected_note]]
        self.assertEqual("iso-bionic", isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

    @mock_isotracker
    def test_post_qa_wrong_date(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        self.assertRaisesRegex(
            Exception, r"Cannot post images from nonexistent directory: .*",
            publisher.post_qa, "bad-date",
            ["ubuntu/daily-live/bionic-desktop-i386"])

    @mock.patch("subprocess.call", return_value=0)
    @mock.patch("cdimage.tree.DailyTreePublisher.make_web_indices")
    def test_polish_directory_no_metalink_focal(self,
                                                mock_make_web_indices,
                                                mock_call):
        self.config["DIST"] = "focal"
        publisher = self.make_publisher("ubuntu", "daily-live")
        target_dir = os.path.join(publisher.publish_base, "20130320")
        touch(os.path.join(
            target_dir, "%s-desktop-i386.iso" % self.config.series))
        self.capture_logging()
        publisher.polish_directory("20130320")
        self.assertCountEqual([
            ".publish_info",
            "SHA256SUMS",
            "%s-desktop-i386.iso" % self.config.series,
        ], os.listdir(target_dir))
        mock_make_web_indices.assert_called_once_with(
            target_dir, self.config.series, status="daily")
        mock_call.assert_not_called()

    @mock.patch("subprocess.call", return_value=0)
    @mock.patch("cdimage.tree.DailyTreePublisher.make_web_indices")
    def test_polish_directory(self, mock_make_web_indices, mock_call):
        publisher = self.make_publisher("ubuntu", "daily-live")
        target_dir = os.path.join(publisher.publish_base, "20130320")
        touch(os.path.join(
            target_dir, "%s-desktop-i386.iso" % self.config.series))
        self.capture_logging()
        publisher.polish_directory("20130320")
        self.assertCountEqual([
            ".publish_info",
            "SHA256SUMS",
            "%s-desktop-i386.iso" % self.config.series,
        ], os.listdir(target_dir))
        mock_make_web_indices.assert_called_once_with(
            target_dir, self.config.series, status="daily")

    def test_create_publish_info_file(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        target_dir = os.path.join(publisher.publish_base, "20130320")
        touch(os.path.join(
            target_dir, "%s-desktop-i386.iso" % self.config.series))
        touch(os.path.join(
            target_dir, "%s-desktop-i386.img" % self.config.series))
        touch(os.path.join(
            target_dir, "%s-desktop-i386.manifest" % self.config.series))
        self.capture_logging()
        publisher.create_publish_info_file("20130320")
        self.assertCountEqual([
            ".publish_info",
            "%s-desktop-i386.iso" % self.config.series,
            "%s-desktop-i386.img" % self.config.series,
            "%s-desktop-i386.manifest" % self.config.series,
        ], os.listdir(target_dir))
        with open(os.path.join(target_dir, ".publish_info")) as info:
            self.assertCountEqual([
                "%s-desktop-i386.img 20130320" % self.config.series,
                "%s-desktop-i386.iso 20130320" % self.config.series,
            ], info.read().splitlines())

    def test_create_publish_info_file_current(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        iso1 = "%s-desktop-i386.iso" % self.config.series
        iso2 = "%s-desktop-amd64.iso" % self.config.series
        source1_dir = os.path.join(publisher.publish_base, "20130320")
        source2_dir = os.path.join(publisher.publish_base, "20130321")
        target_dir = os.path.join(publisher.publish_base, "current")
        osextras.ensuredir(target_dir)
        touch(os.path.join(source1_dir, iso1))
        touch(os.path.join(source2_dir, iso2))
        osextras.symlink_force(os.path.join(source1_dir, iso1),
                               os.path.join(target_dir, iso1))
        osextras.symlink_force(os.path.join(source2_dir, iso2),
                               os.path.join(target_dir, iso2))
        self.capture_logging()
        publisher.create_publish_info_file("current")
        self.assertCountEqual([
            ".publish_info",
            "%s-desktop-amd64.iso" % self.config.series,
            "%s-desktop-i386.iso" % self.config.series,
        ], os.listdir(target_dir))
        with open(os.path.join(target_dir, ".publish_info")) as info:
            self.assertCountEqual([
                "%s-desktop-amd64.iso 20130321" % self.config.series,
                "%s-desktop-i386.iso 20130320" % self.config.series,
            ], info.read().splitlines())

    def test_create_publish_info_file_current_is_link(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        source_dir = os.path.join(publisher.publish_base, "20130320")
        touch(os.path.join(
            source_dir, "%s-desktop-i386.iso" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-i386.img" % self.config.series))
        with open(os.path.join(source_dir, ".publish_info"), "w") as info:
            info.write("PLACEHOLDER")
        target_dir = os.path.join(publisher.publish_base, "current")
        osextras.symlink_force(source_dir, target_dir)
        self.capture_logging()
        publisher.create_publish_info_file("current")
        self.assertCountEqual([
            ".publish_info",
            "%s-desktop-i386.iso" % self.config.series,
            "%s-desktop-i386.img" % self.config.series,
        ], os.listdir(target_dir))
        with open(os.path.join(target_dir, ".publish_info")) as info:
            # Make sure the .publish_info didn't get modified in this case.
            self.assertEqual("PLACEHOLDER", info.read())

    @mock.patch("cdimage.tree.generate_ubuntu_core_image_lxd_metadata")
    def test_generate_lxd_metadata(self, mock_generate):
        self.config["DIST"] = "noble"
        publisher = self.make_publisher("ubuntu-core", "daily-live")
        source_dir = os.path.join(publisher.publish_base, "20130320")
        image_path = os.path.join(
            source_dir,
            "ubuntu-core-%s-amd64.img.xz" % self.config.core_series)
        touch(image_path)
        publisher.generate_lxd_metadata("20130320")
        mock_generate.assert_called_once_with(image_path)

    @mock.patch("cdimage.tree.generate_ubuntu_core_image_lxd_metadata")
    def test_generate_lxd_metadata_non_core(self, mock_generate):
        self.config["DIST"] = "noble"
        publisher = self.make_publisher("ubuntu", "daily-live")
        source_dir = os.path.join(publisher.publish_base, "20130320")
        image_path = os.path.join(
            source_dir,
            "ubuntu-core-%s-amd64.img.xz" % self.config.core_series)
        touch(image_path)
        publisher.generate_lxd_metadata("20130320")
        mock_generate.assert_not_called()

    @mock.patch("cdimage.tree.generate_ubuntu_core_image_lxd_metadata")
    def test_generate_lxd_metadata_disabled(self, mock_generate):
        self.config["DIST"] = "noble"
        self.config["LXD_METADATA"] = "0"
        publisher = self.make_publisher("ubuntu-core", "daily-live")
        source_dir = os.path.join(publisher.publish_base, "20130320")
        image_path = os.path.join(
            source_dir,
            "ubuntu-core-%s-amd64.img.xz" % self.config.core_series)
        touch(image_path)
        publisher.generate_lxd_metadata("20130320")
        mock_generate.assert_not_called()

    @mock.patch("cdimage.tree.generate_ubuntu_core_image_lxd_metadata")
    def test_generate_lxd_metadata_failed(self, mock_generate):
        self.config["DIST"] = "noble"
        publisher = self.make_publisher("ubuntu-core", "daily-live")
        source_dir = os.path.join(publisher.publish_base, "20130320")
        image_path = os.path.join(
            source_dir,
            "ubuntu-core-%s-amd64.img.xz" % self.config.core_series)
        touch(image_path)
        mock_generate.side_effect = Exception("Failed")
        self.capture_logging()
        publisher.generate_lxd_metadata("20130320")
        mock_generate.assert_called_once_with(image_path)
        self.assertLogEqual([
            "Generating LXD metadata for ubuntu-core 20130320 ...",
            "Failed to generate LXD metadata for %s: Failed" % image_path,
        ])

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    @mock.patch("cdimage.tree.DailyTreePublisher.post_qa")
    def test_publish(self, mock_post_qa, *args):
        self.config["ARCHES"] = "i386"
        publisher = self.make_publisher("ubuntu", "daily-live")
        source_dir = publisher.image_output("i386")
        touch(os.path.join(
            source_dir, "%s-desktop-i386.raw" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-i386.list" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-i386.manifest" % self.config.series))
        touch(os.path.join(
            publisher.britney_report, "%s_probs.html" % self.config.series))
        self.capture_logging()

        publisher.publish("20120807")

        self.assertLogEqual([
            "Publishing i386 ...",
            "Unknown file type 'empty'; assuming .iso",
            "Publishing i386 live manifest ...",
            "Making i386 zsync metafile ...",
            "No keys found; not signing images.",
        ])
        target_dir = os.path.join(publisher.publish_base, "20120807")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            ".htaccess",
            ".marked_good",
            ".publish_info",
            "FOOTER.html",
            "HEADER.html",
            "SHA256SUMS",
            "%s-desktop-i386.iso" % self.config.series,
            "%s-desktop-i386.list" % self.config.series,
            "%s-desktop-i386.manifest" % self.config.series,
        ], os.listdir(target_dir))
        self.assertCountEqual(
            [".htaccess", "20120807", "current", "pending"],
            os.listdir(publisher.publish_base))
        mock_post_qa.assert_called_once_with(
            "20120807",
            ["ubuntu/daily-live/%s-desktop-i386" % self.config.series])

        # Check if the resulting .publish_info file has the right info.
        with open(os.path.join(target_dir, ".publish_info")) as info:
            self.assertEqual(
                "%s-desktop-i386.iso 20120807\n" % self.config.series,
                info.read())

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    @mock.patch("cdimage.tree.DailyTreePublisher.post_qa")
    def test_publish_subtree(self, mock_post_qa, *args):
        self.config.subtree = "subtree/test"
        self.config["ARCHES"] = "i386"
        publisher = self.make_publisher("ubuntu", "daily-live")
        source_dir = publisher.image_output("i386")
        touch(os.path.join(
            source_dir, "%s-desktop-i386.raw" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-i386.list" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-i386.manifest" % self.config.series))
        touch(os.path.join(
            publisher.britney_report, "%s_probs.html" % self.config.series))
        self.capture_logging()

        publisher.publish("20120807")

        self.assertLogEqual([
            "Publishing for subtree 'subtree/test'",
            "Publishing i386 ...",
            "Unknown file type 'empty'; assuming .iso",
            "Publishing i386 live manifest ...",
            "Making i386 zsync metafile ...",
            "No keys found; not signing images.",
        ])
        target_dir = os.path.join(publisher.publish_base, "20120807")
        # Check if we published to the right place.
        self.assertIn("subtree/test", target_dir)
        # Otherwise, let's do the usual publish checks to make sure the
        # publisher didn't get confused.
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            ".htaccess",
            ".marked_good",
            ".publish_info",
            "FOOTER.html",
            "HEADER.html",
            "SHA256SUMS",
            "%s-desktop-i386.iso" % self.config.series,
            "%s-desktop-i386.list" % self.config.series,
            "%s-desktop-i386.manifest" % self.config.series,
        ], os.listdir(target_dir))
        self.assertCountEqual(
            [".htaccess", "20120807", "current", "pending"],
            os.listdir(publisher.publish_base))
        mock_post_qa.assert_called_once_with(
            "20120807",
            ["ubuntu/daily-live/%s-desktop-i386" % self.config.series])

        # Check if the resulting .publish_info file has the right info.
        with open(os.path.join(target_dir, ".publish_info")) as info:
            self.assertEqual(
                "%s-desktop-i386.iso 20120807\n" % self.config.series,
                info.read())

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    @mock.patch("cdimage.tree.generate_ubuntu_core_image_lxd_metadata")
    @mock.patch("cdimage.tree.DailyTreePublisher.post_qa")
    def test_publish_core(self, mock_post_qa, mock_metadata, *args):
        self.config["ARCHES"] = "amd64"
        self.config["DIST"] = "noble"
        publisher = self.make_publisher("ubuntu-core", "daily-live")
        source_dir = publisher.image_output("amd64")
        # For this test, we need the raw file to be an xz compressed file.
        touch(os.path.join(
            source_dir, "%s-live-core-amd64.qcow2" % self.config.series))
        touch(os.path.join(
            source_dir,
            "%s-live-core-amd64.model-assertion" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-live-core-amd64.manifest" % self.config.series))
        xz_empty_source = os.path.join(
            os.path.dirname(__file__), "data", "empty-xz-file.xz")
        shutil.copy(
            xz_empty_source,
            os.path.join(
                source_dir, "%s-live-core-amd64.raw" % self.config.series))
        # And here we also rely on the .type file being generated.
        type_path = os.path.join(
            source_dir, "%s-live-core-amd64.type" % self.config.series)
        with open(type_path, "w") as f:
            print("Disk Image", file=f)
        self.capture_logging()

        publisher.publish("20240718")

        # Check if the LXD metadata was generated.
        target_dir = os.path.join(publisher.publish_base, "20240718")
        mock_metadata.assert_called_once_with(
            os.path.join(target_dir, "ubuntu-core-24-amd64.img.xz"))

        self.assertLogEqual([
            "Publishing amd64 ...",
            "Unknown compressed file type 'Disk Image'; assuming .img.xz",
            "Publishing amd64 live manifest ...",
            "Publishing amd64 model assertion ...",
            "Publishing amd64 qcow2 image ...",
            "Making amd64 zsync metafile ...",
            "Generating LXD metadata for ubuntu-core 20240718 ...",
            "No keys found; not signing images.",
        ])
        self.assertEqual(["noble-live-core-amd64.type"],
                         os.listdir(source_dir))
        self.assertCountEqual([
            ".htaccess",
            ".marked_good",
            ".publish_info",
            "FOOTER.html",
            "HEADER.html",
            "SHA256SUMS",
            "ubuntu-core-24-amd64.model-assertion",
            "ubuntu-core-24-amd64.img.xz",
            "ubuntu-core-24-amd64.manifest",
            "ubuntu-core-24-amd64.qcow2",
        ], os.listdir(target_dir))
        self.assertCountEqual(
            [".htaccess", "20240718", "current", "pending"],
            os.listdir(publisher.publish_base))
        mock_post_qa.assert_called_once_with(
            "20240718",
            ["ubuntu-core/24/edge/noble-live-core-amd64"])

    def test_get_purge_data_no_config(self):
        publisher = self.make_publisher("ubuntu", "daily")
        self.assertIsNone(publisher.get_purge_data("daily", "purge-days"))

    def test_get_purge_data(self):
        publisher = self.make_publisher("ubuntu", "daily")
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print(dedent("""\
                # comment

                daily 1
                daily-live 2"""), file=purge_days)
        self.assertEqual(1, publisher.get_purge_data("daily", "purge-days"))
        self.assertEqual(2, publisher.get_purge_data(
            "daily-live", "purge-days"))
        self.assertIsNone(publisher.get_purge_data("dvd", "purge-days"))

    def test_get_purge_count_no_config(self):
        publisher = self.make_publisher("ubuntu", "daily")
        self.assertIsNone(publisher.get_purge_data("daily", "purge-count"))

    def test_get_purge_count(self):
        publisher = self.make_publisher("ubuntu", "daily")
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-count")) as purge_count:
            print(dedent("""\
                # comment

                daily 1
                daily-live 2"""), file=purge_count)
        self.assertEqual(1, publisher.get_purge_data("daily", "purge-count"))
        self.assertEqual(2, publisher.get_purge_data(
            "daily-live", "purge-count"))
        self.assertIsNone(publisher.get_purge_data("dvd", "purge-count"))

    @mock.patch("time.time", return_value=date_to_time("20130321"))
    def test_purge_removes_old(self, *args):
        publisher = self.make_publisher("ubuntu", "daily")
        for name in "20130318", "20130319", "20130320", "20130321":
            touch(os.path.join(publisher.publish_base, name, "file"))
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print("daily 1", file=purge_days)
        self.capture_logging()
        publisher.purge()
        project = "ubuntu"
        purge_desc = project
        self.assertLogEqual([
            "Purging %s/daily images older than 1 day ..." % project,
            "Purging %s/daily/20130319" % purge_desc,
            "Purging %s/daily/20130318" % purge_desc,
        ])
        self.assertCountEqual(
            ["20130320", "20130321"], os.listdir(publisher.publish_base))

    @mock.patch("time.time", return_value=date_to_time("20130321"))
    def test_purge_preserves_pending(self, *args):
        publisher = self.make_publisher("ubuntu", "daily")
        for name in "20130319", "20130320", "20130321":
            touch(os.path.join(publisher.publish_base, name, "file"))
        os.symlink("20130319", os.path.join(publisher.publish_base, "pending"))
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print("daily 1", file=purge_days)
        self.capture_logging()
        publisher.purge()
        project = "ubuntu"
        self.assertLogEqual([
            "Purging %s/daily images older than 1 day ..." % project,
        ])
        self.assertCountEqual(
            ["20130319", "20130320", "20130321", "pending"],
            os.listdir(publisher.publish_base))

    @mock.patch("time.time", return_value=date_to_time("20130321"))
    def test_purge_preserves_current_symlink(self, *args):
        publisher = self.make_publisher("ubuntu", "daily")
        for name in "20130319", "20130320", "20130321":
            touch(os.path.join(publisher.publish_base, name, "file"))
        os.symlink("20130319", os.path.join(publisher.publish_base, "current"))
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print("daily 1", file=purge_days)
        self.capture_logging()
        publisher.purge()
        project = "ubuntu"
        self.assertLogEqual([
            "Purging %s/daily images older than 1 day ..." % project,
        ])
        self.assertCountEqual(
            ["20130319", "20130320", "20130321", "current"],
            os.listdir(publisher.publish_base))

    @mock.patch("time.time", return_value=date_to_time("20130321"))
    def test_purge_preserves_symlinks_in_current_directory(self, *args):
        publisher = self.make_publisher("ubuntu", "daily")
        for name in "20130318", "20130319", "20130320", "20130321":
            touch(os.path.join(publisher.publish_base, name, "file"))
        publish_current = os.path.join(publisher.publish_base, "current")
        os.makedirs(publish_current)
        os.symlink(
            os.path.join(os.pardir, "20130319", "bionic-desktop-i386.iso"),
            os.path.join(publish_current, "bionic-desktop-i386.iso"))
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print("daily 1", file=purge_days)
        self.capture_logging()
        publisher.purge()
        project = "ubuntu"
        purge_desc = project
        self.assertLogEqual([
            "Purging %s/daily images older than 1 day ..." % project,
            "Purging %s/daily/20130318" % purge_desc,
        ])
        self.assertCountEqual(
            ["20130319", "20130320", "20130321", "current"],
            os.listdir(publisher.publish_base))

    @mock.patch("time.time", return_value=date_to_time("20130321"))
    def test_purge_preserves_manual(self, *args):
        publisher = self.make_publisher("ubuntu", "daily")
        for name in "20130319", "20130320", "20130321":
            touch(os.path.join(publisher.publish_base, name, "file"))
        os.symlink("20130319", os.path.join(publisher.publish_base, "manual"))
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print("daily 1", file=purge_days)
        self.capture_logging()
        publisher.purge()
        project = "ubuntu"
        self.assertLogEqual([
            "Purging %s/daily images older than 1 day ..." % project,
        ])
        self.assertCountEqual(
            ["20130319", "20130320", "20130321", "manual"],
            os.listdir(publisher.publish_base))

    @mock.patch("time.time", return_value=date_to_time("20130321"))
    def test_purge_preserves_manual_slash(self, *args):
        # Special test-case to make sure that if the manual symlink is
        # still correct but has a trailing slash, we don't purge it.
        publisher = self.make_publisher("ubuntu", "daily")
        for name in "20130319", "20130320", "20130321":
            touch(os.path.join(publisher.publish_base, name, "file"))
        os.symlink(
            "20130319/", os.path.join(publisher.publish_base, "manual"))
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print("daily 1", file=purge_days)
        self.capture_logging()
        publisher.purge()
        project = "ubuntu"
        self.assertLogEqual([
            "Purging %s/daily images older than 1 day ..." % project,
        ])
        self.assertCountEqual(
            ["20130319", "20130320", "20130321", "manual"],
            os.listdir(publisher.publish_base))

    @mock.patch("time.time", return_value=date_to_time("20130321"))
    def test_purge_removes_symlinks(self, *args):
        publisher = self.make_publisher("ubuntu", "daily")
        touch(os.path.join(publisher.publish_base, "20130319", "file"))
        os.symlink(
            "20130319", os.path.join(publisher.publish_base, "20130319.1"))
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print("daily 1", file=purge_days)
        self.capture_logging()
        publisher.purge()
        project = "ubuntu"
        purge_desc = project
        self.assertLogEqual([
            "Purging %s/daily images older than 1 day ..." % project,
            "Purging %s/daily/20130319.1" % purge_desc,
            "Purging %s/daily/20130319" % purge_desc,
        ])
        self.assertEqual([], os.listdir(publisher.publish_base))

    def test_purge_leaves_count(self, *args):
        publisher = self.make_publisher("ubuntu", "daily")
        for name in "20130318", "20130319", "20130320", "20130321":
            touch(os.path.join(publisher.publish_base, name, "file"))
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-count")) as purge_count:
            print("daily 2", file=purge_count)
        self.capture_logging()
        publisher.purge()
        project = "ubuntu"
        purge_desc = project
        self.assertLogEqual([
            "Purging %s/daily images to leave only the latest 2 images "
            "..." % project,
            "Purging %s/daily/20130319" % purge_desc,
            "Purging %s/daily/20130318" % purge_desc,
        ])
        self.assertCountEqual(
            ["20130320", "20130321"], os.listdir(publisher.publish_base))

    @mock.patch("time.time", return_value=date_to_time("20130321"))
    def test_purge_both_days_and_count_raises(self, *args):
        publisher = self.make_publisher("ubuntu", "daily")
        for name in "20130321", "20130321.1", "20130321.2", "20130321.3":
            touch(os.path.join(publisher.publish_base, name, "file"))
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print("daily 1", file=purge_days)
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-count")) as purge_count:
            print("daily 3", file=purge_count)
        project = "ubuntu"
        self.capture_logging()
        self.assertRaisesRegex(
            Exception, r"Both purge-days and purge-count are defined for "
                       "%s/daily. Such scenario is currently "
                       "unsupported." % project,
            publisher.purge)
        self.assertCountEqual(
            ["20130321", "20130321.1", "20130321.2", "20130321.3"],
            os.listdir(publisher.publish_base))

    @mock.patch("time.time", return_value=date_to_time("20130321"))
    def test_purge_no_purge(self, *args):
        publisher = self.make_publisher("ubuntu", "daily")
        for name in "20130318", "20130319", "20130320", "20130321":
            touch(os.path.join(publisher.publish_base, name, "file"))
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print("daily 0", file=purge_days)
        self.capture_logging()
        publisher.purge()
        project = "ubuntu"
        self.assertLogEqual([
            "Not purging images for %s/daily" % project,
        ])
        self.assertCountEqual(
            ["20130318", "20130319", "20130320", "20130321"],
            os.listdir(publisher.publish_base))


class TestFullReleaseTree(TestCase):
    def setUp(self):
        super(TestFullReleaseTree, self).setUp()
        self.use_temp_dir()
        self.config = Config(read=False)
        self.tree = FullReleaseTree(self.config, self.temp_dir)

    def test_tree_suffix(self):
        self.assertEqual(
            "/ports", self.tree.tree_suffix("ubuntu-server/ports/daily"))
        self.assertEqual("", self.tree.tree_suffix("ubuntu-server/daily"))
        self.assertEqual(
            "", self.tree.tree_suffix("ubuntu-server/daily-preinstalled"))
        self.assertEqual("/ports", self.tree.tree_suffix("ports/daily"))
        self.assertEqual("", self.tree.tree_suffix("daily"))


class TestSimpleReleaseTree(TestCase):
    def setUp(self):
        super(TestSimpleReleaseTree, self).setUp()
        self.use_temp_dir()
        self.config = Config(read=False)
        self.tree = SimpleReleaseTree(self.config, self.temp_dir)

    def test_default_directory(self):
        self.config.root = self.temp_dir
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "simple"),
            SimpleReleaseTree(self.config).directory)

    def test_tree_suffix(self):
        self.assertEqual(
            "/ports", self.tree.tree_suffix("ubuntu-server/ports/daily"))
        self.assertEqual("", self.tree.tree_suffix("ubuntu-server/daily"))
        self.assertEqual(
            "", self.tree.tree_suffix("ubuntu-server/daily-preinstalled"))
        self.assertEqual("/ports", self.tree.tree_suffix("ports/daily"))
        self.assertEqual("", self.tree.tree_suffix("daily"))

    def test_get_publisher(self):
        publisher = self.tree.get_publisher("daily-live", "yes", "beta-1")
        self.assertIsInstance(publisher, SimpleReleasePublisher)
        self.assertEqual("daily-live", publisher.image_type)
        self.assertEqual("yes", publisher.official)
        self.assertEqual("beta-1", publisher.status)

    def test_name_to_series(self):
        self.assertEqual(
            "warty", self.tree.name_to_series("ubuntu-4.10-install-i386.iso"))
        self.assertRaises(ValueError, self.tree.name_to_series, "foo-bar.iso")

    def test_path_to_manifest(self):
        iso = "kubuntu/.pool/kubuntu-5.04-install-i386.iso"
        touch(os.path.join(self.temp_dir, iso))
        self.assertEqual(
            "kubuntu\thoary\t/%s\t0" % iso, self.tree.path_to_manifest(iso))

    def test_manifest_files_prefers_non_pool(self):
        pool = os.path.join(self.temp_dir, ".pool")
        touch(os.path.join(pool, "ubuntu-4.10-install-i386.iso"))
        dist = os.path.join(self.temp_dir, "warty")
        os.mkdir(dist)
        os.symlink(
            os.path.join(os.pardir, ".pool", "ubuntu-4.10-install-i386.iso"),
            os.path.join(dist, "ubuntu-4.10-install-i386.iso"))
        self.assertEqual(
            ["warty/ubuntu-4.10-install-i386.iso"],
            list(self.tree.manifest_files()))

    def test_manifest_files_includes_non_duplicates_in_pool(self):
        pool = os.path.join(self.temp_dir, ".pool")
        touch(os.path.join(pool, "ubuntu-4.10-install-i386.iso"))
        touch(os.path.join(pool, "ubuntu-4.10-install-amd64.iso"))
        dist = os.path.join(self.temp_dir, "warty")
        os.mkdir(dist)
        os.symlink(
            os.path.join(os.pardir, ".pool", "ubuntu-4.10-install-i386.iso"),
            os.path.join(dist, "ubuntu-4.10-install-i386.iso"))
        self.assertEqual([
            "warty/ubuntu-4.10-install-i386.iso",
            ".pool/ubuntu-4.10-install-amd64.iso",
        ], list(self.tree.manifest_files()))

    def test_manifest(self):
        pool = os.path.join(self.temp_dir, "kubuntu", ".pool")
        touch(os.path.join(pool, "kubuntu-5.04-install-i386.iso"))
        touch(os.path.join(pool, "kubuntu-5.04-live-i386.iso"))
        dist = os.path.join(self.temp_dir, "kubuntu", "hoary")
        os.makedirs(dist)
        os.symlink(
            os.path.join(os.pardir, ".pool", "kubuntu-5.04-install-i386.iso"),
            os.path.join(dist, "kubuntu-5.04-install-i386.iso"))
        os.symlink(
            os.path.join(os.pardir, ".pool", "kubuntu-5.04-live-i386.iso"),
            os.path.join(dist, "kubuntu-5.04-live-i386.iso"))
        self.assertEqual([
            "kubuntu\thoary\t/kubuntu/hoary/kubuntu-5.04-install-i386.iso\t0",
            "kubuntu\thoary\t/kubuntu/hoary/kubuntu-5.04-live-i386.iso\t0",
        ], self.tree.manifest())


class TestTorrentTree(TestCase):
    def setUp(self):
        super(TestTorrentTree, self).setUp()
        self.use_temp_dir()
        self.config = Config(read=False)
        self.tree = TorrentTree(self.config, self.temp_dir)

    def test_default_directory(self):
        self.config.root = self.temp_dir
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "torrent"),
            TorrentTree(self.config).directory)


class TestReleasePublisherMixin:
    def test_daily_dir_normal(self):
        self.config["PROJECT"] = "ubuntu"
        publisher = self.get_publisher()
        path = os.path.join(self.temp_dir, "www", "full", "daily", "20130327")
        os.makedirs(path)
        self.assertEqual(
            path, publisher.daily_dir("daily", "20130327", "alternate"))
        self.config["PROJECT"] = "kubuntu"
        publisher = self.get_publisher()
        path = os.path.join(
            self.temp_dir, "www", "full", "kubuntu", "daily", "20130327")
        os.makedirs(path)
        self.assertEqual(
            path, publisher.daily_dir("daily", "20130327", "alternate"))

    def test_daily_dir_preinstalled(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["SUBPROJECT"] = "desktop-preinstalled"
        publisher = self.get_publisher()
        path = os.path.join(
            self.temp_dir, "www", "full", "ubuntu", "daily-preinstalled",
            "20130327")
        os.makedirs(path)
        self.assertEqual(
            path, publisher.daily_dir("ubuntu",
                                      "ubuntu/daily-preinstalled/20130327",
                                      "daily-preinstalled"))

    def test_daily_dir_path_in_date(self):
        self.config["PROJECT"] = "ubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "ubuntu-server", "daily",
                "20130327"),
            self.get_publisher().daily_dir(
                "daily", "ubuntu-server/daily/20130327", "server"))
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "ubuntu-server",
                "daily-preinstalled", "20130327"),
            self.get_publisher().daily_dir(
                "daily-preinstalled",
                "ubuntu-server/daily-preinstalled/20130327", "server"))

    def test_daily_dir_source(self):
        self.config["PROJECT"] = "ubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "daily", "20130327", "source"),
            self.get_publisher().daily_dir("daily", "20130327", "src"))

    def test_daily_base(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "bionic", "daily", "20130327",
                "i386"),
            self.get_publisher().daily_base(
                "bionic/daily", "20130327", "wubi", "i386"))
        self.config["DIST"] = "bionic"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "daily-live", "20130327",
                "bionic-desktop-i386"),
            self.get_publisher().daily_base(
                "daily-live", "20130327", "desktop", "i386"))

    def test_version(self):
        self.config["DIST"] = "raring"
        self.assertEqual("13.04", self.get_publisher().version)
        self.config["DIST"] = "dapper"
        self.assertEqual("6.06.2", self.get_publisher().version)

    def test_do(self):
        path = os.path.join(self.temp_dir, "path")
        self.capture_logging()
        self.get_publisher(dry_run=True).do("touch %s" % path, touch, path)
        self.assertLogEqual(["touch %s" % path])
        self.assertFalse(os.path.exists(path))
        self.capture_logging()
        self.get_publisher().do("touch %s" % path, touch, path)
        self.assertLogEqual([])
        self.assertTrue(os.path.exists(path))

    def test_remove_checksum(self):
        sha256sums_path = os.path.join(self.temp_dir, "SHA256SUMS")
        with mkfile(sha256sums_path) as sha256sums:
            print("checksum  path", file=sha256sums)
        self.capture_logging()
        self.get_publisher(dry_run=True).remove_checksum(self.temp_dir, "path")
        self.assertLogEqual(
            ["checksum-remove --no-sign %s path" % self.temp_dir])
        with open(sha256sums_path) as sha256sums:
            self.assertEqual("checksum  path\n", sha256sums.read())
        self.capture_logging()
        self.get_publisher().remove_checksum(self.temp_dir, "path")
        self.assertLogEqual([])
        self.assertFalse(os.path.exists(sha256sums_path))

    def test_copy(self):
        old_path = os.path.join(self.temp_dir, "old")
        new_path = os.path.join(self.temp_dir, "new")
        with mkfile(old_path) as old:
            print("sentinel", file=old)
        self.get_publisher().copy(old_path, new_path)
        with open(new_path) as new:
            self.assertEqual("sentinel\n", new.read())

    def test_symlink(self):
        pool_path = os.path.join(self.temp_dir, ".pool", "foo.iso")
        touch(pool_path)
        dist_path = os.path.join(self.temp_dir, "bionic", "foo.iso")
        os.makedirs(os.path.dirname(dist_path))
        self.get_publisher().symlink(pool_path, dist_path)
        self.assertEqual(
            os.path.join(os.pardir, ".pool", "foo.iso"),
            os.readlink(dist_path))

    def test_hardlink(self):
        pool_path = os.path.join(self.temp_dir, ".pool", "foo.iso")
        touch(pool_path)
        dist_path = os.path.join(self.temp_dir, "bionic", "foo.iso")
        os.makedirs(os.path.dirname(dist_path))
        self.get_publisher().hardlink(pool_path, dist_path)
        self.assertEqual(os.stat(pool_path), os.stat(dist_path))

    def test_remove(self):
        path = os.path.join(self.temp_dir, "path")
        touch(path)
        self.get_publisher().remove(path)
        self.assertFalse(os.path.exists(path))

    def test_remove_tree(self):
        path = os.path.join(self.temp_dir, "dir", "name")
        touch(path)
        self.get_publisher().remove_tree(os.path.dirname(path))
        self.assertFalse(os.path.exists(os.path.dirname(path)))

    def test_mkemptydir(self):
        path = os.path.join(self.temp_dir, "dir")
        touch(os.path.join(path, "name"))
        self.get_publisher().mkemptydir(path)
        self.assertEqual([], os.listdir(path))

    # TODO: checksum_directory untested

    def test_want_manifest(self):
        path = os.path.join(self.temp_dir, "foo.manifest")
        self.assertTrue(self.get_publisher().want_manifest("desktop", path))
        self.assertFalse(self.get_publisher().want_manifest("dvd", path))
        touch(path)
        self.assertTrue(self.get_publisher().want_manifest("dvd", path))
        self.assertFalse(self.get_publisher().want_manifest("alternate", path))


def call_mktorrent_zsyncmake(command, *args, **kwargs):
    if command[0] == "mktorrent":
        touch("%s.torrent" % command[-1])
    elif command[0] == "zsyncmake":
        for i in range(1, len(command)):
            if command[i] == "-o":
                touch(command[i + 1])
                break
    return 0


class TestFullReleasePublisher(TestCase, TestReleasePublisherMixin):
    def setUp(self):
        super(TestFullReleasePublisher, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()

    def get_tree(self, official="named"):
        return Tree.get_release(self.config, official)

    def get_publisher(self, tree=None, image_type="daily", official="named",
                      **kwargs):
        if tree is None:
            tree = self.get_tree(official=official)
        return tree.get_publisher(image_type, official, **kwargs)

    def test_want_dist(self):
        self.assertFalse(self.get_publisher(official="named").want_dist)
        self.assertFalse(self.get_publisher(official="no").want_dist)

    def test_want_pool(self):
        self.assertFalse(self.get_publisher(official="named").want_pool)
        self.assertFalse(self.get_publisher(official="no").want_pool)

    def test_want_full(self):
        self.assertTrue(self.get_publisher(official="named").want_full)
        self.assertTrue(self.get_publisher(official="no").want_full)

    def test_target_dir(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "releases", "bionic", "release"),
            self.get_publisher().target_dir("daily", "20130327", "alternate"))
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "kubuntu", "releases", "bionic",
                "release", "source"),
            self.get_publisher().target_dir("daily", "20130327", "src"))

    def test_version_link(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "full", "releases", "13.04"),
            self.get_publisher().version_link("daily"))
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "kubuntu", "releases", "13.04"),
            self.get_publisher().version_link("daily"))

    def test_torrent_dir(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "torrent", "releases",
                "bionic", "release", "desktop"),
            self.get_publisher().torrent_dir("daily-live", "desktop"))
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "torrent", "kubuntu", "releases",
                "bionic", "beta-2", "desktop"),
            self.get_publisher(status="beta-2").torrent_dir(
                "daily-live", "desktop"))

    def test_want_torrent(self):
        self.assertTrue(
            self.get_publisher(official="named").want_torrent("desktop"))
        self.assertTrue(
            self.get_publisher(official="no").want_torrent("desktop"))
        self.assertFalse(self.get_publisher().want_torrent("src"))

    @mock.patch("subprocess.check_call")
    def test_make_torrents(self, mock_check_call):
        self.config["CAPPROJECT"] = "Ubuntu"
        paths = [
            os.path.join(
                self.temp_dir, "dir", "ubuntu-13.04-desktop-%s.iso" % arch)
            for arch in ("amd64", "i386")]
        for path in paths:
            touch(path)
        publisher = self.get_publisher(image_type="daily-live")
        self.capture_logging()
        publisher.make_torrents(
            os.path.join(self.temp_dir, "dir"), "ubuntu-13.04")
        self.assertLogEqual(
            ["Creating torrent for %s ..." % path for path in paths])
        command_base = [
            "mktorrent", "-a", "https://torrent.ubuntu.com/announce",
            "--comment", "Ubuntu CD cdimage.ubuntu.com",
            "--output",
        ]
        mock_check_call.assert_has_calls([
            mock.call(command_base + ["%s.torrent" % path, path],
                      stdout=mock.ANY)
            for path in paths])

    def test_publish_release_prefixes(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.assertEqual(
            ("bionic", "bionic-beta2"),
            self.get_publisher(
                official="no", status="beta-2").publish_release_prefixes())
        self.config["PROJECT"] = "kubuntu"
        self.config["DIST"] = "dapper"
        self.assertEqual(
            ("kubuntu-6.06.2", "kubuntu-6.06.2"),
            self.get_publisher(official="named").publish_release_prefixes())

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_mktorrent_zsyncmake)
    def test_publish_release_arch_ubuntu_desktop_named(self, mock_call, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "raring"
        daily_dir = os.path.join(
            self.temp_dir, "www", "full", "daily-live", "20130327")
        touch(os.path.join(daily_dir, "raring-desktop-i386.iso"))
        touch(os.path.join(daily_dir, "raring-desktop-i386.manifest"))
        touch(os.path.join(daily_dir, "raring-desktop-i386.iso.zsync"))
        target_dir = os.path.join(
            self.temp_dir, "www", "full", "releases", "raring", "rc")
        torrent_dir = os.path.join(
            self.temp_dir, "www", "torrent", "releases", "raring", "rc",
            "desktop")
        osextras.ensuredir(target_dir)
        osextras.ensuredir(torrent_dir)
        self.capture_logging()
        publisher = self.get_publisher(official="named", status="rc")
        publisher.publish_release_arch(
            "daily-live", "20130327", "desktop", "i386")
        self.assertLogEqual([
            "Copying desktop-i386 image ...",
            "Making i386 zsync metafile ...",
            "Creating torrent for %s/ubuntu-13.04-rc-desktop-i386.iso ..." %
            target_dir,
        ])
        self.assertCountEqual([
            "ubuntu-13.04-rc-desktop-i386.iso",
            "ubuntu-13.04-rc-desktop-i386.iso.torrent",
            "ubuntu-13.04-rc-desktop-i386.iso.zsync",
            "ubuntu-13.04-rc-desktop-i386.manifest",
        ], os.listdir(target_dir))
        target_base = os.path.join(target_dir, "ubuntu-13.04-rc-desktop-i386")
        self.assertFalse(os.path.islink("%s.iso" % target_base))
        self.assertFalse(os.path.islink("%s.manifest" % target_base))
        self.assertEqual(2, mock_call.call_count)
        mock_call.assert_has_calls([
            mock.call([
                "zsyncmake", "-o", "%s.iso.zsync" % target_base,
                "-u", "ubuntu-13.04-rc-desktop-i386.iso",
                "%s.iso" % target_base,
            ]),
            mock.call([
                "mktorrent", mock.ANY, mock.ANY,
                "--comment", "Ubuntu CD cdimage.ubuntu.com",
                "--output", "%s.iso.torrent" % target_base,
                "%s.iso" % target_base,
            ], stdout=mock.ANY),
        ])
        self.assertCountEqual([
            "ubuntu-13.04-rc-desktop-i386.iso",
            "ubuntu-13.04-rc-desktop-i386.iso.torrent",
        ], os.listdir(torrent_dir))
        torrent_base = os.path.join(
            torrent_dir, "ubuntu-13.04-rc-desktop-i386")
        self.assertEqual(
            os.stat("%s.iso" % target_base), os.stat("%s.iso" % torrent_base))
        self.assertEqual(
            os.stat("%s.iso.torrent" % target_base),
            os.stat("%s.iso.torrent" % torrent_base))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_mktorrent_zsyncmake)
    def test_publish_release_arch_ubuntu_desktop_no(self, mock_call, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "bionic"
        daily_dir = os.path.join(
            self.temp_dir, "www", "full", "daily-live", "20130327")
        touch(os.path.join(daily_dir, "bionic-desktop-i386.iso"))
        touch(os.path.join(daily_dir, "bionic-desktop-i386.manifest"))
        touch(os.path.join(daily_dir, "bionic-desktop-i386.iso.zsync"))
        target_dir = os.path.join(
            self.temp_dir, "www", "full", "releases", "bionic", "rc")
        torrent_dir = os.path.join(
            self.temp_dir, "www", "torrent", "releases", "bionic", "rc",
            "desktop")
        osextras.ensuredir(target_dir)
        osextras.ensuredir(torrent_dir)
        self.capture_logging()
        publisher = self.get_publisher(official="no", status="rc")
        publisher.publish_release_arch(
            "daily-live", "20130327", "desktop", "i386")
        self.assertLogEqual([
            "Copying desktop-i386 image ...",
            "Creating torrent for %s/bionic-desktop-i386.iso ..." % target_dir,
        ])
        self.assertCountEqual([
            "bionic-desktop-i386.iso", "bionic-desktop-i386.iso.torrent",
            "bionic-desktop-i386.iso.zsync", "bionic-desktop-i386.manifest",
        ], os.listdir(target_dir))
        target_base = os.path.join(target_dir, "bionic-desktop-i386")
        self.assertFalse(os.path.islink("%s.iso" % target_base))
        self.assertFalse(os.path.islink("%s.manifest" % target_base))
        mock_call.assert_called_once_with([
            "mktorrent", mock.ANY, mock.ANY,
            "--comment", "Ubuntu CD cdimage.ubuntu.com",
            "--output", "%s.iso.torrent" % target_base,
            "%s.iso" % target_base,
        ], stdout=mock.ANY)
        self.assertCountEqual([
            "bionic-desktop-i386.iso", "bionic-desktop-i386.iso.torrent",
        ], os.listdir(torrent_dir))
        torrent_base = os.path.join(torrent_dir, "bionic-desktop-i386")
        self.assertEqual(
            os.stat("%s.iso" % target_base), os.stat("%s.iso" % torrent_base))
        self.assertEqual(
            os.stat("%s.iso.torrent" % target_base),
            os.stat("%s.iso.torrent" % torrent_base))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_mktorrent_zsyncmake)
    def test_publish_release_arch_ubuntu_desktop_inteliot(self, mock_call,
                                                          *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "jammy"
        self.config["ARCHES"] = "amd64+intel-iot"
        daily_dir = os.path.join(
            self.temp_dir, "www", "full", "daily-live", "20130327")
        touch(os.path.join(daily_dir, "jammy-desktop-amd64+intel-iot.iso"))
        touch(os.path.join(daily_dir,
                           "jammy-desktop-amd64+intel-iot.manifest"))
        touch(os.path.join(daily_dir,
                           "jammy-desktop-amd64+intel-iot.iso.zsync"))
        target_dir = os.path.join(
            self.temp_dir, "www", "full", "releases", "jammy", "release",
            "inteliot")
        osextras.ensuredir(target_dir)
        self.capture_logging()
        publisher = self.get_publisher(official="inteliot")
        publisher.publish_release_arch(
            "daily-live", "20130327", "desktop", "amd64+intel-iot")
        self.assertLogEqual([
            "Copying desktop-amd64+intel-iot image ...",
            "Making amd64+intel-iot zsync metafile ...",
        ])
        self.assertCountEqual([
            "ubuntu-22.04-desktop-amd64+intel-iot.iso",
            "ubuntu-22.04-desktop-amd64+intel-iot.iso.zsync",
            "ubuntu-22.04-desktop-amd64+intel-iot.manifest",
        ], os.listdir(target_dir))
        target_base = os.path.join(target_dir,
                                   "ubuntu-22.04-desktop-amd64+intel-iot")
        self.assertFalse(os.path.islink("%s.iso" % target_base))
        self.assertFalse(os.path.islink("%s.manifest" % target_base))
        self.assertEqual(1, mock_call.call_count)
        mock_call.assert_has_calls([
            mock.call([
                "zsyncmake", "-o", "%s.iso.zsync" % target_base,
                "-u", "ubuntu-22.04-desktop-amd64+intel-iot.iso",
                "%s.iso" % target_base,
            ]),
        ])

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_mktorrent_zsyncmake)
    def test_publish_release_kubuntu_desktop_named(self, mock_call, *args):
        self.config["PROJECT"] = "kubuntu"
        self.config["CAPPROJECT"] = "Kubuntu"
        series = Series.latest()
        try:
            version = series.pointversion
        except Exception:
            version = series.version
        self.config["DIST"] = series
        self.config["ARCHES"] = "amd64 i386"
        daily_dir = os.path.join(
            self.temp_dir, "www", "full", "kubuntu", "daily-live", "20130327")
        touch(os.path.join(daily_dir, "%s-desktop-amd64.iso" % series))
        touch(os.path.join(daily_dir, "%s-desktop-amd64.manifest" % series))
        touch(os.path.join(daily_dir, "%s-desktop-amd64.iso.zsync" % series))
        touch(os.path.join(daily_dir, "%s-desktop-i386.iso" % series))
        touch(os.path.join(daily_dir, "%s-desktop-i386.manifest" % series))
        touch(os.path.join(daily_dir, "%s-desktop-i386.iso.zsync" % series))
        target_dir = os.path.join(
            self.temp_dir, "www", "full", "kubuntu", "releases", series.name,
            "release")
        torrent_dir = os.path.join(
            self.temp_dir, "www", "torrent", "kubuntu", "releases",
            series.name, "release", "desktop")
        self.capture_logging()
        publisher = self.get_publisher(official="named")
        publisher.publish_release("daily-live", "20130327", "desktop")
        self.assertLogEqual([
            "Constructing release trees ...",
            "Copying desktop-amd64 image ...",
            "Making amd64 zsync metafile ...",
            "Creating torrent for %s/kubuntu-%s-desktop-amd64.iso ..." % (
                target_dir, version),
            "Copying desktop-i386 image ...",
            "Making i386 zsync metafile ...",
            "Creating torrent for %s/kubuntu-%s-desktop-i386.iso ..." % (
                target_dir, version),
            "Checksumming full tree ...",
            "No keys found; not signing images.",
            "Refreshing simplestreams...",
            "No keys found; not signing images.",
            "No keys found; not signing images.",
            "Done!  Remember to sync-mirrors after checking that everything "
            "is OK.",
        ])
        self.assertCountEqual([
            ".htaccess", "FOOTER.html", "HEADER.html",
            "SHA256SUMS",
            "kubuntu-%s-desktop-amd64.iso" % version,
            "kubuntu-%s-desktop-amd64.iso.torrent" % version,
            "kubuntu-%s-desktop-amd64.iso.zsync" % version,
            "kubuntu-%s-desktop-amd64.manifest" % version,
            "kubuntu-%s-desktop-i386.iso" % version,
            "kubuntu-%s-desktop-i386.iso.torrent" % version,
            "kubuntu-%s-desktop-i386.iso.zsync" % version,
            "kubuntu-%s-desktop-i386.manifest" % version,
        ], os.listdir(target_dir))
        self.assertCountEqual([
            "kubuntu-%s-desktop-amd64.iso" % version,
            "kubuntu-%s-desktop-amd64.iso.torrent" % version,
            "kubuntu-%s-desktop-i386.iso" % version,
            "kubuntu-%s-desktop-i386.iso.torrent" % version,
        ], os.listdir(torrent_dir))
        self.assertFalse(os.path.exists(os.path.join(
            self.temp_dir, "www", "simple")))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_mktorrent_zsyncmake)
    def test_publish_release_test_no_purge_torrent(self, mock_call, *args):
        self.config["PROJECT"] = "kubuntu"
        self.config["CAPPROJECT"] = "Kubuntu"
        self.config["CDIMAGE_NO_PURGE"] = "1"
        series = Series.latest()
        try:
            version = series.pointversion
        except Exception:
            version = series.version
        self.config["DIST"] = series
        self.config["ARCHES"] = "amd64 i386"
        daily_dir = os.path.join(
            self.temp_dir, "www", "full", "kubuntu", "daily-live", "20130327")
        touch(os.path.join(daily_dir, "%s-desktop-amd64.iso" % series))
        touch(os.path.join(daily_dir, "%s-desktop-amd64.manifest" % series))
        touch(os.path.join(daily_dir, "%s-desktop-amd64.iso.zsync" % series))
        touch(os.path.join(daily_dir, "%s-desktop-i386.iso" % series))
        touch(os.path.join(daily_dir, "%s-desktop-i386.manifest" % series))
        touch(os.path.join(daily_dir, "%s-desktop-i386.iso.zsync" % series))
        torrent_dir = os.path.join(
            self.temp_dir, "www", "torrent", "kubuntu", "releases",
            series.name, "release", "desktop")
        # Add existing published torrent set to see if it gets purged
        touch(os.path.join(
            torrent_dir, "kubuntu-%s-desktop-arm64.iso.torrent" % version))
        touch(os.path.join(
            torrent_dir, "kubuntu-%s-desktop-arm64.iso" % version))
        self.capture_logging()
        publisher = self.get_publisher(official="named")
        publisher.publish_release("daily-live", "20130327", "desktop")
        self.assertCountEqual([
            "kubuntu-%s-desktop-amd64.iso" % version,
            "kubuntu-%s-desktop-amd64.iso.torrent" % version,
            "kubuntu-%s-desktop-i386.iso" % version,
            "kubuntu-%s-desktop-i386.iso.torrent" % version,
            "kubuntu-%s-desktop-arm64.iso" % version,
            "kubuntu-%s-desktop-arm64.iso.torrent" % version,
        ], os.listdir(torrent_dir))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_mktorrent_zsyncmake)
    def test_publish_release_simplestreams(self, mock_call, *args):
        self.config["PROJECT"] = "kubuntu"
        self.config["CAPPROJECT"] = "Kubuntu"
        series = Series.latest()
        try:
            version = series.pointversion
        except Exception:
            version = series.version
        self.config["DIST"] = series
        self.config["ARCHES"] = "amd64"
        self.config["SIMPLESTREAMS"] = "1"
        daily_dir = os.path.join(
            self.temp_dir, "www", "full", "kubuntu", "daily-live", "20130327")
        touch(os.path.join(daily_dir, "%s-desktop-amd64.iso" % series))
        touch(os.path.join(daily_dir, "%s-desktop-amd64.manifest" % series))
        touch(os.path.join(daily_dir, "%s-desktop-amd64.iso.zsync" % series))
        target_dir = os.path.join(
            self.temp_dir, "www", "full", "kubuntu", "releases", series.name,
            "release")
        streams_dir = os.path.join(
            self.temp_dir, "www", "full", "releases", "streams", "v1")
        self.capture_logging()
        publisher = self.get_publisher(official="named")
        publisher.publish_release("daily-live", "20130327", "desktop")
        self.assertLogEqual([
            "Constructing release trees ...",
            "Copying desktop-amd64 image ...",
            "Making amd64 zsync metafile ...",
            "Creating torrent for %s/kubuntu-%s-desktop-amd64.iso ..." % (
                target_dir, version),
            "Checksumming full tree ...",
            "No keys found; not signing images.",
            "Refreshing simplestreams...",
            "No keys found; not signing images.",
            "No keys found; not signing images.",
            "Done!  Remember to sync-mirrors after checking that everything "
            "is OK.",
        ])
        # Double check if we still published everything
        self.assertCountEqual([
            ".htaccess", "FOOTER.html", "HEADER.html",
            "SHA256SUMS",
            "kubuntu-%s-desktop-amd64.iso" % version,
            "kubuntu-%s-desktop-amd64.iso.torrent" % version,
            "kubuntu-%s-desktop-amd64.iso.zsync" % version,
            "kubuntu-%s-desktop-amd64.manifest" % version,
        ], os.listdir(target_dir))
        # ...and that the streams got generated as expected
        self.assertCountEqual([
            "index.json", "com.ubuntu.cdimage:kubuntu.json"
        ], os.listdir(streams_dir))


class TestSimpleReleasePublisher(TestCase, TestReleasePublisherMixin):
    def setUp(self):
        super(TestSimpleReleasePublisher, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()

    def get_tree(self, official="yes"):
        return Tree.get_release(self.config, official)

    def get_publisher(self, tree=None, image_type="daily", official="yes",
                      **kwargs):
        if tree is None:
            tree = self.get_tree(official=official)
        return tree.get_publisher(image_type, official, **kwargs)

    def test_want_dist(self):
        self.assertTrue(self.get_publisher(official="yes").want_dist)
        self.assertFalse(self.get_publisher(official="poolonly").want_dist)

    def test_want_pool(self):
        self.assertTrue(self.get_publisher(official="yes").want_pool)
        self.assertTrue(self.get_publisher(official="poolonly").want_pool)

    def test_want_full(self):
        self.assertFalse(self.get_publisher(official="yes").want_full)
        self.assertFalse(self.get_publisher(official="poolonly").want_full)

    def test_target_dir(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "simple", "bionic"),
            self.get_publisher().target_dir("daily", "20130327", "alternate"))
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "simple", "kubuntu", "bionic", "source"),
            self.get_publisher().target_dir("daily", "20130327", "src"))

    def test_version_link(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "simple", "13.04"),
            self.get_publisher().version_link("daily"))
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "simple", "kubuntu", "13.04"),
            self.get_publisher().version_link("daily"))

    def test_pool_dir(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "simple", ".pool"),
            self.get_publisher().pool_dir("daily"))
        self.config["PROJECT"] = "kubuntu"
        self.config["DIST"] = "bionic"
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "simple", "kubuntu", ".pool"),
            self.get_publisher().pool_dir("daily"))

    def test_torrent_dir(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "bionic"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "torrent", "simple",
                "bionic", "desktop"),
            self.get_publisher().torrent_dir("daily-live", "desktop"))
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "torrent", "kubuntu", "simple",
                "bionic", "desktop"),
            self.get_publisher().torrent_dir("daily-live", "desktop"))

    def test_want_torrent(self):
        self.assertTrue(
            self.get_publisher(official="yes").want_torrent("desktop"))
        self.assertFalse(
            self.get_publisher(official="poolonly").want_torrent("desktop"))
        self.assertFalse(self.get_publisher().want_torrent("src"))

    @mock.patch("subprocess.check_call")
    def test_make_torrents(self, mock_check_call):
        self.config["CAPPROJECT"] = "Ubuntu"
        paths = [
            os.path.join(
                self.temp_dir, "dir", "ubuntu-13.04-desktop-%s.iso" % arch)
            for arch in ("amd64", "i386")]
        for path in paths:
            touch(path)
        publisher = self.get_publisher(image_type="daily-live")
        publisher.make_torrents(
            os.path.join(self.temp_dir, "dir"), "ubuntu-13.04")
        command_base = [
            "mktorrent",
            "-a", "https://torrent.ubuntu.com/announce",
            "-a", "https://ipv6.torrent.ubuntu.com/announce",
            "--comment", "Ubuntu CD releases.ubuntu.com",
            "--output",
        ]
        mock_check_call.assert_has_calls([
            mock.call(command_base + ["%s.torrent" % path, path],
                      stdout=mock.ANY)
            for path in paths])

    def test_publish_release_prefixes(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.assertEqual(
            ("ubuntu-13.04", "ubuntu-13.04-beta2"),
            self.get_publisher(status="beta-2").publish_release_prefixes())
        self.config["PROJECT"] = "kubuntu"
        self.config["DIST"] = "dapper"
        self.assertEqual(
            ("kubuntu-6.06.2", "kubuntu-6.06.2"),
            self.get_publisher().publish_release_prefixes())

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_mktorrent_zsyncmake)
    def test_publish_release_arch_ubuntu_desktop_yes(self, mock_call, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "raring"
        daily_dir = os.path.join(
            self.temp_dir, "www", "full", "daily-live", "20130327")
        touch(os.path.join(daily_dir, "raring-desktop-i386.iso"))
        touch(os.path.join(daily_dir, "raring-desktop-i386.manifest"))
        touch(os.path.join(daily_dir, "raring-desktop-i386.iso.zsync"))
        pool_dir = os.path.join(self.temp_dir, "www", "simple", ".pool")
        target_dir = os.path.join(self.temp_dir, "www", "simple", "raring")
        torrent_dir = os.path.join(
            self.temp_dir, "www", "torrent", "simple", "raring", "desktop")
        osextras.ensuredir(pool_dir)
        osextras.ensuredir(target_dir)
        osextras.ensuredir(torrent_dir)
        self.capture_logging()
        publisher = self.get_publisher(official="yes", status="rc")
        publisher.publish_release_arch(
            "daily-live", "20130327", "desktop", "i386")
        self.assertLogEqual([
            "Copying desktop-i386 image ...",
            "Making i386 zsync metafile ...",
            "Creating torrent for %s/ubuntu-13.04-rc-desktop-i386.iso ..." %
            target_dir,
        ])
        self.assertCountEqual([
            "ubuntu-13.04-rc-desktop-i386.iso",
            "ubuntu-13.04-rc-desktop-i386.iso.zsync",
            "ubuntu-13.04-rc-desktop-i386.manifest",
        ], os.listdir(pool_dir))
        self.assertCountEqual([
            "ubuntu-13.04-rc-desktop-i386.iso",
            "ubuntu-13.04-rc-desktop-i386.iso.torrent",
            "ubuntu-13.04-rc-desktop-i386.iso.zsync",
            "ubuntu-13.04-rc-desktop-i386.manifest",
        ], os.listdir(target_dir))
        pool_base = os.path.join(pool_dir, "ubuntu-13.04-rc-desktop-i386")
        target_base = os.path.join(target_dir, "ubuntu-13.04-rc-desktop-i386")
        self.assertEqual(
            "../.pool/ubuntu-13.04-rc-desktop-i386.iso",
            os.readlink("%s.iso" % target_base))
        self.assertEqual(
            "../.pool/ubuntu-13.04-rc-desktop-i386.iso.zsync",
            os.readlink("%s.iso.zsync" % target_base))
        self.assertEqual(
            "../.pool/ubuntu-13.04-rc-desktop-i386.manifest",
            os.readlink("%s.manifest" % target_base))
        self.assertFalse(os.path.islink("%s.iso.torrent" % target_base))
        self.assertEqual(2, mock_call.call_count)
        mock_call.assert_has_calls([
            mock.call([
                "zsyncmake", "-o", "%s.iso.zsync" % pool_base,
                "-u", "ubuntu-13.04-rc-desktop-i386.iso",
                "%s.iso" % pool_base,
            ]),
            mock.call([
                "mktorrent",
                "-a", mock.ANY,
                "-a", mock.ANY,
                "--comment", "Ubuntu CD releases.ubuntu.com",
                "--output", "%s.iso.torrent" % target_base,
                "%s.iso" % target_base
            ], stdout=mock.ANY),
        ])
        self.assertCountEqual([
            "ubuntu-13.04-rc-desktop-i386.iso",
            "ubuntu-13.04-rc-desktop-i386.iso.torrent",
        ], os.listdir(torrent_dir))
        torrent_base = os.path.join(
            torrent_dir, "ubuntu-13.04-rc-desktop-i386")
        self.assertEqual(
            os.stat("%s.iso" % pool_base), os.stat("%s.iso" % torrent_base))
        self.assertEqual(
            os.stat("%s.iso.torrent" % target_base),
            os.stat("%s.iso.torrent" % torrent_base))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_mktorrent_zsyncmake)
    def test_publish_release_arch_ubuntu_desktop_poolonly(self, mock_call,
                                                          *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "raring"
        daily_dir = os.path.join(
            self.temp_dir, "www", "full", "daily-live", "20130327")
        touch(os.path.join(daily_dir, "raring-desktop-i386.iso"))
        touch(os.path.join(daily_dir, "raring-desktop-i386.manifest"))
        touch(os.path.join(daily_dir, "raring-desktop-i386.iso.zsync"))
        pool_dir = os.path.join(self.temp_dir, "www", "simple", ".pool")
        osextras.ensuredir(pool_dir)
        self.capture_logging()
        publisher = self.get_publisher(official="poolonly", status="rc")
        publisher.publish_release_arch(
            "daily-live", "20130327", "desktop", "i386")
        self.assertLogEqual([
            "Copying desktop-i386 image ...",
            "Making i386 zsync metafile ...",
        ])
        self.assertCountEqual([
            "ubuntu-13.04-rc-desktop-i386.iso",
            "ubuntu-13.04-rc-desktop-i386.iso.zsync",
            "ubuntu-13.04-rc-desktop-i386.manifest",
        ], os.listdir(pool_dir))
        self.assertFalse(os.path.exists(os.path.join(
            self.temp_dir, "www", "simple", "bionic")))
        self.assertFalse(os.path.exists(os.path.join(
            self.temp_dir, "www", "torrent", "simple", "bionic", "desktop")))
        pool_base = os.path.join(pool_dir, "ubuntu-13.04-rc-desktop-i386")
        mock_call.assert_called_once_with([
            "zsyncmake", "-o", "%s.iso.zsync" % pool_base,
            "-u", "ubuntu-13.04-rc-desktop-i386.iso",
            "%s.iso" % pool_base,
        ])

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_mktorrent_zsyncmake)
    def test_publish_release_kubuntu_desktop_yes(self, mock_call, *args):
        self.config["PROJECT"] = "kubuntu"
        self.config["CAPPROJECT"] = "Kubuntu"
        series = Series.latest()
        try:
            version = series.pointversion
        except Exception:
            version = series.version
        self.config["DIST"] = series
        self.config["ARCHES"] = "amd64 i386"
        daily_dir = os.path.join(
            self.temp_dir, "www", "full", "kubuntu", "daily-live", "20130327")
        touch(os.path.join(daily_dir, "%s-desktop-amd64.iso" % series))
        touch(os.path.join(daily_dir, "%s-desktop-amd64.manifest" % series))
        touch(os.path.join(daily_dir, "%s-desktop-amd64.iso.zsync" % series))
        touch(os.path.join(daily_dir, "%s-desktop-i386.iso" % series))
        touch(os.path.join(daily_dir, "%s-desktop-i386.manifest" % series))
        touch(os.path.join(daily_dir, "%s-desktop-i386.iso.zsync" % series))
        pool_dir = os.path.join(
            self.temp_dir, "www", "simple", "kubuntu", ".pool")
        target_dir = os.path.join(
            self.temp_dir, "www", "simple", "kubuntu", series.name)
        torrent_dir = os.path.join(
            self.temp_dir, "www", "torrent", "kubuntu", "simple", series.name,
            "desktop")
        self.capture_logging()
        publisher = self.get_publisher(official="yes")
        publisher.publish_release("daily-live", "20130327", "desktop")
        self.assertLogEqual([
            "Constructing release trees ...",
            "Copying desktop-amd64 image ...",
            "Making amd64 zsync metafile ...",
            "Creating torrent for %s/kubuntu-%s-desktop-amd64.iso ..." % (
                target_dir, version),
            "Copying desktop-i386 image ...",
            "Making i386 zsync metafile ...",
            "Creating torrent for %s/kubuntu-%s-desktop-i386.iso ..." % (
                target_dir, version),
            "Checksumming simple tree (pool) ...",
            "No keys found; not signing images.",
            "Checksumming simple tree (%s) ..." % series,
            "No keys found; not signing images.",
            "Refreshing simplestreams...",
            "No keys found; not signing images.",
            "Done!  Remember to sync-mirrors after checking that everything "
            "is OK.",
        ])
        self.assertCountEqual([
            "SHA256SUMS",
            "kubuntu-%s-desktop-amd64.iso" % version,
            "kubuntu-%s-desktop-amd64.iso.zsync" % version,
            "kubuntu-%s-desktop-amd64.manifest" % version,
            "kubuntu-%s-desktop-i386.iso" % version,
            "kubuntu-%s-desktop-i386.iso.zsync" % version,
            "kubuntu-%s-desktop-i386.manifest" % version,
        ], os.listdir(pool_dir))
        self.assertCountEqual([
            ".htaccess", "FOOTER.html", "HEADER.html",
            "SHA256SUMS",
            "kubuntu-%s-desktop-amd64.iso" % version,
            "kubuntu-%s-desktop-amd64.iso.torrent" % version,
            "kubuntu-%s-desktop-amd64.iso.zsync" % version,
            "kubuntu-%s-desktop-amd64.manifest" % version,
            "kubuntu-%s-desktop-i386.iso" % version,
            "kubuntu-%s-desktop-i386.iso.torrent" % version,
            "kubuntu-%s-desktop-i386.iso.zsync" % version,
            "kubuntu-%s-desktop-i386.manifest" % version,
        ], os.listdir(target_dir))
        self.assertCountEqual([
            "kubuntu-%s-desktop-amd64.iso" % version,
            "kubuntu-%s-desktop-amd64.iso.torrent" % version,
            "kubuntu-%s-desktop-i386.iso" % version,
            "kubuntu-%s-desktop-i386.iso.torrent" % version,
        ], os.listdir(torrent_dir))
        self.assertFalse(os.path.exists(os.path.join(
            self.temp_dir, "www", "full", "kubuntu", "releases")))
        self.assertTrue(os.path.exists(os.path.join(
            self.temp_dir, "www", "simple", ".manifest")))
        self.assertTrue(os.path.isdir(os.path.join(
            self.temp_dir, "www", "simple", ".trace")))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_mktorrent_zsyncmake)
    def test_publish_release_simplestreams(self, mock_call, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        series = Series.latest()
        try:
            version = series.pointversion
        except Exception:
            version = series.version
        self.config["DIST"] = series
        self.config["ARCHES"] = "amd64"
        self.config["SIMPLESTREAMS"] = "1"
        daily_dir = os.path.join(
            self.temp_dir, "www", "full", "daily-live", "20130327")
        touch(os.path.join(daily_dir, "%s-desktop-amd64.iso" % series))
        touch(os.path.join(daily_dir, "%s-desktop-amd64.manifest" % series))
        touch(os.path.join(daily_dir, "%s-desktop-amd64.iso.zsync" % series))
        pool_dir = os.path.join(
            self.temp_dir, "www", "simple", ".pool")
        target_dir = os.path.join(
            self.temp_dir, "www", "simple", series.name)
        streams_dir = os.path.join(
            self.temp_dir, "www", "simple", "streams", "v1")
        self.capture_logging()
        publisher = self.get_publisher(official="yes")
        publisher.publish_release("daily-live", "20130327", "desktop")
        self.assertLogEqual([
            "Constructing release trees ...",
            "Copying desktop-amd64 image ...",
            "Making amd64 zsync metafile ...",
            "Creating torrent for %s/ubuntu-%s-desktop-amd64.iso ..." % (
                target_dir, version),
            "Checksumming simple tree (pool) ...",
            "No keys found; not signing images.",
            "Checksumming simple tree (%s) ..." % series,
            "No keys found; not signing images.",
            "Refreshing simplestreams...",
            "No keys found; not signing images.",
            "No keys found; not signing images.",
            "Done!  Remember to sync-mirrors after checking that everything "
            "is OK.",
        ])
        # Double check if we still published everything
        self.assertCountEqual([
            "SHA256SUMS",
            "ubuntu-%s-desktop-amd64.iso" % version,
            "ubuntu-%s-desktop-amd64.iso.zsync" % version,
            "ubuntu-%s-desktop-amd64.manifest" % version,
        ], os.listdir(pool_dir))
        self.assertCountEqual([
            ".htaccess", "FOOTER.html", "HEADER.html",
            "SHA256SUMS",
            "ubuntu-%s-desktop-amd64.iso" % version,
            "ubuntu-%s-desktop-amd64.iso.torrent" % version,
            "ubuntu-%s-desktop-amd64.iso.zsync" % version,
            "ubuntu-%s-desktop-amd64.manifest" % version,
        ], os.listdir(target_dir))
        self.assertFalse(os.path.exists(os.path.join(
            self.temp_dir, "www", "full", "releases")))
        # ...and that the streams got generated as expected
        self.assertCountEqual([
            "index.json", "com.ubuntu.releases:ubuntu.json"
        ], os.listdir(streams_dir))
