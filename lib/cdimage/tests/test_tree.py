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

__metaclass__ = type

from functools import wraps
try:
    from html.parser import HTMLParser
except ImportError:
    from HTMLParser import HTMLParser
import os
import sys
from textwrap import dedent
import traceback

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage import osextras
from cdimage.config import Config, Series, all_series
from cdimage.tests.helpers import TestCase, date_to_time, mkfile, touch
from cdimage.tree import (
    ChinaDailyTree,
    ChinaDailyTreePublisher,
    ChinaReleaseTree,
    DailyTree,
    DailyTreePublisher,
    FullReleaseTree,
    Link,
    Paragraph,
    Publisher,
    SimpleReleasePublisher,
    SimpleReleaseTree,
    Span,
    Tree,
    UnorderedList,
)


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
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        tree = Tree.get_daily(self.config, self.temp_dir)
        self.assertIsInstance(tree, ChinaDailyTree)
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
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        tree = Tree.get_release(self.config, "yes", self.temp_dir)
        self.assertIsInstance(tree, ChinaReleaseTree)
        self.assertEqual(self.config, tree.config)
        self.assertEqual(self.temp_dir, tree.directory)

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
            "%s-desktop-i386.iso" % series,
            "%s-desktop-i386.manifest" % series,
        ):
            touch(os.path.join(target_dir, name))
        current_triggers_path = os.path.join(
            self.temp_dir, "production", "current-triggers")
        with mkfile(current_triggers_path) as current_triggers:
            print("ubuntu\tdaily-live\traring\ti386", file=current_triggers)
        self.config["SSH_ORIGINAL_COMMAND"] = (
            "mark-current --project=ubuntu --series=%s --publish-type=desktop "
            "--architecture=i386 20130321" % series)
        pid = os.fork()
        if pid == 0:  # child
            try:
                Tree.mark_current_trigger(self.config)
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
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        tree = Tree.get_daily(self.config)
        publisher = Publisher.get_daily(tree, "daily")
        self.assertIsInstance(publisher, ChinaDailyTreePublisher)
        self.assertEqual(tree, publisher.tree)
        self.assertEqual("daily", publisher.image_type)

    def test_publish_type(self):
        for image_type, project, dist, publish_type in (
            ("daily-preinstalled", "ubuntu-netbook", "precise",
             "preinstalled-netbook"),
            ("daily-preinstalled", "ubuntu-headless", "precise",
             "preinstalled-headless"),
            ("daily-preinstalled", "ubuntu-server", "precise",
             "preinstalled-server"),
            ("daily-preinstalled", "ubuntu", "precise",
             "preinstalled-desktop"),
            ("daily-live", "edubuntu", "edgy", "live"),
            ("daily-live", "edubuntu", "feisty", "desktop"),
            ("daily-live", "kubuntu-netbook", "lucid", "netbook"),
            ("daily-live", "ubuntu-mid", "lucid", "mid"),
            ("daily-live", "ubuntu-moblin-remix", "lucid", "moblin-remix"),
            ("daily-live", "ubuntu-netbook", "hardy", "netbook"),
            ("daily-live", "ubuntu-server", "hardy", "live"),
            ("daily-live", "ubuntu", "breezy", "live"),
            ("daily-live", "ubuntu", "dapper", "desktop"),
            ("daily-live", "ubuntu-zh_CN", "raring", "desktop"),
            ("ports_dvd", "ubuntu", "hardy", "dvd"),
            ("dvd", "kubuntu", "hardy", "dvd"),
            ("daily", "edubuntu", "edgy", "install"),
            ("daily", "edubuntu", "feisty", "server"),
            ("daily", "edubuntu", "gutsy", "server"),
            ("daily", "edubuntu", "hardy", "addon"),
            ("daily", "jeos", "hardy", "jeos"),
            ("daily", "ubuntu-core", "precise", "core"),
            ("daily", "ubuntu-server", "breezy", "install"),
            ("daily", "ubuntu-server", "dapper", "server"),
            ("daily", "ubuntu", "breezy", "install"),
            ("daily", "ubuntu", "dapper", "alternate"),
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
        for project, expected in (
            ("ubuntu", ["http://releases.ubuntu.com/include/style.css"]),
            ("kubuntu", ["http://releases.ubuntu.com/include/kubuntu.css"]),
        ):
            self.config["PROJECT"] = project
            publisher = Publisher(self.tree, "daily")
            self.assertEqual(expected, publisher.cssincludes())

    def test_cdtypestr(self):
        self.config["DIST"] = "quantal"
        publisher = Publisher(self.tree, "daily-live")
        self.assertEqual(
            "desktop image", publisher.cdtypestr("desktop", "iso"))

    def test_cdtypedesc_desktop(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "quantal"
        publisher = Publisher(self.tree, "daily-live")
        desc = list(publisher.cdtypedesc("desktop", "iso"))
        self.assertEqual(
            "<p>The desktop image allows you to try Ubuntu without changing "
            "your computer at all, and at your option to install it "
            "permanently later.  This type of image is what most people will "
            "want to use.  You will need at least 384MiB of RAM to install "
            "from this image.</p>", "\n".join(map(str, desc)))
        desc_second_time = list(publisher.cdtypedesc("desktop", "iso"))
        self.assertEqual(
            "<p>The desktop image allows you to try Ubuntu without changing "
            "your computer at all, and at your option to install it "
            "permanently later.  You will need at least 384MiB of RAM to "
            "install from this image.</p>",
            "\n".join(map(str, desc_second_time)))

    def test_cdtypedesc_alternate(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "quantal"
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
            "<li>installs on systems with less than about 384MiB of RAM "
            "(although note that low-memory systems may not be able to run "
            "a full desktop environment reasonably).</li>\n"
            "</ul>\n"
            "<p>In the event that you encounter a bug using the alternate "
            "installer, please file a bug on the <a "
            "href=\"https://bugs.launchpad.net/ubuntu/+source/"
            "debian-installer/+filebug\">debian-installer</a> package.</p>",
            "\n".join(map(str, desc)))

    def test_archdesc(self):
        publisher = Publisher(self.tree, "daily-live")
        self.assertEqual(
            "For almost all PCs.  This includes most machines with "
            "Intel/AMD/etc type processors and almost all computers that run "
            "Microsoft Windows, as well as newer Apple Macintosh systems "
            "based on Intel processors.  Choose this if you are at all "
            "unsure.",
            publisher.archdesc("i386", "desktop"))

    def test_maybe_oversized(self):
        self.config["DIST"] = "precise"
        oversized_path = os.path.join(
            self.directory, "precise-desktop-i386.OVERSIZED")
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
            "MD5SUMS",
            "raring-desktop-amd64.iso", "raring-desktop-amd64.list",
            "raring-desktop-i386.iso", "raring-desktop-i386.list",
        ):
            touch(os.path.join(self.directory, name))
        publisher = Publisher(self.tree, "daily-live")
        self.assertCountEqual(
            ["raring-desktop-amd64.list", "raring-desktop-i386.list"],
            publisher.find_images(self.directory, "raring", "desktop"))

    def test_find_source_images(self):
        for name in (
            "MD5SUMS",
            "raring-src-1.iso", "raring-src-2.iso", "raring-src-3.iso",
        ):
            touch(os.path.join(self.directory, name))
        publisher = Publisher(self.tree, "daily-live")
        self.assertEqual(
            [1, 2, 3], publisher.find_source_images(self.directory, "raring"))

    def test_find_any_with_extension(self):
        for name in (
            "MD5SUMS",
            "raring-desktop-amd64.iso", "raring-desktop-amd64.iso.torrent",
            "raring-desktop-i386.iso", "raring-desktop-i386.list",
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
        self.config["DIST"] = "raring"
        for name in (
            "MD5SUMS",
            "raring-desktop-amd64.iso", "raring-desktop-amd64.iso.zsync",
            "raring-desktop-i386.iso", "raring-desktop-i386.list",
        ):
            touch(os.path.join(self.directory, name))
        publisher = Publisher(self.tree, "daily-live")
        publisher.make_web_indices(self.directory, "raring", status="daily")

        self.assertCountEqual([
            "HEADER.html", "FOOTER.html", ".htaccess",
            "MD5SUMS",
            "raring-desktop-amd64.iso", "raring-desktop-amd64.iso.zsync",
            "raring-desktop-i386.iso", "raring-desktop-i386.list",
        ], os.listdir(self.directory))

        header_path = os.path.join(self.directory, "HEADER.html")
        footer_path = os.path.join(self.directory, "FOOTER.html")
        htaccess_path = os.path.join(self.directory, ".htaccess")
        parser = HTMLParser()
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
                "AddDescription \"Desktop image for PC (Intel x86) computers "
                "(standard download)\" raring-desktop-i386.iso\n"
                "AddDescription \"Desktop image for PC (Intel x86) computers "
                "(file listing)\" raring-desktop-i386.list\n"
                "AddDescription \"Desktop image for 64-bit PC (AMD64) "
                "computers (<a href=\\\"http://zsync.moria.org.uk/\\\">"
                "zsync</a> metafile)\" raring-desktop-amd64.iso.zsync\n"
                "AddDescription \"Desktop image for 64-bit PC (AMD64) "
                "computers (standard download)\" raring-desktop-amd64.iso\n"
                "\n"
                "HeaderName HEADER.html\n"
                "ReadmeName FOOTER.html\n"
                "IndexIgnore .htaccess HEADER.html FOOTER.html "
                "published-ec2-daily.txt published-ec2-release.txt\n"
                "IndexOptions NameWidth=* DescriptionWidth=* "
                "SuppressHTMLPreamble FancyIndexing IconHeight=22 "
                "IconWidth=22\n"
                "AddIcon ../../cdicons/folder.png ^^DIRECTORY^^\n"
                "AddIcon ../../cdicons/iso.png .iso\n"
                "AddIcon ../../cdicons/img.png .img .tar.gz .tar.xz\n"
                "AddIcon ../../cdicons/jigdo.png .jigdo .template\n"
                "AddIcon ../../cdicons/list.png .list .manifest .html .zsync "
                "MD5SUMS MD5SUMS.gpg MD5SUMS-metalink MD5SUMS-metalink.gpg "
                "SHA1SUMS SHA1SUMS.gpg SHA256SUMS SHA256SUMS.gpg\n"
                "AddIcon ../../cdicons/torrent.png .torrent .metalink\n",
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
        self.config["DIST"] = Series.latest()

    def make_publisher(self, project, image_type, **kwargs):
        self.config["PROJECT"] = project
        self.tree = DailyTree(self.config)
        publisher = DailyTreePublisher(self.tree, image_type, **kwargs)
        osextras.ensuredir(publisher.image_output("i386"))
        osextras.ensuredir(publisher.britney_report)
        osextras.ensuredir(publisher.full_tree)
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

    def test_full_tree(self):
        self.assertEqual(
            os.path.join(self.config.root, "www", "full"),
            self.make_publisher("ubuntu", "daily").full_tree)
        self.assertEqual(
            os.path.join(self.config.root, "www", "full", "kubuntu"),
            self.make_publisher("kubuntu", "daily").full_tree)

    def test_image_type_dir(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        self.assertEqual("daily-live", publisher.image_type_dir)
        self.config["DIST"] = "hoary"
        self.assertEqual(
            os.path.join("hoary", "daily-live"), publisher.image_type_dir)

    def test_publish_base(self):
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

    def test_metalink_dirs(self):
        basedir = os.path.join(self.config.root, "www", "full")
        date = "20120912"
        publisher = self.make_publisher("ubuntu", "daily-live")
        self.assertEqual(
            (basedir, os.path.join("daily-live", date)),
            publisher.metalink_dirs(date))
        self.config["DIST"] = "hoary"
        self.assertEqual(
            (basedir, os.path.join("hoary", "daily-live", date)),
            publisher.metalink_dirs(date))
        publisher = self.make_publisher("kubuntu", "daily-live")
        self.config["DIST"] = Series.latest()
        self.assertEqual(
            (basedir, os.path.join("kubuntu", "daily-live", date)),
            publisher.metalink_dirs(date))
        self.config["DIST"] = "hoary"
        self.assertEqual(
            (basedir, os.path.join("kubuntu", "hoary", "daily-live", date)),
            publisher.metalink_dirs(date))

    def test_size_limit(self):
        for project, dist, image_type, size_limit in (
            ("edubuntu", None, "daily-preinstalled", 4700372992),
            ("edubuntu", None, "dvd", 4700372992),
            ("ubuntustudio", None, "dvd", 4700372992),
            ("ubuntu-mid", None, "daily-live", 1073741824),
            ("ubuntu-moblin-remix", None, "daily-live", 1073741824),
            ("kubuntu-active", None, "daily-live", 1073741824),
            ("kubuntu", None, "daily-live", 1073741824),
            ("ubuntu", None, "dvd", 4700372992),
            ("ubuntu", "precise", "daily-live", 736665600),
            ("ubuntu", "quantal", "daily-live", 801000000),
            ("xubuntu", "quantal", "daily-live", 736665600),
            ("xubuntu", "raring", "daily-live", 1073741824),
        ):
            if dist is not None:
                self.config["DIST"] = dist
            publisher = self.make_publisher(project, image_type)
            self.assertEqual(size_limit, publisher.size_limit)

    def test_size_limit_extension(self):
        publisher = self.make_publisher("ubuntu", "daily")
        self.assertEqual(
            1024 * 1024 * 1024, publisher.size_limit_extension("img"))
        self.assertEqual(
            1024 * 1024 * 1024, publisher.size_limit_extension("tar.gz"))
        self.assertEqual(
            publisher.size_limit, publisher.size_limit_extension("iso"))

    def test_size_limit_extension_edubuntu(self):
        # size_limit_extension has special-casing for Edubuntu.
        publisher = self.make_publisher("edubuntu", "daily")
        self.assertEqual(
            publisher.size_limit, publisher.size_limit_extension("img"))
        self.assertEqual(
            publisher.size_limit, publisher.size_limit_extension("tar.gz"))
        self.assertEqual(
            publisher.size_limit, publisher.size_limit_extension("iso"))

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

    def test_jigdo_ports_powerpc(self):
        publisher = self.make_publisher("ubuntu", "daily")
        for series in all_series[:5]:
            publisher.config["DIST"] = series
            self.assertFalse(publisher.jigdo_ports("powerpc"))
        for series in all_series[5:]:
            publisher.config["DIST"] = series
            self.assertTrue(publisher.jigdo_ports("powerpc"))

    def test_jigdo_ports_sparc(self):
        publisher = self.make_publisher("ubuntu", "daily")
        for series in all_series[:3] + all_series[7:]:
            publisher.config["DIST"] = series
            self.assertTrue(publisher.jigdo_ports("sparc"))
        for series in all_series[3:7]:
            publisher.config["DIST"] = series
            self.assertFalse(publisher.jigdo_ports("sparc"))

    def test_jigdo_ports(self):
        publisher = self.make_publisher("ubuntu", "daily")
        for arch in ("amd64", "i386"):
            self.assertFalse(publisher.jigdo_ports(arch))
        for arch in ("armel", "armhf", "hppa", "ia64", "lpia"):
            self.assertTrue(publisher.jigdo_ports(arch))

    def test_replace_jigdo_mirror(self):
        jigdo_path = os.path.join(self.temp_dir, "jigdo")
        with mkfile(jigdo_path) as jigdo:
            print("[Servers]", file=jigdo)
            print("Debian=http://archive.ubuntu.com/ubuntu/ --try-last",
                  file=jigdo)
        publisher = self.make_publisher("ubuntu", "daily")
        publisher.replace_jigdo_mirror(
            jigdo_path, "http://archive.ubuntu.com/ubuntu/",
            "http://ports.ubuntu.com/ubuntu-ports")
        with open(jigdo_path) as jigdo:
            self.assertEqual(dedent("""\
                [Servers]
                Debian=http://ports.ubuntu.com/ubuntu-ports --try-last
                """), jigdo.read())

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

    def test_publish_livecd_base(self):
        publisher = self.make_publisher("livecd-base", "livecd-base")
        source_dir = os.path.join(
            self.temp_dir, "scratch", "livecd-base", self.config.series,
            "livecd-base", "live")
        for ext in (
            "squashfs", "kernel", "initrd", "manifest", "manifest-remove",
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
        ], os.listdir(target_dir))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    def test_publish_source(self, mock_zsyncmake, *args):
        publisher = self.make_publisher("ubuntu", "daily-live")
        source_dir = publisher.image_output("src")
        touch(os.path.join(source_dir, "%s-src-1.raw" % self.config.series))
        touch(os.path.join(source_dir, "%s-src-1.list" % self.config.series))
        touch(os.path.join(source_dir, "%s-src-1.jigdo" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-src-1.template" % self.config.series))
        touch(os.path.join(source_dir, "%s-src-2.raw" % self.config.series))
        touch(os.path.join(source_dir, "%s-src-2.list" % self.config.series))
        touch(os.path.join(source_dir, "%s-src-2.jigdo" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-src-2.template" % self.config.series))
        self.capture_logging()
        list(publisher.publish_source("20120807"))
        self.assertLogEqual([
            "Publishing source 1 ...",
            "Publishing source 1 jigdo ...",
            "Making source 1 zsync metafile ...",
            "Publishing source 2 ...",
            "Publishing source 2 jigdo ...",
            "Making source 2 zsync metafile ...",
        ])
        target_dir = os.path.join(publisher.publish_base, "20120807", "source")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            "%s-src-1.iso" % self.config.series,
            "%s-src-1.jigdo" % self.config.series,
            "%s-src-1.list" % self.config.series,
            "%s-src-1.template" % self.config.series,
            "%s-src-2.iso" % self.config.series,
            "%s-src-2.jigdo" % self.config.series,
            "%s-src-2.list" % self.config.series,
            "%s-src-2.template" % self.config.series,
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
        publisher = self.make_publisher("ubuntu", "daily-live")
        target_dir = os.path.join(publisher.publish_base, "20130321")
        for name in (
            "MD5SUMS",
            "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
            "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
        ):
            touch(os.path.join(target_dir, name))
        self.assertEqual(
            set(["raring-desktop-amd64.iso", "raring-desktop-i386.iso"]),
            publisher.published_images("20130321"))

    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_missing_to_single(self, mock_polish_directory):
        publisher = self.make_publisher("ubuntu", "daily-live")
        target_dir = os.path.join(publisher.publish_base, "20130321")
        for name in (
            "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
            "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
        ):
            touch(os.path.join(target_dir, name))
        publisher.mark_current("20130321", ["amd64", "i386"])
        publish_current = os.path.join(publisher.publish_base, "current")
        self.assertTrue(os.path.islink(publish_current))
        self.assertEqual("20130321", os.readlink(publish_current))
        self.assertEqual(0, mock_polish_directory.call_count)

    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_missing_to_mixed(self, mock_polish_directory):
        publisher = self.make_publisher("ubuntu", "daily-live")
        target_dir = os.path.join(publisher.publish_base, "20130321")
        for name in (
            "MD5SUMS",
            "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
            "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
        ):
            touch(os.path.join(target_dir, name))
        publisher.mark_current("20130321", ["amd64"])
        publish_current = os.path.join(publisher.publish_base, "current")
        self.assertFalse(os.path.islink(publish_current))
        self.assertTrue(os.path.isdir(publish_current))
        self.assertCountEqual(
            ["raring-desktop-amd64.iso", "raring-desktop-amd64.manifest"],
            os.listdir(publish_current))
        for name in (
            "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
        ):
            path = os.path.join(publish_current, name)
            self.assertTrue(os.path.islink(path))
            self.assertEqual(
                os.path.join(os.pardir, "20130321", name), os.readlink(path))
        self.assertEqual([target_dir], publisher.checksum_dirs)
        mock_polish_directory.assert_called_once_with("current")

    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_single_to_single(self, mock_polish_directory):
        publisher = self.make_publisher("ubuntu", "daily-live")
        for date in "20130320", "20130321":
            for name in (
                "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
                "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
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
        publisher = self.make_publisher("ubuntu", "daily-live")
        for date in "20130320", "20130321":
            for name in (
                "MD5SUMS",
                "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
                "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
            ):
                touch(os.path.join(publisher.publish_base, date, name))
        publish_current = os.path.join(publisher.publish_base, "current")
        os.symlink("20130320", publish_current)
        publisher.mark_current("20130321", ["amd64"])
        self.assertFalse(os.path.islink(publish_current))
        self.assertTrue(os.path.isdir(publish_current))
        self.assertCountEqual([
            "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
            "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
        ], os.listdir(publish_current))
        for date, arch in (("20130320", "i386"), ("20130321", "amd64")):
            for name in (
                "raring-desktop-%s.iso" % arch,
                "raring-desktop-%s.manifest" % arch,
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
        publisher = self.make_publisher("ubuntu", "daily-live")
        for date in "20130320", "20130321":
            for name in (
                "MD5SUMS",
                "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
                "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
            ):
                touch(os.path.join(publisher.publish_base, date, name))
        publish_current = os.path.join(publisher.publish_base, "current")
        osextras.ensuredir(publish_current)
        for date, arch in (("20130320", "i386"), ("20130321", "amd64")):
            for name in (
                "raring-desktop-%s.iso" % arch,
                "raring-desktop-%s.manifest" % arch,
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
        publisher = self.make_publisher("ubuntu", "daily-live")
        for date in "20130320", "20130321":
            for name in (
                "MD5SUMS",
                "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
                "raring-desktop-amd64+mac.iso",
                "raring-desktop-amd64+mac.manifest",
                "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
                "raring-desktop-powerpc.iso",
                "raring-desktop-powerpc.manifest",
            ):
                touch(os.path.join(publisher.publish_base, date, name))
        publish_current = os.path.join(publisher.publish_base, "current")
        osextras.ensuredir(publish_current)
        for date, arch in (("20130320", "i386"), ("20130321", "amd64")):
            for name in (
                "raring-desktop-%s.iso" % arch,
                "raring-desktop-%s.manifest" % arch,
            ):
                os.symlink(
                    os.path.join(os.pardir, date, name),
                    os.path.join(publish_current, name))
        publisher.mark_current("20130321", ["i386"])
        self.assertFalse(os.path.islink(publish_current))
        self.assertTrue(os.path.isdir(publish_current))
        self.assertCountEqual([
            "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
            "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
        ], os.listdir(publish_current))
        for name in (
            "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
            "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
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
        publisher.mark_current("20130320", ["amd64+mac", "powerpc"])
        self.assertFalse(os.path.islink(publish_current))
        self.assertTrue(os.path.isdir(publish_current))
        self.assertCountEqual([
            "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
            "raring-desktop-amd64+mac.iso",
            "raring-desktop-amd64+mac.manifest",
            "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
            "raring-desktop-powerpc.iso", "raring-desktop-powerpc.manifest",
        ], os.listdir(publish_current))
        for date, arch in (
            ("20130320", "amd64+mac"), ("20130320", "powerpc"),
            ("20130321", "amd64"), ("20130321", "i386"),
        ):
            for name in (
                "raring-desktop-%s.iso" % arch,
                "raring-desktop-%s.manifest" % arch,
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

    def test_qa_product(self):
        for project, image_type, publish_type, product in (
            ("ubuntu", "daily", "alternate", "Ubuntu Alternate"),
            ("ubuntu", "daily-live", "desktop", "Ubuntu Desktop"),
            ("ubuntu", "daily-preinstalled", "preinstalled-desktop",
             "Ubuntu Desktop Preinstalled"),
            ("ubuntu", "dvd", "dvd", "Ubuntu DVD"),
            ("ubuntu", "wubi", "wubi", "Ubuntu Wubi"),
            ("kubuntu", "daily", "alternate", "Kubuntu Alternate"),
            ("kubuntu", "daily-live", "desktop", "Kubuntu Desktop"),
            ("kubuntu", "daily-preinstalled", "preinstalled-desktop",
             "Kubuntu Desktop"),
            ("kubuntu", "dvd", "dvd", "Kubuntu DVD"),
            ("kubuntu-active", "daily-live", "desktop", "Kubuntu Active"),
            ("kubuntu-active", "daily-preinstalled", "preinstalled-mobile",
             "Kubuntu Active"),
            ("edubuntu", "dvd", "dvd", "Edubuntu DVD"),
            ("xubuntu", "daily", "alternate", "Xubuntu Alternate"),
            ("xubuntu", "daily-live", "desktop", "Xubuntu Desktop"),
            ("ubuntu-server", "daily", "server", "Ubuntu Server"),
            ("ubuntu-server", "daily-preinstalled", "preinstalled-server",
             "Ubuntu Server"),
            ("ubuntustudio", "daily", "alternate", "Ubuntu Studio Alternate"),
            ("ubuntustudio", "dvd", "dvd", "Ubuntu Studio DVD"),
            ("mythbuntu", "daily-live", "desktop", "Mythbuntu Desktop"),
            ("lubuntu", "daily", "alternate", "Lubuntu Alternate"),
            ("lubuntu", "daily-live", "desktop", "Lubuntu Desktop"),
            ("lubuntu", "daily-preinstalled", "preinstalled-desktop",
             "Lubuntu Desktop Preinstalled"),
            ("ubuntu-core", "daily", "core", "Ubuntu Core"),
            ("ubuntu-zh_CN", "daily-live", "desktop",
             "Ubuntu Chinese Desktop"),
            ("ubuntukylin", "daily-live", "desktop", "UbuntuKylin Desktop"),
            ("ubuntu-gnome", "daily-live", "desktop", "Ubuntu GNOME Desktop"),
        ):
            # Use "daily" here to match bin/post-qa; qa_product shouldn't
            # use the publisher's image_type at all.
            publisher = self.make_publisher(project, "daily")
            self.assertEqual(
                "%s i386" % product,
                publisher.qa_product(
                    project, image_type, publish_type, "i386"))

    @mock_isotracker
    def test_post_qa(self):
        publisher = self.make_publisher("ubuntu", "daily")
        os.makedirs(os.path.join(publisher.publish_base, "20130221"))
        publisher.post_qa(
            "20130221", [
                "ubuntu/daily/raring-alternate-i386",
                "ubuntu/daily/raring-alternate-amd64",
            ])
        expected = [
            ["Ubuntu Alternate i386", "20130221", ""],
            ["Ubuntu Alternate amd64", "20130221", ""],
        ]
        self.assertEqual("raring", isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

        os.makedirs(os.path.join(
            publisher.full_tree, "precise", "daily-live", "20130221"))
        publisher.post_qa(
            "20130221", [
                "ubuntu/precise/daily-live/precise-desktop-i386",
                "ubuntu/precise/daily-live/precise-desktop-amd64",
            ])
        expected = [
            ["Ubuntu Desktop i386", "20130221", ""],
            ["Ubuntu Desktop amd64", "20130221", ""],
        ]
        self.assertEqual("precise", isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

    @mock_isotracker
    def test_post_qa_oversized(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        touch(os.path.join(
            self.temp_dir, "www", "full", "daily-live", "20130315",
            "raring-desktop-i386.OVERSIZED"))
        publisher.post_qa(
            "20130315", ["ubuntu/daily-live/raring-desktop-i386"])
        expected_note = (
            "<strong>WARNING: This image is OVERSIZED. This should never "
            "happen during milestone testing.</strong>")
        expected = [["Ubuntu Desktop i386", "20130315", expected_note]]
        self.assertEqual("raring", isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

        publisher = self.make_publisher("kubuntu", "daily-live")
        touch(os.path.join(
            self.temp_dir, "www", "full", "kubuntu", "precise", "daily-live",
            "20130315", "precise-desktop-i386.OVERSIZED"))
        publisher.post_qa(
            "20130315", ["kubuntu/precise/daily-live/precise-desktop-i386"])
        expected_note = (
            "<strong>WARNING: This image is OVERSIZED. This should never "
            "happen during milestone testing.</strong>")
        expected = [["Kubuntu Desktop i386", "20130315", expected_note]]
        self.assertEqual("precise", isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

    @mock_isotracker
    def test_post_qa_wrong_date(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        self.assertRaisesRegex(
            Exception, r"Cannot post images from nonexistent directory: .*",
            publisher.post_qa, "bad-date",
            ["ubuntu/daily-live/raring-desktop-i386.iso"])

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
            "MD5SUMS",
            "SHA1SUMS",
            "SHA256SUMS",
            "%s-desktop-i386.iso" % self.config.series,
        ], os.listdir(target_dir))
        mock_make_web_indices.assert_called_once_with(
            target_dir, self.config.series, status="daily")
        make_metalink = os.path.join(self.temp_dir, "bin", "make-metalink")
        mock_call.assert_called_once_with([
            make_metalink, publisher.tree.directory, self.config.series,
            os.path.join(publisher.image_type_dir, "20130320"),
            publisher.tree.site_name
        ])

    @mock.patch("cdimage.tree.zsyncmake")
    @mock.patch("cdimage.tree.DailyTreePublisher.post_qa")
    def test_publish(self, mock_post_qa, *args):
        self.config["ARCHES"] = "i386"
        self.config["CDIMAGE_INSTALL_BASE"] = "1"
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
        # TODO: clean up make-metalink call
        bin_dir = os.path.join(self.config.root, "bin")
        os.mkdir(bin_dir)
        os.symlink("/bin/true", os.path.join(bin_dir, "make-metalink"))
        os.mkdir(os.path.join(self.config.root, "etc"))
        self.capture_logging()

        publisher.publish("20120807")

        self.assertLogEqual([
            "Publishing i386 ...",
            "Unknown file type 'empty'; assuming .iso",
            "Publishing i386 live manifest ...",
            "Making i386 zsync metafile ...",
            "No keys found; not signing images.",
            "No keys found; not signing images.",
        ])
        target_dir = os.path.join(publisher.publish_base, "20120807")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            ".htaccess",
            "FOOTER.html",
            "HEADER.html",
            "MD5SUMS",
            "SHA1SUMS",
            "SHA256SUMS",
            "%s-desktop-i386.iso" % self.config.series,
            "%s-desktop-i386.list" % self.config.series,
            "%s-desktop-i386.manifest" % self.config.series,
            "report.html",
        ], os.listdir(target_dir))
        self.assertCountEqual(
            [".htaccess", "20120807", "current", "pending"],
            os.listdir(publisher.publish_base))
        mock_post_qa.assert_called_once_with(
            "20120807", ["ubuntu/daily-live/raring-desktop-i386"])

    def test_get_purge_days_no_config(self):
        publisher = self.make_publisher("ubuntu", "daily")
        self.assertIsNone(publisher.get_purge_days("daily"))

    def test_get_purge_days(self):
        publisher = self.make_publisher("ubuntu", "daily")
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print(dedent("""\
                # comment

                daily 1
                daily-live 2"""), file=purge_days)
        self.assertEqual(1, publisher.get_purge_days("daily"))
        self.assertEqual(2, publisher.get_purge_days("daily-live"))
        self.assertIsNone(publisher.get_purge_days("dvd"))

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
        if self.config["UBUNTU_DEFAULTS_LOCALE"]:
            project = "ubuntu-zh_CN"
            purge_desc = "%s/%s" % (project, self.config.series)
        else:
            project = "ubuntu"
            purge_desc = project
        self.assertLogEqual([
            # TODO: this test exposes poor grammar
            "Purging %s/daily images older than 1 days ..." % project,
            "Purging %s/daily/20130318" % purge_desc,
            "Purging %s/daily/20130319" % purge_desc,
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
        if self.config["UBUNTU_DEFAULTS_LOCALE"]:
            project = "ubuntu-zh_CN"
        else:
            project = "ubuntu"
        self.assertLogEqual([
            # TODO: this test exposes poor grammar
            "Purging %s/daily images older than 1 days ..." % project,
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
        if self.config["UBUNTU_DEFAULTS_LOCALE"]:
            project = "ubuntu-zh_CN"
        else:
            project = "ubuntu"
        self.assertLogEqual([
            # TODO: this test exposes poor grammar
            "Purging %s/daily images older than 1 days ..." % project,
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
            os.path.join(os.pardir, "20130319", "raring-desktop-i386.iso"),
            os.path.join(publish_current, "raring-desktop-i386.iso"))
        with mkfile(os.path.join(
                self.temp_dir, "etc", "purge-days")) as purge_days:
            print("daily 1", file=purge_days)
        self.capture_logging()
        publisher.purge()
        if self.config["UBUNTU_DEFAULTS_LOCALE"]:
            project = "ubuntu-zh_CN"
            purge_desc = "%s/%s" % (project, self.config.series)
        else:
            project = "ubuntu"
            purge_desc = project
        self.assertLogEqual([
            # TODO: this test exposes poor grammar
            "Purging %s/daily images older than 1 days ..." % project,
            "Purging %s/daily/20130318" % purge_desc,
        ])
        self.assertCountEqual(
            ["20130319", "20130320", "20130321", "current"],
            os.listdir(publisher.publish_base))


class TestChinaDailyTree(TestDailyTree):
    def setUp(self):
        super(TestChinaDailyTree, self).setUp()
        self.tree = ChinaDailyTree(self.config, self.temp_dir)

    def test_default_directory(self):
        self.config.root = self.temp_dir
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "china-images"),
            ChinaDailyTree(self.config).directory)

    def test_site_name(self):
        self.assertEqual("china-images.ubuntu.com", self.tree.site_name)


class TestChinaDailyTreePublisher(TestDailyTreePublisher):
    def setUp(self):
        super(TestChinaDailyTreePublisher, self).setUp()
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"

    def make_publisher(self, project, image_type, **kwargs):
        self.config["PROJECT"] = project
        self.tree = ChinaDailyTree(self.config)
        publisher = ChinaDailyTreePublisher(self.tree, image_type, **kwargs)
        osextras.ensuredir(publisher.image_output("i386"))
        osextras.ensuredir(publisher.britney_report)
        osextras.ensuredir(publisher.full_tree)
        return publisher

    def test_image_output(self):
        self.config["DIST"] = "natty"
        self.assertEqual(
            os.path.join(
                self.config.root, "scratch", "ubuntu-chinese-edition",
                "natty"),
            self.make_publisher("ubuntu", "daily-live").image_output("i386"))
        self.config["DIST"] = "oneiric"
        self.assertEqual(
            os.path.join(
                self.config.root, "scratch", "ubuntu-zh_CN", "oneiric",
                "daily-live", "live"),
            self.make_publisher("ubuntu", "daily-live").image_output("i386"))

    def test_source_extension(self):
        self.assertEqual(
            "iso",
            self.make_publisher("ubuntu", "daily-live").source_extension)

    def test_full_tree(self):
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "china-images"),
            self.make_publisher("ubuntu", "daily-live").full_tree)

    def test_image_type_dir(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        for series in all_series:
            self.config["DIST"] = series
            self.assertEqual(
                os.path.join(series.name, "daily-live"),
                publisher.image_type_dir)

    def test_publish_base(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        for series in all_series:
            self.config["DIST"] = series
            self.assertEqual(
                os.path.join(
                    self.config.root, "www", "china-images",
                    series.name, "daily-live"),
                publisher.publish_base)

    def test_metalink_dirs(self):
        basedir = os.path.join(self.config.root, "www", "china-images")
        date = "20120912"
        publisher = self.make_publisher("ubuntu", "daily-live")
        for series in all_series:
            self.config["DIST"] = series
            self.assertEqual(
                (basedir, os.path.join(series.name, "daily-live", date)),
                publisher.metalink_dirs(date))

    def test_size_limit(self):
        for image_type, size_limit in (
            ("dvd", 4700372992),
            ("daily-live", 850000000),
        ):
            publisher = self.make_publisher("ubuntu", image_type)
            self.assertEqual(size_limit, publisher.size_limit)

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    def test_publish_binary(self, mock_zsyncmake, *args):
        publisher = self.make_publisher("ubuntu", "daily-live")
        source_dir = publisher.image_output("i386")
        touch(os.path.join(
            source_dir, "%s-desktop-i386.iso" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-i386.list" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-i386.manifest" % self.config.series))
        self.capture_logging()
        self.assertEqual(
            ["ubuntu-zh_CN/raring/daily-live/raring-desktop-i386"],
            list(publisher.publish_binary("desktop", "i386", "20120807")))
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

    def test_publish_livecd_base(self):
        pass

    def test_publish_source(self):
        pass

    # TODO: we should have a modified version of this for zh_CN
    def test_post_qa(self):
        pass

    @mock_isotracker
    def test_post_qa_oversized(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        touch(os.path.join(
            self.temp_dir, "www", "china-images", "raring", "daily-live",
            "20130315", "raring-desktop-i386.OVERSIZED"))
        publisher.post_qa(
            "20130315", ["ubuntu-zh_CN/raring/daily-live/raring-desktop-i386"])
        expected_note = (
            "<strong>WARNING: This image is OVERSIZED. This should never "
            "happen during milestone testing.</strong>")
        expected = [["Ubuntu Chinese Desktop i386", "20130315", expected_note]]
        self.assertEqual("raring", isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

    @mock.patch("cdimage.tree.zsyncmake")
    @mock.patch("cdimage.tree.DailyTreePublisher.post_qa")
    def test_publish(self, mock_post_qa, *args):
        self.config["ARCHES"] = "i386"
        self.config["CDIMAGE_LIVE"] = "1"
        publisher = self.make_publisher("ubuntu", "daily-live")
        source_dir = publisher.image_output("i386")
        touch(os.path.join(
            source_dir, "%s-desktop-i386.iso" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-i386.list" % self.config.series))
        touch(os.path.join(
            source_dir, "%s-desktop-i386.manifest" % self.config.series))
        # TODO: clean up make-metalink call
        bin_dir = os.path.join(self.config.root, "bin")
        os.mkdir(bin_dir)
        os.symlink("/bin/true", os.path.join(bin_dir, "make-metalink"))
        os.mkdir(os.path.join(self.config.root, "etc"))
        self.capture_logging()

        publisher.publish("20120807")

        self.assertLogEqual([
            "Publishing i386 ...",
            "Unknown file type 'empty'; assuming .iso",
            "Publishing i386 live manifest ...",
            "Making i386 zsync metafile ...",
            "No keys found; not signing images.",
            "No keys found; not signing images.",
        ])
        target_dir = os.path.join(publisher.publish_base, "20120807")
        self.assertEqual([], os.listdir(source_dir))
        self.assertCountEqual([
            ".htaccess",
            "FOOTER.html",
            "HEADER.html",
            "MD5SUMS",
            "SHA1SUMS",
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
            ["ubuntu-zh_CN/raring/daily-live/raring-desktop-i386"])


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


class TestReleasePublisher(TestCase):
    def setUp(self):
        super(TestReleasePublisher, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()

    @mock.patch("subprocess.check_call")
    def test_make_torrents_simple(self, mock_check_call):
        self.config["CAPPROJECT"] = "Ubuntu"
        paths = [
            os.path.join(
                self.temp_dir, "dir", "ubuntu-13.04-desktop-%s.iso" % arch)
            for arch in ("amd64", "i386")]
        for path in paths:
            touch(path)
        tree = Tree.get_release(self.config, "yes", directory=self.temp_dir)
        publisher = tree.get_publisher("daily-live", "yes")
        publisher.make_torrents(
            os.path.join(self.temp_dir, "dir"), "ubuntu-13.04")
        command_base = [
            "btmakemetafile", "http://torrent.ubuntu.com:6969/announce",
            "--announce_list",
            ("http://torrent.ubuntu.com:6969/announce|"
                "http://ipv6.torrent.ubuntu.com:6969/announce"),
            "--comment", "Ubuntu CD releases.ubuntu.com",
        ]
        mock_check_call.assert_has_calls([
            mock.call(command_base + [path], stdout=mock.ANY)
            for path in paths])

    @mock.patch("subprocess.check_call")
    def test_make_torrents_full(self, mock_check_call):
        self.config["CAPPROJECT"] = "Ubuntu"
        paths = [
            os.path.join(
                self.temp_dir, "dir", "ubuntu-13.04-desktop-%s.iso" % arch)
            for arch in ("amd64", "i386")]
        for path in paths:
            touch(path)
        tree = Tree.get_release(self.config, "named", directory=self.temp_dir)
        publisher = tree.get_publisher("daily-live", "named")
        self.capture_logging()
        publisher.make_torrents(
            os.path.join(self.temp_dir, "dir"), "ubuntu-13.04")
        self.assertLogEqual(
            ["Creating torrent for %s ..." % path for path in paths])
        command_base = [
            "btmakemetafile", "http://torrent.ubuntu.com:6969/announce",
            "--comment", "Ubuntu CD cdimage.ubuntu.com",
        ]
        mock_check_call.assert_has_calls([
            mock.call(command_base + [path], stdout=mock.ANY)
            for path in paths])
