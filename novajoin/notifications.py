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

import glanceclient as glance_client
from neutronclient.v2_0 import client as neutron_client
from novaclient import client as nova_client
from oslo_log import log as logging
import oslo_messaging
from oslo_serialization import jsonutils

from novajoin import config
from novajoin import exception
from novajoin.ipa import IPAClient
from novajoin import join
from novajoin.keystone_client import get_session
from novajoin.keystone_client import register_keystoneauth_opts
from novajoin.nova import get_instance
from novajoin import util


CONF = config.CONF

LOG = logging.getLogger(__name__)

BACKOFF = 2


def ipaclient():
    return IPAClient(backoff=BACKOFF)


def novaclient():
    session = get_session()
    return nova_client.Client('2.1', session=session)


def neutronclient():
    session = get_session()
    return neutron_client.Client(session=session)


def glanceclient():
    session = get_session()
    return glance_client.Client('2', session=session)


class Registry(dict):
    def __call__(self, name, version=None, service='nova'):
        def register_event(fun):
            if version:
                def check_event(sself, payload):
                    self.check_version(payload, version, service)
                    return fun(sself, payload[service + '_object.data'])
                self[name] = check_event
                return check_event
            self[name] = fun
            return fun
        return register_event

    @staticmethod
    def check_version(payload, expected_version, service):
        """Check nova notification version

        If actual's major version is different from expected, a
        NotificationVersionMismatch error is raised.
        If the minor versions are different, a DEBUG level log
        message is output
        """
        notification_version = payload[service + '_object.version']
        notification_name = payload[service + '_object.name']

        maj_ver, min_ver = map(int, notification_version.split('.'))
        expected_maj, expected_min = map(int, expected_version.split('.'))
        if maj_ver != expected_maj:
            raise exception.NotificationVersionMismatch(
                provided_maj=maj_ver, provided_min=min_ver,
                expected_maj=expected_maj, expected_min=expected_min,
                type=notification_name)

        if min_ver != expected_min:
            LOG.debug(
                "Notification %(type)s minor version mismatch, "
                "provided: %(provided_maj)s.%(provided_min)s, "
                "expected: %(expected_maj)s.%(expected_min)s.",
                {"type": notification_name,
                 "provided_maj": maj_ver, "provided_min": min_ver,
                 "expected_maj": expected_maj, "expected_min": expected_min}
            )


