#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (C) 2013 Canonical Ltd.
# Author: Stéphane Graber <stgraber@ubuntu.com>

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

"""QATracker integration functions."""

import xmlrpclib

from cdimage.log import logger
from cdimage.tree import Publisher, Tree


def tracker_set_rebuild_status(config, current_state, new_state,
                               arches=None):

    if not isinstance(arches, list):
        arches = [arches]

    # Only import it here as we need to have the right paths in sys.path
    try:
        from isotracker import ISOTracker
    except ImportError:
        # Become a no-op if the isotracker module can't be found
        return

    if not arches:
        arches = config.arches

    tree = Tree.get_daily(config)
    publisher = Publisher.get_daily(tree, "daily")

    # Build a dict of tracker instance and product list
    qa_products = {}
    for arch in arches:
        qaproduct = publisher.qa_product(config.project, config.image_type,
                                         None, arch)

        if not qaproduct:
            continue

        if qaproduct[1] not in qa_products:
            qa_products[qaproduct[1]] = []

        qa_products[qaproduct[1]].append(qaproduct[0])

    # Iterate through the trackers and set the new status
    for instance, products in qa_products.items():
        try:
            tracker = ISOTracker(
                target="%s-%s" % (instance, config.full_series))
        except xmlrpclib.Error as e:
            logger.warning("Unable to contact tracker: %s" % e)
            continue

        for rebuild in tracker.qatracker.get_rebuilds(current_state):
            if rebuild.series_title.lower() != config.full_series:
                continue

            if rebuild.product_title in products:
                rebuild.status = new_state
                rebuild.save()
