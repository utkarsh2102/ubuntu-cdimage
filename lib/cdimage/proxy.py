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

"""Proxy handling."""

from functools import partial
import os
import subprocess


def _select_proxy(config, call_site):
    path = os.path.join(config.root, "production", "proxies")
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                if line.startswith("#"):
                    continue
                words = line.split()
                if len(words) >= 2 and words[0] == call_site:
                    return words[1]
    return None


def _child_set_http_proxy(http_proxy):
    if http_proxy is None:
        os.environ.pop("http_proxy", None)
    else:
        os.environ["http_proxy"] = http_proxy


def _set_preexec_fn(config, call_site, call_kwargs):
    http_proxy = _select_proxy(config, call_site)
    if http_proxy is None:
        return
    if http_proxy == "unset":
        call_kwargs["preexec_fn"] = partial(_child_set_http_proxy, None)
    else:
        call_kwargs["preexec_fn"] = partial(_child_set_http_proxy, http_proxy)


def proxy_call(config, call_site, *args, **kwargs):
    _set_preexec_fn(config, call_site, kwargs)
    return subprocess.call(*args, **kwargs)


def proxy_check_call(config, call_site, *args, **kwargs):
    _set_preexec_fn(config, call_site, kwargs)
    subprocess.check_call(*args, **kwargs)
