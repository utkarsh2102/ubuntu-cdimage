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

"""E-mail notifications."""

from __future__ import print_function

__metaclass__ = type

import os
import subprocess
import sys

from cdimage.log import logger


if sys.version >= "3":
    import io
    text_file_type = io.TextIOBase
else:
    text_file_type = file


def _notify_addresses_path(config):
    paths = [
        os.path.join(config.root, "production", "notify-addresses"),
        os.path.join(config.root, "etc", "notify-addresses"),
    ]
    for path in paths:
        if os.path.exists(path):
            return path
    else:
        return None


def get_notify_addresses(config, project=None):
    path = _notify_addresses_path(config)
    if path is None:
        return []
    with open(path) as fp:
        all_addresses = []
        for line in fp:
            this_project, addresses = line.split(None, 1)
            if (this_project == "ALL" or
                    (project is not None and this_project == project)):
                all_addresses.extend(addresses.split())
        return all_addresses


def send_mail(subject, generator, recipients, body, dry_run=False):
    if dry_run:
        logger.info("Would send mail to: %s" % ", ".join(recipients))
        logger.info("")
        logger.info("Subject: %s" % subject)
        logger.info("X-Generated-By: %s" % generator)
        logger.info("")
        if isinstance(body, text_file_type):
            for line in body:
                logger.info(line.rstrip("\n"))
        else:
            for line in body.splitlines():
                logger.info(line)
        logger.info("")
    else:
        command = [
            "mail", "-s", subject, "-a", "X-Generated-By: %s" % generator]
        command.extend(recipients)
        if isinstance(body, text_file_type):
            mailer = subprocess.Popen(command, stdin=body)
        else:
            mailer = subprocess.Popen(command, stdin=subprocess.PIPE)
            if bytes is not str and isinstance(body, str):
                body = body.encode()
            mailer.stdin.write(body)
            mailer.stdin.close()
        mailer.wait()
