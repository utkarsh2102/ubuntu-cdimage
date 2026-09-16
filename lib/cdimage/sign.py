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

"""Sign a file with the cdimage key."""

import os
import subprocess

from cdimage import osextras
from cdimage.log import logger


def can_sign(config):
    lp_signing_conf = config["LP_SIGN_CONFIG"]
    if os.path.exists(lp_signing_conf):
        return True
    else:
        logger.warning("LP_SIGN_CONFIG set but not found.")
        return False


def sign_cdimage(config, path):
    if not can_sign(config):
        return False

    logger.info("Signing %s using LP signing service", path)
    with open("%s.gpg" % path, "wb") as outfile:
        try:
            subprocess.check_call(
                ["lp-sign", "--config-file", config["LP_SIGN_CONFIG"], path],
                stdout=outfile,
            )
        except subprocess.CalledProcessError:
            osextras.unlink_force("%s.gpg" % path)
            raise
    return True
