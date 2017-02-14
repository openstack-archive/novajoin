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

import json
import sys
import time

from neutronclient.v2_0 import client as neutron_client
from novaclient import client as nova_client
from novajoin import config
from novajoin.ipa import IPAClient
from novajoin.keystone_client import get_session
from novajoin.keystone_client import register_keystoneauth_opts
from novajoin.util import get_domain
from novajoin.util import get_fqdn
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
        domain = get_domain()
        if CONF.project_subdomain:
            host = '%s.%s.%s' % (hostname, project, domain)
        else:
            host = '%s.%s' % (hostname, domain)
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
            hostname_short = payload.get('hostname')
            instance_id = payload.get('instance_id')
            payload_metadata = payload.get('metadata')

            hostname = self._generate_hostname(hostname_short)
            LOG.info("Delete host %s (%s)", instance_id, hostname)
            self.ipaclient.delete_host(hostname, {})
            self.delete_subhosts(hostname_short, payload_metadata)
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

    def delete_subhosts(self, hostname_short, metadata):
        """Delete subhosts and remove VIPs if possible.

        Servers can have multiple network interfaces, and therefore can
        have multiple aliases.  Moreover, they can part of a service using
        a virtual host (VIP).  These aliases are denoted 'subhosts',

        We read the metadata to determine which subhosts to remove.

        The subhosts corresponding to network aliases are specified in the
        metadata parameter compact_services.  These are specified in a compact
        JSON representation to avoid the 255 character nova metadata limit.
        These should all be removed when the server is removed.

        The VIPs should only be removed if the host is the last host managing
        the service.
        """
        if metadata is None:
            return

        if 'compact_services' in metadata:
            self.handle_compact_services(hostname_short,
                                         metadata.get('compact_services'))
        managed_services = [metadata[key] for key in metadata.keys()
                            if key.startswith('managed_service_')]
        if managed_services:
            self.handle_managed_services(managed_services)

    def handle_compact_services(self, host_short, service_repr_json):
        """Reconstructs and removes subhosts for compact services.

           Data looks like this:
            {"HTTP":
                ["internalapi", "ctlplane", "storagemgmt", "storage"],
             "rabbitmq":
                ["internalapi", "ctlplane"]
            }

            In this function, we will remove the subhosts.  We expect the
            services to be automatically deleted through IPA referential
            integrity.
        """
        LOG.debug("In handle compact services")
        service_repr = json.loads(service_repr_json)
        hosts_found = list()

        self.ipaclient.start_batch_operation()
        for service_name, net_list in service_repr.items():
            for network in net_list:
                host = "%s.%s" % (host_short, network)
                principal_host = get_fqdn(host)

                # remove host
                if principal_host not in hosts_found:
                    self.ipaclient.delete_subhost(principal_host)
                    hosts_found.append(principal_host)
        self.ipaclient.flush_batch_operation()

    def handle_managed_services(self, services):
        """Delete any managed services if possible.

           Checks to see if the managed service subhost has no managed hosts
           associations and if so, deletes the host.
        """
        LOG.debug("In handle_managed_services")
        hosts_deleted = list()
        services_deleted = list()

        for principal in services:
            if principal not in services_deleted:
                try:
                    if self.ipaclient.service_has_hosts(principal):
                        continue
                except KeyError:
                    continue
                self.ipaclient.delete_service(principal, batch=False)
                services_deleted.append(principal)

            principal_host = principal.split('/', 1)[1]
            if principal_host not in hosts_deleted:
                if not self.ipaclient.host_has_services(principal_host):
                    self.ipaclient.delete_subhost(principal_host, batch=False)
                    hosts_deleted.append(principal_host)


def main():
    register_keystoneauth_opts(CONF)
    CONF(sys.argv[1:], version='1.0.11',
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
