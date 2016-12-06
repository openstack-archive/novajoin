# Copyright 2016 Red Hat, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Utility functions shared between notify and server"""

from oslo_config import cfg
from oslo_log import log as logging

try:
    from ipalib import api
    ipalib_imported = True
except ImportError:
    # ipalib/ipapython are not available in PyPi yet, don't make it
    # a showstopper for the tests.
    ipalib_imported = False

CONF = cfg.CONF

LOG = logging.getLogger(__name__)


def get_domain():
    """Retrieve the domain for creating a FQDN.

       By default this will come out of the IPA API but it can be
       overridden by setting domain in the DEFAULT section of the
       configuration file.
    """
    domain = None

    try:
        domain = CONF.domain
    except cfg.NoSuchOptError:
        pass

    if domain:
        return domain

    return api.env.domain
