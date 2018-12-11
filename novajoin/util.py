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

import json

from oslo_config import cfg
from oslo_log import log as logging
import six

from novajoin.errors import ConfigurationError
from novajoin.ipa import ipalib_imported
if ipalib_imported:
    from ipalib import api

CONF = cfg.CONF

LOG = logging.getLogger(__name__)


def get_domain():
    """Retrieve the domain for creating a FQDN.

       By default this will come out of the IPA API but it can be
       overridden by setting domain in the DEFAULT section of the
       configuration file.
    """
    if CONF.domain:
        return CONF.domain

    if ipalib_imported:
        return api.env.domain

    raise ConfigurationError("Unable to get domain")


def get_fqdn(hostname, project_name=None):
    domain = get_domain()
    try:
        project_subdomain = CONF.project_subdomain
    except cfg.NoSuchOptError:
        return '%s.%s' % (hostname, domain)
    if project_subdomain:
        LOG.warn('Project subdomain is experimental')
        return '%s.%s.%s' % (hostname, project_name, domain)
    else:
        return '%s.%s' % (hostname, domain)


def get_compact_services(metadata):
    """Retrieve and convert the compact_services from instance metadata.

    This converts the new compact services format to the old/internal one.
    The old format looks like:

    "compact_services": {
        "http": ["internalapi", "ctlplane", "storage"],
        "rabbitmq": ["internalapi", "ctlplane"]
    }

    The new format contains service names inside the primary key:

    "compact_services_http": ["internalapi", "ctlplane", "storage"],
    "compact_services_rabbitmq": ["internalapi", "ctlplane"]
    """
    # compact key-per-service
    compact_services = {key.split('_', 2)[-1]: json.loads(value)
                        for key, value in six.iteritems(metadata)
                        if key.startswith('compact_service_')}
    if compact_services:
        return compact_services
    # legacy compact json format
    if 'compact_services' in metadata:
        return json.loads(metadata['compact_services'])

    return None
