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

"""Extra OS-level utility functions."""

import errno
import os

try:
    from shlex import quote as shell_quote
except ImportError:
    from pipes import quote as shell_quote
import shutil
import subprocess

from cdimage import retry
from cdimage.log import logger
from cdimage.proxy import proxy_call


def ensuredir(directory):
    if not os.path.isdir(directory):
        os.makedirs(directory)


def mkemptydir(directory):
    try:
        shutil.rmtree(directory)
    except OSError:
        pass
    ensuredir(directory)


def listdir_force(directory):
    try:
        return os.listdir(directory)
    except OSError as e:
        if e.errno == errno.ENOENT:
            return []
        raise


def unlink_force(path):
    """Unlink path, without worrying about whether it exists."""
    try:
        os.unlink(path)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise


def symlink_force(source, link_name):
    """Create symlink link_name -> source, even if link_name exists."""
    unlink_force(link_name)
    os.symlink(source, link_name)


def link_force(source, link_name):
    """Create hard link link_name -> source, even if link_name exists."""
    unlink_force(link_name)
    os.link(source, link_name)


def find_on_path(command):
    """Is command on the executable search path?"""
    if "PATH" not in os.environ:
        return False
    path = os.environ["PATH"]
    for element in path.split(os.pathsep):
        if not element:
            continue
        filename = os.path.join(element, command)
        if os.path.isfile(filename) and os.access(filename, os.X_OK):
            return True
    return False


def waitpid_retry(*args):
    """Run waitpid, retrying on EINTR."""
    while True:
        try:
            return os.waitpid(*args)
        except OSError as e:
            if e.errno != errno.EINTR:
                raise


class FetchError(Exception):
    """An attempt to fetch a file from a remote system failed."""


DEFAULT_FETCH_RETRIES = 3
DEFAULT_FETCH_RETRY_DELAY = 60


def _fetch_once(config, source, target, no_proxy=False):
    """Run one wget for source -> target.

    Any partial download is deliberately left in place: --continue can resume
    it on the next attempt, and only fetch() knows when there will not be one.
    """
    # Match lazr.restfulclient, for convenience when working with
    # development instances of Launchpad.
    no_check_certificate = bool(
        os.environ.get("LP_DISABLE_SSL_CERTIFICATE_VALIDATION", False)
    )

    # This should arguably use urllib2/urllib.request or similar instead.
    command = [
        "wget",
        "-nv",
        "--retry-connrefused",  # retry in case of fatal errors
        # wget treats every HTTP error status as fatal unless told otherwise,
        # so without this it gives up on the first one.  A single librarian
        # 502 on a tiny .manifest has thrown away whole multi-gigabyte images
        # that had already downloaded successfully.
        "--retry-on-http-error=429,500,502,503,504",
        "--retry-on-host-error",  # DNS not answering is transient too
        "--waitretry=15",  # wait up to 15 seconds between retries
        "--read-timeout=120",  # 120 seconds of not data received before retry
        "--timeout=120",  # wait 120 seconds before initial connection timeout
        "-t",
        "20",  # retry 20 times in total
        "--continue",  # pick up where the last attempt left off
    ]
    if no_proxy:
        command.append("--no-proxy")
    if no_check_certificate:
        command.append("--no-check-certificate")
    command.extend([source, "-O", target])
    ret = proxy_call(config, "fetch", command)
    if ret != 0:
        command_str = "wget -nv"
        if no_proxy:
            command_str += " --no-proxy"
        if no_check_certificate:
            command_str += " --no-check-certificate"
        command_str += " '%s' -O '%s'" % (source, target)
        raise FetchError("%s returned %d" % (command_str, ret))


def fetch(config, source, target):
    """Fetch a file from a remote system, retrying transient failures.

    wget has a retry budget of its own, but it runs out after a few minutes
    and Launchpad outages last longer than that.  Wrapping it in a second,
    slower loop buys roughly twenty minutes in total, which is the difference
    between waiting for the librarian to come back and losing the day's image.
    """
    if not source:
        raise FetchError("empty source URL (downloading to %s)" % target)

    if source.startswith("/"):
        os.link(source, target)
        return

    tries = retry.int_setting(config, "CDIMAGE_FETCH_RETRIES", DEFAULT_FETCH_RETRIES)
    delay = retry.int_setting(
        config, "CDIMAGE_FETCH_RETRY_DELAY", DEFAULT_FETCH_RETRY_DELAY
    )
    attempt = 0

    def attempt_fetch():
        nonlocal attempt
        attempt += 1
        # Every librarian failure we have seen has been "502 Proxy Error", so
        # if the proxy itself is the broken part then going around it on the
        # last attempt is the only thing left that might work.
        last = tries > 1 and attempt >= tries
        if last:
            logger.info("Retrying %s without the proxy", source)
        _fetch_once(config, source, target, no_proxy=last)

    try:
        retry.retry_call(
            attempt_fetch,
            description="librarian fetch",
            tries=tries,
            delay=delay,
            transient=(FetchError,),
        )
    except FetchError:
        # Only now is the partial file useless.  Up to this point --continue
        # could still have resumed it.
        unlink_force(target)
        raise


def _read_nullsep_output(command):
    raw = subprocess.Popen(
        command, stdout=subprocess.PIPE, universal_newlines=True
    ).communicate()[0]
    out = {}
    for line in raw.split("\0"):
        try:
            key, value = line.split("=", 1)
            out[key] = value
        except ValueError:
            continue
    return out


def read_shell_config(config_path=None, allowed_keys=[]):
    commands = []
    if config_path is not None:
        commands.append(". %s" % shell_quote(config_path))
    commands.append("cat /proc/self/environ")
    for key in allowed_keys:
        commands.append(
            'test -z "${KEY+x}" || printf \'%s\\0\' "KEY=$KEY"'.replace("KEY", key)
        )
    env = _read_nullsep_output(["sh", "-c", "; ".join(commands)])
    for key, value in env.items():
        yield key, value


def pid_exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        raise