class NotificationEndpoint(object):

    filter_rule = oslo_messaging.notify.filter.NotificationFilter(
        publisher_id='^compute.*|^network.*',
        event_type='^compute.instance.create.end|'
                   '^compute.instance.delete.end|'
                   '^compute.instance.update|'
                   '^network.floating_ip.(dis)?associate|'
                   '^floatingip.update.end')

    event_handlers = Registry()

    def info(self, ctxt, publisher_id, event_type, payload, metadata):
        LOG.debug('notification:')
        LOG.debug(jsonutils.dumps(payload, indent=4))

        LOG.debug("publisher: %s, event: %s, metadata: %s", publisher_id,
                  event_type, metadata)

        event_handler = self.event_handlers.get(
            event_type, lambda payload: LOG.error("Status update or unknown"))
        # run event handler for received notification type
        event_handler(self, payload)

    @event_handlers('compute.instance.create.end')
    def compute_instance_create(self, payload):
        hostname = self._generate_hostname(payload.get('hostname'))
        instance_id = payload['instance_id']
        LOG.info("Add new host %s (%s)", instance_id, hostname)

    @event_handlers('compute.instance.update')
    def compute_instance_update(self, payload):
        ipa = ipaclient()
        join_controller = join.JoinController(ipa)
        hostname_short = payload['hostname']
        instance_id = payload['instance_id']
        payload_metadata = payload['metadata']
        image_metadata = payload['image_meta']

        hostname = self._generate_hostname(hostname_short)

        enroll = payload_metadata.get('ipa_enroll', '')
        image_enroll = image_metadata.get('ipa_enroll', '')
        if enroll.lower() != 'true' and image_enroll.lower() != 'true':
            LOG.info('IPA enrollment not requested, skipping update of %s',
                     hostname)
            return
        # Ensure this instance exists in nova
        instance = get_instance(instance_id)
        if instance is None:
            msg = 'No such instance-id, %s' % instance_id
            LOG.error(msg)
            return

        ipa.start_batch_operation()
        # key-per-service
        managed_services = [
            payload_metadata[key] for key in payload_metadata.keys()
            if key.startswith('managed_service_')]
        if managed_services:
            join_controller.handle_services(hostname, managed_services)

        compact_services = util.get_compact_services(payload_metadata)
        if compact_services:
            join_controller.handle_compact_services(
                hostname_short, compact_services)

        ipa.flush_batch_operation()

    @event_handlers('compute.instance.delete.end')
    def compute_instance_delete(self, payload):
        hostname_short = payload['hostname']
        instance_id = payload['instance_id']
        payload_metadata = payload['metadata']
        image_metadata = payload['image_meta']

        hostname = self._generate_hostname(hostname_short)

        enroll = payload_metadata.get('ipa_enroll', '')
        image_enroll = image_metadata.get('ipa_enroll', '')

        if enroll.lower() != 'true' and image_enroll.lower() != 'true':
            LOG.info('IPA enrollment not requested, skipping delete of %s',
                     hostname)
            return

        LOG.info("Delete host %s (%s)", instance_id, hostname)
        ipa = ipaclient()
        ipa.delete_host(hostname, {})
        self.delete_subhosts(ipa, hostname_short, payload_metadata)

    @event_handlers('network.floating_ip.associate')
    def floaitng_ip_associate(self, payload):
        floating_ip = payload['floating_ip']
        LOG.info("Associate floating IP %s" % floating_ip)
        ipa = ipaclient()
        nova = novaclient()
        server = nova.servers.get(payload['instance_id'])
        if server:
            ipa.add_ip(server.name, floating_ip)
        else:
            LOG.error("Could not resolve %s into a hostname",
                      payload['instance_id'])

    @event_handlers('network.floating_ip.disassociate')
    def floating_ip_disassociate(self, payload):
        floating_ip = payload['floating_ip']
        LOG.info("Disassociate floating IP %s" % floating_ip)
        ipa = ipaclient()
        ipa.remove_ip(floating_ip)

    @event_handlers('floatingip.update.end')
    def floating_ip_update(self, payload):
        """Neutron event"""
        floatingip = payload['floatingip']
        floating_ip = floatingip['floating_ip_address']
        port_id = floatingip['port_id']
        ipa = ipaclient()
        if port_id:
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
                        ipa.add_ip(server.name, floating_ip)
            else:
                LOG.error("Expected 1 port, got %d", len(ports))
        else:
            LOG.info("Neutron floating IP disassociate: %s" % floating_ip)
            ipa.remove_ip(floating_ip)

    def delete_subhosts(self, ipa, hostname_short, metadata):
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

        compact_services = util.get_compact_services(metadata)
        if compact_services:
            self.handle_compact_services(ipa, hostname_short,
                                         compact_services)
        managed_services = [metadata[key] for key in metadata.keys()
                            if key.startswith('managed_service_')]
        if managed_services:
            self.handle_managed_services(ipa, managed_services)

    def handle_compact_services(self, ipa, host_short, service_repr):
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
        hosts_found = list()

        ipa.start_batch_operation()
        for service_name, net_list in service_repr.items():
            for network in net_list:
                host = "%s.%s" % (host_short, network)
                principal_host = util.get_fqdn(host)

                # remove host
                if principal_host not in hosts_found:
                    ipa.delete_subhost(principal_host)
                    hosts_found.append(principal_host)
        ipa.flush_batch_operation()

    def handle_managed_services(self, ipa, services):
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
                    if ipa.service_has_hosts(principal):
                        continue
                except KeyError:
                    continue
                ipa.delete_service(principal, batch=False)
                services_deleted.append(principal)

            principal_host = principal.split('/', 1)[1]
            if principal_host not in hosts_deleted:
                if not ipa.host_has_services(principal_host):
                    ipa.delete_subhost(principal_host, batch=False)
                    hosts_deleted.append(principal_host)

    def _generate_hostname(self, hostname):
        # FIXME: Don't re-calculate the hostname, fetch it from somewhere
        project = 'foo'
        domain = util.get_domain()
        if CONF.project_subdomain:
            host = '%s.%s.%s' % (hostname, project, domain)
        else:
            host = '%s.%s' % (hostname, domain)
        return host


class VersionedNotificationEndpoint(NotificationEndpoint):

    filter_rule = oslo_messaging.notify.filter.NotificationFilter(
        publisher_id='^nova-compute.*|^network.*',
        event_type='^instance.create.end|'
                   '^instance.delete.end|'
                   '^instance.update|'
                   '^floatingip.update.end')

    event_handlers = Registry(NotificationEndpoint.event_handlers)

    @event_handlers('instance.create.end', '1.10')
    def instance_create(self, payload):
        newpayload = {
            'hostname': payload['host_name'],
            'instance_id': payload['uuid'],
        }
        self.compute_instance_create(newpayload)

    @event_handlers('instance.update', '1.8')
    def instance_update(self, payload):
        glance = glanceclient()
        newpayload = {
            'hostname': payload['host_name'],
            'instance_id': payload['uuid'],
            'metadata': payload['metadata'],
            'image_meta': glance.images.get(payload['image_uuid'])
        }
        self.compute_instance_update(newpayload)

    @event_handlers('instance.delete.end', '1.7')
    def instance_delete(self, payload):
        glance = glanceclient()
        newpayload = {
            'hostname': payload['host_name'],
            'instance_id': payload['uuid'],
            'metadata': payload['metadata'],
            'image_meta': glance.images.get(payload['image_uuid'])
        }
        self.compute_instance_delete(newpayload)


def main():
    register_keystoneauth_opts(CONF)
    CONF(sys.argv[1:], version='1.0.22',
         default_config_files=config.find_config_files())
    logging.setup(CONF, 'join')

    transport = oslo_messaging.get_notification_transport(CONF)
    targets = [oslo_messaging.Target(topic=CONF.notifications_topic)]
    if CONF.notification_format == 'unversioned':
        endpoints = [NotificationEndpoint()]
    elif CONF.notification_format == 'versioned':
        endpoints = [VersionedNotificationEndpoint()]

    server = oslo_messaging.get_notification_listener(transport,
                                                      targets,
                                                      endpoints,
                                                      executor='threading')
    LOG.info("Starting")
    server.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        LOG.info("Stopping, be patient")
        server.stop()
        server.wait()
