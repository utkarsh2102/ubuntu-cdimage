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


def _set_proxy_env(config, call_site, call_kwargs):
    http_proxy = _select_proxy(config, call_site)
    if http_proxy is None:
        return
    env = dict(call_kwargs.get("env", os.environ))
    if http_proxy == "unset":
        env.pop("http_proxy", None)
    else:
        env["http_proxy"] = http_proxy
    call_kwargs["env"] = env


def proxy_call(config, call_site, *args, **kwargs):
    _set_proxy_env(config, call_site, kwargs)
    return subprocess.call(*args, **kwargs)


def proxy_check_call(config, call_site, *args, **kwargs):
    _set_proxy_env(config, call_site, kwargs)
    subprocess.check_call(*args, **kwargs)
