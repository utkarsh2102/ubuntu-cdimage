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

"""Germinate handling."""

from __future__ import print_function

__metaclass__ = type


class GerminateOutput:
    def __init__(self, config, structure):
        self.config = config
        self.structure = structure
        self._parse_structure()

    def _parse_structure(self):
        self._seeds = {}
        # TODO: move to collections.OrderedDict with 2.7
        self._seed_order = []
        with open(self.structure) as structure:
            for line in structure:
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                seed, inherit = line.split(":", 1)
                self._seeds[seed] = inherit.split()
                self._seed_order.append(seed)

    def _expand_inheritance(self, seed, inherit):
        for s in self._seeds.get(seed, ()):
            self._expand_inheritance(s, inherit)
        if seed not in inherit:
            inherit.append(seed)

    def _inheritance(self, seed):
        inherit = []
        self._expand_inheritance(seed, inherit)
        return inherit

    def _without_inheritance(self, subtract, seeds):
        subtract_inherit = self._inheritance(subtract)
        remaining = set(seeds) - set(subtract_inherit)
        return [seed for seed in seeds if seed in remaining]

    def list_seeds(self, mode):
        project = self.config["PROJECT"]
        series = self.config["DIST"]

        if mode == "all":
            for seed in self._seed_order:
                yield seed
        elif mode == "tasks":
            ship = "ship"
            if "ship-addon" in self._seeds:
                ship = "ship-addon"
            if project == "ubuntu-server":
                if series <= "breezy":
                    pass
                elif series <= "dapper":
                    ship = "server"
                else:
                    ship = "server-ship"
            elif project == "kubuntu-active":
                ship = "active-ship"
            for seed in self._inheritance(ship):
                yield seed
            if self.config["CDIMAGE_DVD"]:
                if series >= "edgy":
                    # TODO cjwatson 2007-04-18: hideous hack to fix DVD tasks
                    yield "dns-server"
                    yield "lamp-server"
        elif mode == "installer":
            if self.config["CDIMAGE_INSTALL_BASE"]:
                yield "installer"
            if self.config["CDIMAGE_LIVE"]:
                if series >= "hoary" and series <= "breezy":
                    yield "casper"
        elif mode == "debootstrap":
            if series <= "hoary":
                yield "base"
            elif series <= "feisty":
                yield "minimal"
            else:
                yield "required"
                yield "minimal"
        elif mode == "base":
            if series <= "hoary":
                yield "base"
            elif series <= "breezy":
                yield "minimal"
                yield "standard"
            elif series <= "feisty":
                yield "boot"
                yield "minimal"
                yield "standard"
            else:
                yield "boot"
                yield "required"
                yield "minimal"
                yield "standard"
        elif mode == "ship-live":
            if project == "kubuntu-active":
                yield "ship-active-live"
            elif project == "ubuntu-server":
                seeds = self._inheritance("server-ship")
                seeds = self._without_inheritance("minimal", seeds)
                for seed in seeds:
                    yield seed
            else:
                if series >= "dapper":
                    yield "ship-live"
        elif mode == "addon":
            ship = self._inheritance("ship")
            ship_addon = self._inheritance("ship-addon")
            for seed in ship_addon:
                if seed not in ship:
                    yield seed
        elif mode == "dvd":
            if series <= "gutsy":
                for seed in self._inheritance("supported"):
                    yield seed
            elif series <= "karmic":
                for seed in self._inheritance("dvd"):
                    yield seed
            else:
                if project == "edubuntu":
                    # no inheritance; most of this goes on the live filesystem
                    yield "dvd"
                    yield "ship-live"
                elif project == "ubuntu" and series >= "oneiric":
                    # no inheritance; most of this goes on the live filesystem
                    yield "usb-langsupport"
                    yield "usb-ship-live"
                elif project == "ubuntustudio" and series >= "precise":
                    # no inheritance; most of this goes on the live filesystem
                    yield "dvd"
                else:
                    for seed in self._inheritance("dvd"):
                        yield seed
