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
import shutil
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
    TorrentTree,
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
            "%s-desktop-i386.iso" % series,
            "%s-desktop-i386.manifest" % series,
        ):
            touch(os.path.join(target_dir, name))
        current_triggers_path = os.path.join(
            self.temp_dir, "production", "current-triggers")
        with mkfile(current_triggers_path) as current_triggers:
            print(
                "ubuntu\tdaily-live\t%s\ti386" % series, file=current_triggers)
        self.config["SSH_ORIGINAL_COMMAND"] = (
            "mark-current --project=ubuntu --series=%s --publish-type=desktop "
            "--architecture=i386 20130321" % series)
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
                self.assertEqual("wily-desktop-i386.iso\n",
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
            ("daily-preinstalled", "ubuntu-touch", "saucy",
             "preinstalled-touch"),
            ("daily-preinstalled", "ubuntu-pd", "vivid",
             "preinstalled-pd"),
            ("daily-live", "edubuntu", "edgy", "live"),
            ("daily-live", "edubuntu", "feisty", "desktop"),
            ("daily-live", "kubuntu-netbook", "lucid", "netbook"),
            ("daily-live", "kubuntu-plasma5", "utopic", "desktop"),
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
            ("kubuntu-plasma5",
             ["http://releases.ubuntu.com/include/kubuntu-plasma5.css"]),
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
                "zsync</a> metafile)\" raring-desktop-amd64.iso.zsync\n"
                "AddDescription \"Desktop image for 64-bit PC (AMD64) "
                "computers (standard download)\" raring-desktop-amd64.iso\n"
                "AddDescription \"Desktop image for 32-bit PC (i386) "
                "computers (standard download)\" raring-desktop-i386.iso\n"
                "AddDescription \"Desktop image for 32-bit PC (i386) "
                "computers (file listing)\" raring-desktop-i386.list\n"
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
        self.config["DIST"] = "ubuntu-rtm/14.09"
        self.assertEqual(
            os.path.join(
                self.config.root, "scratch", "ubuntu-touch", "ubuntu-rtm",
                "14.09", "daily-preinstalled", "debian-cd", "armhf"),
            self.make_publisher(
                "ubuntu-touch", "daily-preinstalled").image_output("armhf"))

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
                self.config.root, "www", "full", "kubuntu", "daily-live"),
            self.make_publisher("kubuntu", "daily-live").publish_base)
        self.config["DIST"] = "hoary"
        self.assertEqual(
            os.path.join(
                self.config.root, "www", "full",
                "kubuntu", "hoary", "daily-live"),
            self.make_publisher("kubuntu", "daily-live").publish_base)
        self.config["DIST"] = "ubuntu-rtm/14.09"
        self.assertEqual(
            os.path.join(
                self.config.root, "www", "full",
                "ubuntu-touch", "ubuntu-rtm", "14.09", "daily-preinstalled"),
            self.make_publisher(
                "ubuntu-touch", "daily-preinstalled").publish_base)

    def test_size_limit(self):
        for project, dist, image_type, arch, size_limit in (
            ("edubuntu", None, "daily-preinstalled", "i386", 4700372992),
            ("edubuntu", None, "dvd", "i386", 4700372992),
            ("ubuntustudio", None, "dvd", "i386", 4700372992),
            ("ubuntu-mid", None, "daily-live", "i386", 1073741824),
            ("ubuntu-moblin-remix", None, "daily-live", "i386", 1073741824),
            ("kubuntu", None, "daily-live", "i386", 1283457024),
            ("kubuntu-active", None, "daily-live", "i386", 1283457024),
            ("kubuntu-plasma5", None, "daily-live", "i386", 1283457024),
            ("ubuntu", None, "dvd", "i386", 4700372992),
            ("ubuntu", "precise", "daily-live", "i386", 736665600),
            ("ubuntu", "quantal", "daily-live", "i386", 801000000),
            ("ubuntu", "raring", "daily-live", "i386", 835000000),
            ("ubuntu", "raring", "daily-live", "powerpc", 850000000),
            ("ubuntu", "saucy", "daily-live", "i386", 950000000),
            ("ubuntu", "saucy", "daily-live", "powerpc", 950000000),
            ("ubuntu", "trusty", "daily-live", "i386", 1073741824),
            ("ubuntu", "trusty", "daily-live", "powerpc", 1073741824),
            ("xubuntu", "quantal", "daily-live", "i386", 736665600),
            ("xubuntu", "raring", "daily-live", "i386", 1073741824),
            ("ubuntu-gnome", "saucy", "daily-live", "i386", 1073741824),
            ("ubuntu-mate", None, "daily-live", "amd64", 1073741824),
        ):
            if dist is not None:
                self.config["DIST"] = dist
            publisher = self.make_publisher(project, image_type)
            self.assertEqual(size_limit, publisher.size_limit(arch))

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

    def test_size_limit_extension_edubuntu(self):
        # size_limit_extension has special-casing for Edubuntu.
        publisher = self.make_publisher("edubuntu", "daily")
        self.assertEqual(
            publisher.size_limit("armhf+omap4"),
            publisher.size_limit_extension("armhf+omap4", "img"))
        self.assertEqual(
            publisher.size_limit("i386"),
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
        for arch in ("armel", "armhf", "hppa", "ia64", "lpia", "ppc64el"):
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
        self.config["DIST"] = "raring"
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
        self.config["DIST"] = "raring"
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
        self.config["DIST"] = "raring"
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
        self.config["DIST"] = "raring"
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
        self.config["DIST"] = "raring"
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
        self.config["DIST"] = "raring"
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
        self.config["DIST"] = "raring"
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

    @mock.patch("cdimage.tree.DailyTreePublisher.polish_directory")
    def test_mark_current_ignores_old_series(self, mock_polish_directory):
        self.config["DIST"] = "saucy"
        publisher = self.make_publisher("ubuntu", "daily-live")
        old_target_dir = os.path.join(publisher.publish_base, "20130321")
        for name in (
            "raring-desktop-amd64.iso", "raring-desktop-amd64.manifest",
            "raring-desktop-i386.iso", "raring-desktop-i386.manifest",
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
            ("kubuntu-active", "daily-live", "desktop", "Kubuntu Active"),
            ("kubuntu-plasma5", "daily-live", "desktop",
                "Kubuntu Plasma 5 Desktop"),
            ("edubuntu", "dvd", "dvd", "Edubuntu DVD"),
            ("xubuntu", "daily-live", "desktop", "Xubuntu Desktop"),
            ("ubuntu-server", "daily", "server", "Ubuntu Server"),
            ("ubuntustudio", "dvd", "dvd", "Ubuntu Studio DVD"),
            ("mythbuntu", "daily-live", "desktop", "Mythbuntu Desktop"),
            ("lubuntu", "daily", "alternate", "Lubuntu Alternate"),
            ("lubuntu", "daily-live", "desktop", "Lubuntu Desktop"),
            ("ubuntu-core", "daily", "core", "Ubuntu Core"),
            ("ubuntukylin", "daily-live", "desktop", "Ubuntu Kylin Desktop"),
            ("ubuntu-gnome", "daily-live", "desktop", "Ubuntu GNOME Desktop"),
            ("ubuntu-mate", "daily-live", "desktop", "Ubuntu Mate Desktop"),
            ("ubuntu-desktop-next", "daily-preinstalled",
                "preinstalled-desktop-next", "Ubuntu Desktop (Unity 8)"),
        ):
            # Use "daily" here to match bin/post-qa; qa_product shouldn't
            # use the publisher's image_type at all.
            publisher = self.make_publisher(project, "daily")
            self.assertEqual(
                ("%s i386" % product, "iso"),
                publisher.qa_product(
                    project, image_type, publish_type, "i386"))

    def test_qa_product_localized_tracker(self):
        publisher = self.make_publisher("ubuntu-zh_CN", "daily-live")
        self.assertEqual(
            ("Ubuntu Chinese Desktop i386", "localized-iso-china"),
            publisher.qa_product(
                "ubuntu-zh_CN", "daily-live", "desktop",
                "i386"))

    def test_qa_product_ubuntu_touch(self):
        publisher = self.make_publisher("ubuntu-touch", "daily-preinstalled")
        self.assertEqual(
            ("Ubuntu Touch armhf", "iso"),
            publisher.qa_product(
                "ubuntu-touch", "daily-preinstalled", "preinstalled-touch",
                "armhf"))

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
            ("kubuntu-active", "daily-live", "desktop", "Kubuntu Active"),
            ("kubuntu-plasma5", "daily-live", "desktop",
                "Kubuntu Plasma 5 Desktop"),
            ("edubuntu", "dvd", "dvd", "Edubuntu DVD"),
            ("xubuntu", "daily-live", "desktop", "Xubuntu Desktop"),
            ("ubuntu-server", "daily", "server", "Ubuntu Server"),
            ("ubuntustudio", "dvd", "dvd", "Ubuntu Studio DVD"),
            ("mythbuntu", "daily-live", "desktop", "Mythbuntu Desktop"),
            ("lubuntu", "daily", "alternate", "Lubuntu Alternate"),
            ("lubuntu", "daily-live", "desktop", "Lubuntu Desktop"),
            ("ubuntu-core", "daily", "core", "Ubuntu Core"),
            ("ubuntukylin", "daily-live", "desktop", "Ubuntu Kylin Desktop"),
            ("ubuntu-gnome", "daily-live", "desktop", "Ubuntu GNOME Desktop"),
            ("ubuntu-mate", "daily-live", "desktop", "Ubuntu Mate Desktop"),
            ("ubuntu-desktop-next/system-image", "daily-preinstalled",
                "preinstalled-desktop-next", "Ubuntu Desktop (Unity 8)"),
        ):
            # Use "daily" here to match bin/post-qa; qa_product shouldn't
            # use the publisher's image_type at all.
            publisher = self.make_publisher(project, "daily")
            self.assertEqual(
                (project, image_type, publish_type, "i386"),
                publisher.cdimage_project(
                    "%s i386" % product, "iso"))

    def test_cdimage_project_localized_tracker(self):
        publisher = self.make_publisher("ubuntu-zh_CN", "daily-live")
        self.assertEqual(
            ("ubuntu-zh_CN", "daily-live", "desktop", "i386"),
            publisher.cdimage_project(
                "Ubuntu Chinese Desktop i386", "localized-iso-china"))

    @mock_isotracker
    def test_post_qa(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        os.makedirs(os.path.join(publisher.publish_base, "20130221"))
        publisher.post_qa(
            "20130221", [
                "ubuntu/daily-live/raring-desktop-i386",
                "ubuntu/daily-live/raring-desktop-amd64",
            ])
        expected = [
            ["Ubuntu Desktop i386", "20130221", ""],
            ["Ubuntu Desktop amd64", "20130221", ""],
        ]
        self.assertEqual("iso-raring", isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

        os.makedirs(os.path.join(
            self.tree.project_base, "precise", "daily-live", "20130221"))
        publisher.post_qa(
            "20130221", [
                "ubuntu/precise/daily-live/precise-desktop-i386",
                "ubuntu/precise/daily-live/precise-desktop-amd64",
            ])
        expected = [
            ["Ubuntu Desktop i386", "20130221", ""],
            ["Ubuntu Desktop amd64", "20130221", ""],
        ]
        self.assertEqual("iso-precise", isotracker_module.tracker.target)
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
        self.assertEqual("iso-raring", isotracker_module.tracker.target)
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
        self.assertEqual("iso-precise", isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

    @mock_isotracker
    def test_post_qa_wrong_date(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        self.assertRaisesRegex(
            Exception, r"Cannot post images from nonexistent directory: .*",
            publisher.post_qa, "bad-date",
            ["ubuntu/daily-live/raring-desktop-i386"])

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
        metalink_builder = os.path.join(
            self.temp_dir, "MirrorMetalink", "build.py")
        mock_call.assert_called_once_with([
            metalink_builder, publisher.tree.directory, self.config.series,
            os.path.join(publisher.image_type_dir, "20130320"),
            publisher.tree.site_name
        ])

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    @mock.patch("cdimage.tree.DailyTreePublisher.make_metalink")
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
            "20120807",
            ["ubuntu/daily-live/%s-desktop-i386" % self.config.series])

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
            "Purging %s/daily images older than 1 day ..." % project,
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
        if self.config["UBUNTU_DEFAULTS_LOCALE"]:
            project = "ubuntu-zh_CN"
        else:
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
            "Purging %s/daily images older than 1 day ..." % project,
            "Purging %s/daily/20130318" % purge_desc,
        ])
        self.assertCountEqual(
            ["20130319", "20130320", "20130321", "current"],
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
        if self.config["UBUNTU_DEFAULTS_LOCALE"]:
            project = "ubuntu-zh_CN"
            purge_desc = "%s/%s" % (project, self.config.series)
        else:
            project = "ubuntu"
            purge_desc = project
        self.assertLogEqual([
            "Purging %s/daily images older than 1 day ..." % project,
            "Purging %s/daily/20130319" % purge_desc,
            "Purging %s/daily/20130319.1" % purge_desc,
        ])
        self.assertEqual([], os.listdir(publisher.publish_base))


class TestChinaDailyTree(TestDailyTree):
    def setUp(self):
        super(TestChinaDailyTree, self).setUp()
        self.tree = ChinaDailyTree(self.config, self.temp_dir)

    def test_default_directory(self):
        self.config.root = self.temp_dir
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "china-images"),
            ChinaDailyTree(self.config).directory)

    def test_project_base(self):
        self.config.root = self.temp_dir
        self.config["PROJECT"] = "ubuntu"
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "china-images"),
            ChinaDailyTree(self.config).project_base)

    def test_site_name(self):
        self.assertEqual("china-images.ubuntu.com", self.tree.site_name)


