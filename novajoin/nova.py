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

"""Handle communication with Nova."""

from novaclient import client
from novaclient import exceptions
from oslo_config import cfg
from oslo_log import log as logging


CONF = cfg.CONF

LOG = logging.getLogger(__name__)

NOVA_APIVERSION = 2.1


class NovaClient(object):
    """Wrapper around nova client."""

    def __init__(self, session):

        self.version = NOVA_APIVERSION
        self.client = self._nova_client(session)

    def _nova_client(self, session):
        """Instantiate a new novaclient.Client object."""

        return client.Client(str(self.version), session=session)


def get_instance(instance_id, session):
    client = NovaClient(session)
    try:
        return client.client.servers.get(instance_id)
    except exceptions.NotFound:
        return None
