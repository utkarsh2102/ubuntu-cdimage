#! /usr/bin/python
"""Unit tests for cdimage.config."""

from __future__ import print_function

import os
import shutil
import tempfile
from textwrap import dedent
try:
    from test.support import EnvironmentVarGuard
except ImportError:
    from test.test_support import EnvironmentVarGuard
try:
    import unittest2 as unittest
except ImportError:
    import unittest

from cdimage.config import Config

__metaclass__ = type


class TestConfig(unittest.TestCase):
    def setUp(self):
        super(TestConfig, self).setUp()
        self.temp_dir = None

    def use_temp_dir(self):
        if self.temp_dir is not None:
            return
        self.temp_dir = tempfile.mkdtemp(prefix="cdimage")
        self.addCleanup(shutil.rmtree, self.temp_dir)

    def test_default_root(self):
        with EnvironmentVarGuard() as env:
            env.pop("CDIMAGE_ROOT", None)
            config = Config(read=False)
            self.assertEqual("/srv/cdimage.ubuntu.com", config.root)

    def test_root_from_environment(self):
        with EnvironmentVarGuard() as env:
            env["CDIMAGE_ROOT"] = "/path"
            config = Config(read=False)
            self.assertEqual("/path", config.root)

    def test_default_values(self):
        config = Config(read=False)
        self.assertEqual("", config["PROJECT"])

    def test_read_shell(self):
        self.use_temp_dir()
        with EnvironmentVarGuard() as env:
            env["CDIMAGE_ROOT"] = self.temp_dir
            os.mkdir(os.path.join(self.temp_dir, "etc"))
            with open(os.path.join(self.temp_dir, "etc", "config"), "w") as f:
                print(dedent("""\
                    #! /bin/sh
                    PROJECT=ubuntu
                    CAPPROJECT=Ubuntu
                    """), file=f)
            config = Config()
            self.assertEqual("ubuntu", config["PROJECT"])
            self.assertEqual("Ubuntu", config["CAPPROJECT"])