class TestChinaDailyTreePublisher(TestDailyTreePublisher):
    def setUp(self):
        super(TestChinaDailyTreePublisher, self).setUp()
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"

    def make_publisher(self, project, image_type, **kwargs):
        self.config["PROJECT"] = project
        self.tree = ChinaDailyTree(self.config)
        osextras.ensuredir(self.tree.project_base)
        publisher = ChinaDailyTreePublisher(self.tree, image_type, **kwargs)
        osextras.ensuredir(publisher.image_output("i386"))
        osextras.ensuredir(publisher.britney_report)
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

    def test_image_type_dir(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        for series in all_series:
            if series.distribution != "ubuntu":
                continue
            self.config["DIST"] = series
            self.assertEqual(
                os.path.join(series.name, "daily-live"),
                publisher.image_type_dir)

    def test_publish_base(self):
        publisher = self.make_publisher("ubuntu", "daily-live")
        for series in all_series:
            if series.distribution != "ubuntu":
                continue
            self.config["DIST"] = series
            self.assertEqual(
                os.path.join(
                    self.config.root, "www", "china-images",
                    series.name, "daily-live"),
                publisher.publish_base)

    def test_size_limit(self):
        for image_type, arch, size_limit in (
            ("dvd", "i386", 4700372992),
            ("daily-live", "i386", 850000000),
        ):
            publisher = self.make_publisher("ubuntu", image_type)
            self.assertEqual(size_limit, publisher.size_limit(arch))

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
            ["ubuntu-zh_CN/%s/daily-live/%s-desktop-i386" % (
                self.config.series, self.config.series)],
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
        self.assertEqual("localized-iso-china-raring",
                         isotracker_module.tracker.target)
        self.assertEqual(expected, isotracker_module.tracker.posted)

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("cdimage.tree.zsyncmake")
    @mock.patch("cdimage.tree.DailyTreePublisher.make_metalink")
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
            ["ubuntu-zh_CN/%s/daily-live/%s-desktop-i386" % (
                self.config.series, self.config.series)])


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

    def test_daily_dir_path_in_date(self):
        self.config["PROJECT"] = "ubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "ubuntu-server", "daily",
                "20130327"),
            self.get_publisher().daily_dir(
                "daily", "ubuntu-server/daily/20130327", "server"))

    def test_daily_dir_source(self):
        self.config["PROJECT"] = "ubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "daily", "20130327", "source"),
            self.get_publisher().daily_dir("daily", "20130327", "src"))

    def test_daily_base(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "quantal"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "quantal", "daily", "20130327",
                "i386"),
            self.get_publisher().daily_base(
                "quantal/daily", "20130327", "wubi", "i386"))
        self.config["DIST"] = "raring"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "daily-live", "20130327",
                "raring-desktop-i386"),
            self.get_publisher().daily_base(
                "daily-live", "20130327", "desktop", "i386"))

    def test_version(self):
        self.config["DIST"] = "raring"
        self.assertEqual("13.04", self.get_publisher().version)
        self.config["DIST"] = "dapper"
        self.assertEqual("6.06.2", self.get_publisher().version)

    def test_metalink_version(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.assertEqual("13.04", self.get_publisher().metalink_version)
        self.config["PROJECT"] = "kubuntu"
        self.config["DIST"] = "dapper"
        self.assertEqual(
            "kubuntu/6.06.2", self.get_publisher().metalink_version)

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
        md5sums_path = os.path.join(self.temp_dir, "MD5SUMS")
        with mkfile(md5sums_path) as md5sums:
            print("checksum  path", file=md5sums)
        self.capture_logging()
        self.get_publisher(dry_run=True).remove_checksum(self.temp_dir, "path")
        self.assertLogEqual(
            ["checksum-remove --no-sign %s path" % self.temp_dir])
        with open(md5sums_path) as md5sums:
            self.assertEqual("checksum  path\n", md5sums.read())
        self.capture_logging()
        self.get_publisher().remove_checksum(self.temp_dir, "path")
        self.assertLogEqual([])
        self.assertFalse(os.path.exists(md5sums_path))

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
        dist_path = os.path.join(self.temp_dir, "raring", "foo.iso")
        os.makedirs(os.path.dirname(dist_path))
        self.get_publisher().symlink(pool_path, dist_path)
        self.assertEqual(
            os.path.join(os.pardir, ".pool", "foo.iso"),
            os.readlink(dist_path))

    def test_hardlink(self):
        pool_path = os.path.join(self.temp_dir, ".pool", "foo.iso")
        touch(pool_path)
        dist_path = os.path.join(self.temp_dir, "raring", "foo.iso")
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

    def test_copy_jigdo(self):
        old_path = os.path.join(self.temp_dir, "raring-alternate-amd64.jigdo")
        new_path = os.path.join(
            self.temp_dir, "ubuntu-13.04-alternate-amd64.jigdo")
        with mkfile(old_path) as old:
            print("Filename=raring-alternate-amd64.jigdo", file=old)
            print("Template=raring-alternate-amd64.template", file=old)
        self.get_publisher().copy_jigdo(old_path, new_path)
        with open(new_path) as new:
            self.assertEqual(
                "Filename=ubuntu-13.04-alternate-amd64.jigdo\n"
                "Template=ubuntu-13.04-alternate-amd64.template\n",
                new.read())

    def test_mkemptydir(self):
        path = os.path.join(self.temp_dir, "dir")
        touch(os.path.join(path, "name"))
        self.get_publisher().mkemptydir(path)
        self.assertEqual([], os.listdir(path))

    # TODO: checksum_directory, metalink_checksum_directory untested

    def test_want_manifest(self):
        path = os.path.join(self.temp_dir, "foo.manifest")
        self.assertTrue(self.get_publisher().want_manifest("desktop", path))
        self.assertFalse(self.get_publisher().want_manifest("dvd", path))
        touch(path)
        self.assertTrue(self.get_publisher().want_manifest("dvd", path))
        self.assertFalse(self.get_publisher().want_manifest("alternate", path))

    def test_want_metalink(self):
        self.assertTrue(self.get_publisher().want_metalink("desktop"))
        self.assertFalse(self.get_publisher().want_metalink("netbook"))
        self.assertFalse(self.get_publisher().want_metalink(
            "preinstalled-netbook"))


def call_btmakemetafile_zsyncmake(command, *args, **kwargs):
    if command[0] == "btmakemetafile":
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
        self.config["DIST"] = "raring"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "releases", "raring", "release"),
            self.get_publisher().target_dir("daily", "20130327", "alternate"))
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "full", "kubuntu", "releases", "raring",
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
        self.config["DIST"] = "raring"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "torrent", "releases",
                "raring", "release", "desktop"),
            self.get_publisher().torrent_dir("daily-live", "desktop"))
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "torrent", "kubuntu", "releases",
                "raring", "beta-2", "desktop"),
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
            "btmakemetafile", "http://torrent.ubuntu.com:6969/announce",
            "--comment", "Ubuntu CD cdimage.ubuntu.com",
        ]
        mock_check_call.assert_has_calls([
            mock.call(command_base + [path], stdout=mock.ANY)
            for path in paths])

    def test_publish_release_prefixes(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.assertEqual(
            ("raring", "raring-beta2"),
            self.get_publisher(
                official="no", status="beta-2").publish_release_prefixes())
        self.config["PROJECT"] = "kubuntu"
        self.config["DIST"] = "dapper"
        self.assertEqual(
            ("kubuntu-6.06.2", "kubuntu-6.06.2"),
            self.get_publisher(official="named").publish_release_prefixes())

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_btmakemetafile_zsyncmake)
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
                "btmakemetafile", mock.ANY,
                "--comment", "Ubuntu CD cdimage.ubuntu.com",
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
    @mock.patch("subprocess.call", side_effect=call_btmakemetafile_zsyncmake)
    def test_publish_release_arch_ubuntu_desktop_no(self, mock_call, *args):
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
        publisher = self.get_publisher(official="no", status="rc")
        publisher.publish_release_arch(
            "daily-live", "20130327", "desktop", "i386")
        self.assertLogEqual([
            "Copying desktop-i386 image ...",
            "Creating torrent for %s/raring-desktop-i386.iso ..." % target_dir,
        ])
        self.assertCountEqual([
            "raring-desktop-i386.iso", "raring-desktop-i386.iso.torrent",
            "raring-desktop-i386.iso.zsync", "raring-desktop-i386.manifest",
        ], os.listdir(target_dir))
        target_base = os.path.join(target_dir, "raring-desktop-i386")
        self.assertFalse(os.path.islink("%s.iso" % target_base))
        self.assertFalse(os.path.islink("%s.manifest" % target_base))
        mock_call.assert_called_once_with([
            "btmakemetafile", mock.ANY,
            "--comment", "Ubuntu CD cdimage.ubuntu.com",
            "%s.iso" % target_base,
        ], stdout=mock.ANY)
        self.assertCountEqual([
            "raring-desktop-i386.iso", "raring-desktop-i386.iso.torrent",
        ], os.listdir(torrent_dir))
        torrent_base = os.path.join(torrent_dir, "raring-desktop-i386")
        self.assertEqual(
            os.stat("%s.iso" % target_base), os.stat("%s.iso" % torrent_base))
        self.assertEqual(
            os.stat("%s.iso.torrent" % target_base),
            os.stat("%s.iso.torrent" % torrent_base))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_btmakemetafile_zsyncmake)
    def test_publish_release_kubuntu_desktop_named(self, mock_call, *args):
        self.config["PROJECT"] = "kubuntu"
        self.config["CAPPROJECT"] = "Kubuntu"
        series = Series.latest()
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
                target_dir, series.version),
            "Copying desktop-i386 image ...",
            "Making i386 zsync metafile ...",
            "Creating torrent for %s/kubuntu-%s-desktop-i386.iso ..." % (
                target_dir, series.version),
            "Checksumming full tree ...",
            "No keys found; not signing images.",
            "Creating and publishing metalink files for the full tree ...",
            "No keys found; not signing images.",
            "Done!  Remember to sync-mirrors after checking that everything "
            "is OK.",
        ])
        self.assertCountEqual([
            ".htaccess", "FOOTER.html", "HEADER.html",
            "MD5SUMS", "SHA1SUMS", "SHA256SUMS",
            "kubuntu-%s-desktop-amd64.iso" % series.version,
            "kubuntu-%s-desktop-amd64.iso.torrent" % series.version,
            "kubuntu-%s-desktop-amd64.iso.zsync" % series.version,
            "kubuntu-%s-desktop-amd64.manifest" % series.version,
            "kubuntu-%s-desktop-i386.iso" % series.version,
            "kubuntu-%s-desktop-i386.iso.torrent" % series.version,
            "kubuntu-%s-desktop-i386.iso.zsync" % series.version,
            "kubuntu-%s-desktop-i386.manifest" % series.version,
        ], os.listdir(target_dir))
        self.assertCountEqual([
            "kubuntu-%s-desktop-amd64.iso" % series.version,
            "kubuntu-%s-desktop-amd64.iso.torrent" % series.version,
            "kubuntu-%s-desktop-i386.iso" % series.version,
            "kubuntu-%s-desktop-i386.iso.torrent" % series.version,
        ], os.listdir(torrent_dir))
        self.assertFalse(os.path.exists(os.path.join(
            self.temp_dir, "www", "simple")))


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
        self.config["DIST"] = "raring"
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "simple", "raring"),
            self.get_publisher().target_dir("daily", "20130327", "alternate"))
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "simple", "kubuntu", "raring", "source"),
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
        self.config["DIST"] = "raring"
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "simple", ".pool"),
            self.get_publisher().pool_dir("daily"))
        self.config["PROJECT"] = "kubuntu"
        self.config["DIST"] = "raring"
        self.assertEqual(
            os.path.join(self.temp_dir, "www", "simple", "kubuntu", ".pool"),
            self.get_publisher().pool_dir("daily"))

    def test_torrent_dir(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "raring"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "torrent", "simple",
                "raring", "desktop"),
            self.get_publisher().torrent_dir("daily-live", "desktop"))
        self.config["PROJECT"] = "kubuntu"
        self.assertEqual(
            os.path.join(
                self.temp_dir, "www", "torrent", "kubuntu", "simple",
                "raring", "desktop"),
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
            "btmakemetafile", "http://torrent.ubuntu.com:6969/announce",
            "--announce_list",
            ("http://torrent.ubuntu.com:6969/announce|"
                "http://ipv6.torrent.ubuntu.com:6969/announce"),
            "--comment", "Ubuntu CD releases.ubuntu.com",
        ]
        mock_check_call.assert_has_calls([
            mock.call(command_base + [path], stdout=mock.ANY)
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
    @mock.patch("subprocess.call", side_effect=call_btmakemetafile_zsyncmake)
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
                "btmakemetafile", mock.ANY,
                "--announce_list", mock.ANY,
                "--comment", "Ubuntu CD releases.ubuntu.com",
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
            os.stat("%s.iso" % pool_base), os.stat("%s.iso" % torrent_base))
        self.assertEqual(
            os.stat("%s.iso.torrent" % target_base),
            os.stat("%s.iso.torrent" % torrent_base))

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_btmakemetafile_zsyncmake)
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
            self.temp_dir, "www", "simple", "raring")))
        self.assertFalse(os.path.exists(os.path.join(
            self.temp_dir, "www", "torrent", "simple", "raring", "desktop")))
        pool_base = os.path.join(pool_dir, "ubuntu-13.04-rc-desktop-i386")
        mock_call.assert_called_once_with([
            "zsyncmake", "-o", "%s.iso.zsync" % pool_base,
            "-u", "ubuntu-13.04-rc-desktop-i386.iso",
            "%s.iso" % pool_base,
        ])

    @mock.patch("cdimage.osextras.find_on_path", return_value=True)
    @mock.patch("subprocess.call", side_effect=call_btmakemetafile_zsyncmake)
    def test_publish_release_kubuntu_desktop_yes(self, mock_call, *args):
        self.config["PROJECT"] = "kubuntu"
        self.config["CAPPROJECT"] = "Kubuntu"
        series = Series.latest()
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
                target_dir, series.version),
            "Copying desktop-i386 image ...",
            "Making i386 zsync metafile ...",
            "Creating torrent for %s/kubuntu-%s-desktop-i386.iso ..." % (
                target_dir, series.version),
            "Checksumming simple tree (pool) ...",
            "No keys found; not signing images.",
            "Checksumming simple tree (%s) ..." % series,
            "No keys found; not signing images.",
            "Creating and publishing metalink files for the simple tree "
            "(%s) ..." % series,
            "No keys found; not signing images.",
            "Done!  Remember to sync-mirrors after checking that everything "
            "is OK.",
        ])
        self.assertCountEqual([
            "MD5SUMS", "SHA1SUMS", "SHA256SUMS",
            "kubuntu-%s-desktop-amd64.iso" % series.version,
            "kubuntu-%s-desktop-amd64.iso.zsync" % series.version,
            "kubuntu-%s-desktop-amd64.manifest" % series.version,
            "kubuntu-%s-desktop-i386.iso" % series.version,
            "kubuntu-%s-desktop-i386.iso.zsync" % series.version,
            "kubuntu-%s-desktop-i386.manifest" % series.version,
        ], os.listdir(pool_dir))
        self.assertCountEqual([
            ".htaccess", "FOOTER.html", "HEADER.html",
            "MD5SUMS", "SHA1SUMS", "SHA256SUMS",
            "kubuntu-%s-desktop-amd64.iso" % series.version,
            "kubuntu-%s-desktop-amd64.iso.torrent" % series.version,
            "kubuntu-%s-desktop-amd64.iso.zsync" % series.version,
            "kubuntu-%s-desktop-amd64.manifest" % series.version,
            "kubuntu-%s-desktop-i386.iso" % series.version,
            "kubuntu-%s-desktop-i386.iso.torrent" % series.version,
            "kubuntu-%s-desktop-i386.iso.zsync" % series.version,
            "kubuntu-%s-desktop-i386.manifest" % series.version,
        ], os.listdir(target_dir))
        self.assertCountEqual([
            "kubuntu-%s-desktop-amd64.iso" % series.version,
            "kubuntu-%s-desktop-amd64.iso.torrent" % series.version,
            "kubuntu-%s-desktop-i386.iso" % series.version,
            "kubuntu-%s-desktop-i386.iso.torrent" % series.version,
        ], os.listdir(torrent_dir))
        self.assertFalse(os.path.exists(os.path.join(
            self.temp_dir, "www", "full", "kubuntu", "releases")))
        self.assertTrue(os.path.exists(os.path.join(
            self.temp_dir, "www", "simple", ".manifest")))
        self.assertTrue(os.path.isdir(os.path.join(
            self.temp_dir, "www", "simple", ".trace")))
