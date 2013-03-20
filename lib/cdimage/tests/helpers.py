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

"""Testing helpers."""

from __future__ import print_function

__metaclass__ = type

from logging.handlers import BufferingHandler
import os
import shutil
import subprocess
import tempfile
from textwrap import dedent
try:
    import unittest2 as unittest
except ImportError:
    import unittest

from cdimage import osextras
from cdimage.log import logger


class UnlimitedBufferHandler(BufferingHandler):
    """A buffering handler that never flushes any records."""

    def __init__(self):
        BufferingHandler.__init__(self, 0)

    def shouldFlush(self, record):
        return False


class TestCase(unittest.TestCase):
    def setUp(self):
        super(TestCase, self).setUp()
        self.temp_dir = None
        self.save_env = dict(os.environ)

    def tearDown(self):
        for key in set(os.environ.keys()) - set(self.save_env.keys()):
            del os.environ[key]
        for key, value in os.environ.items():
            if value != self.save_env[key]:
                os.environ[key] = self.save_env[key]
        for key in set(self.save_env.keys()) - set(os.environ.keys()):
            os.environ[key] = self.save_env[key]

    def use_temp_dir(self):
        if self.temp_dir is not None:
            return self.temp_dir
        self.temp_dir = tempfile.mkdtemp(prefix="cdimage")
        self.addCleanup(shutil.rmtree, self.temp_dir)
        return self.temp_dir

    def capture_logging(self):
        self.handler = UnlimitedBufferHandler()
        logger.handlers = []
        logger.addHandler(self.handler)
        logger.propagate = False

    def captured_log_messages(self):
        return [record.getMessage() for record in self.handler.buffer]

    def assertLogEqual(self, expected):
        self.assertEqual(expected, self.captured_log_messages())

    def make_deb(self, path, section, priority, files={}):
        build_dir = os.path.join(self.temp_dir, "make_deb")
        os.mkdir(build_dir)
        try:
            base = os.path.basename(path).split(".", 1)[0]
            name, version, arch = base.split("_")
            control_dir = os.path.join(build_dir, "DEBIAN")
            os.mkdir(control_dir)
            with open(os.path.join(control_dir, "control"), "w") as control:
                print(dedent("""\
                    Package: %s
                    Version: %s
                    Architecture: %s
                    Section: %s
                    Priority: %s
                    Maintainer: Fake Maintainer <fake@example.org>
                    Description: fake package""") % (
                    name, version, arch, section, priority),
                    file=control)
            for file_path, file_contents in files.items():
                rel_path = os.path.join(
                    build_dir, os.path.relpath(file_path, "/"))
                osextras.ensuredir(os.path.dirname(rel_path))
                with open(rel_path, "wb") as fp:
                    fp.write(file_contents)
            with open("/dev/null", "w") as devnull:
                subprocess.check_call(
                    ["dpkg-deb", "-b", build_dir, path], stdout=devnull)
        finally:
            shutil.rmtree(build_dir)

    # Monkey-patch for Python 2/3 compatibility.
    if not hasattr(unittest.TestCase, 'assertCountEqual'):
        assertCountEqual = unittest.TestCase.assertItemsEqual
    if not hasattr(unittest.TestCase, 'assertRegex'):
        assertRegex = unittest.TestCase.assertRegexpMatches


def touch(path):
    with open(path, "a"):
        pass
