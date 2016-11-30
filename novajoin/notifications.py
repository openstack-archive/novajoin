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
#
# To enable in nova, put this into [DEFAULT]
# notification_driver = messaging
# notification_topic = notifications
# notify_on_state_change = vm_state

import sys
import time

from neutronclient.v2_0 import client as neutron_client
from novaclient import client as nova_client
from novajoin import config
from novajoin.ipa import IPAClient
from novajoin.keystone_client import get_session
from novajoin.keystone_client import register_keystoneauth_opts
from oslo_log import log as logging
import oslo_messaging
from oslo_serialization import jsonutils


CONF = config.CONF

LOG = logging.getLogger(__name__)


def novaclient():
    session = get_session()
    return nova_client.Client('2.1', session=session)


def neutronclient():
    session = get_session()
    return neutron_client.Client(session=session)


class NotificationEndpoint(object):

    filter_rule = oslo_messaging.notify.filter.NotificationFilter(
        publisher_id='^compute.*|^network.*',
        event_type='^compute.instance.create.end|'
                   '^compute.instance.delete.end|'
                   '^network.floating_ip.(dis)?associate|'
                   '^floatingip.update.end')

    def __init__(self):
        self.ipaclient = IPAClient()

    def _generate_hostname(self, hostname):
        # FIXME: Don't re-calculate the hostname, fetch it from somewhere
        project = 'foo'
        if CONF.project_subdomain:
            host = '%s.%s.%s' % (hostname, project, CONF.domain)
        else:
            host = '%s.%s' % (hostname, CONF.domain)
        return host

    def info(self, ctxt, publisher_id, event_type, payload, metadata):
        LOG.debug('notification:')
        LOG.debug(jsonutils.dumps(payload, indent=4))

        LOG.debug("publisher: %s, event: %s, metadata: %s", publisher_id,
                  event_type, metadata)

        if event_type == 'compute.instance.create.end':
            hostname = self._generate_hostname(payload.get('hostname'))
            instance_id = payload.get('instance_id')
            LOG.info("Add new host %s (%s)", instance_id, hostname)
        elif event_type == 'compute.instance.delete.end':
            hostname = self._generate_hostname(payload.get('hostname'))
            instance_id = payload.get('instance_id')
            LOG.info("Delete host %s (%s)", instance_id, hostname)
            self.ipaclient.delete_host(hostname, {})
        elif event_type == 'network.floating_ip.associate':
            floating_ip = payload.get('floating_ip')
            LOG.info("Associate floating IP %s" % floating_ip)
            nova = novaclient()
            server = nova.servers.get(payload.get('instance_id'))
            if server:
                self.ipaclient.add_ip(server.get, floating_ip)
            else:
                LOG.error("Could not resolve %s into a hostname",
                          payload.get('instance_id'))
        elif event_type == 'network.floating_ip.disassociate':
            floating_ip = payload.get('floating_ip')
            LOG.info("Disassociate floating IP %s" % floating_ip)
            nova = novaclient()
            server = nova.servers.get(payload.get('instance_id'))
            if server:
                self.ipaclient.remove_ip(server.name, floating_ip)
            else:
                LOG.error("Could not resolve %s into a hostname",
                          payload.get('instance_id'))
        elif event_type == 'floatingip.update.end':  # Neutron
            floatingip = payload.get('floatingip')
            floating_ip = floatingip.get('floating_ip_address')
            port_id = floatingip.get('port_id')
            LOG.info("Neutron floating IP associate: %s" % floating_ip)
            nova = novaclient()
            neutron = neutronclient()
            search_opts = {'id': port_id}
            ports = neutron.list_ports(**search_opts).get('ports')
            if len(ports) == 1:
                device_id = ports[0].get('device_id')
                if device_id:
                    server = nova.servers.get(device_id)
                    if server:
                        self.ipaclient.add_ip(server.name, floating_ip)
            else:
                LOG.error("Expected 1 port, got %d", len(ports))
        else:
            LOG.error("Status update or unknown")


def main():
    register_keystoneauth_opts(CONF)
    CONF(sys.argv[1:], version='1.0.10',
         default_config_files=config.find_config_files())
    logging.setup(CONF, 'join')

    transport = oslo_messaging.get_transport(CONF)
    targets = [oslo_messaging.Target(topic='notifications')]
    endpoints = [NotificationEndpoint()]

    server = oslo_messaging.get_notification_listener(transport,
                                                      targets,
                                                      endpoints,
                                                      executor='threading',
                                                      allow_requeue=True)
    LOG.info("Starting")
    server.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        LOG.info("Stopping, be patient")
        server.stop()
        server.wait()
