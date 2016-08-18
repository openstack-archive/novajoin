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
import json
import oslo_messaging
from oslo_serialization import jsonutils
from oslo_log import log as logging

import config
import cache
from ipa import IPAClient


CONF = config.CONF

LOG = logging.getLogger(__name__)


class NotificationEndpoint(object):

    filter_rule = oslo_messaging.notify.filter.NotificationFilter(
        publisher_id='^compute.*|^network.*',
        event_type='^compute.instance.create.end|'
                   '^compute.instance.delete.end|'
                   '^network.floating_ip.(dis)?associate',)

    def __init__(self):
        self.uuidcache = cache.Cache()
        self.ipaclient = IPAClient()

    def info(self, ctxt, publisher_id, event_type, payload, metadata):
        LOG.debug('notification:')
        LOG.debug(json.dumps(payload, indent=4))

        LOG.debug("publisher: %s, event: %s, metadata: %s", publisher_id,
                  event_type, metadata)

        if event_type == 'compute.instance.create.end':
            LOG.info("Add new host")
        elif event_type == 'compute.instance.delete.end':
            LOG.info("Delete host")
            hostname = payload.get('hostname')
            # FIXME: Don't re-calculate the hostname, fetch it from somewhere
            project = 'foo'
            if CONF.project_subdomain:
                hostname = '%s.%s.%s' % (hostname, project, CONF.domain)
            else:
                hostname = '%s.%s' % (hostname, CONF.domain)

            self.ipaclient.delete_host(hostname, {})
        elif event_type == 'network.floating_ip.associate':
            floating_ip = payload.get('floating_ip')
            LOG.info("Associate floating IP %s" % floating_ip)
            entry = self.uuidcache.get(payload.get('instance_id'))
            if entry:
                data = jsonutils.loads(entry)
                self.ipaclient.add_ip(data.get('hostname'), floating_ip)
            else:
                LOG.error("Could not resolve %s into a hostname",
                          payload.get('instance_id'))
        elif event_type == 'network.floating_ip.disassociate':
            floating_ip = payload.get('floating_ip')
            LOG.info("Disassociate floating IP %s" % floating_ip)
            entry = self.uuidcache.get(payload.get('instance_id'))
            if entry:
                data = jsonutils.loads(entry)
                self.ipaclient.remove_ip(data.get('hostname'), floating_ip)
            else:
                LOG.error("Could not resolve %s into a hostname",
                          payload.get('instance_id'))
        else:
            LOG.error("Status update or unknown")


def main():

    CONF(sys.argv[1:], project='join', version='1.0.0')
    logging.setup(CONF, 'join')

    transport = oslo_messaging.get_transport(CONF)
    targets = [oslo_messaging.Target(topic='notifications')]
    endpoints = [NotificationEndpoint()]
    pool = 'listener-novajoin'

    server = oslo_messaging.get_notification_listener(transport,
                                                      targets,
                                                      endpoints,
                                                      executor='threading',
                                                      allow_requeue=True,
                                                      pool=pool)
    LOG.info("Starting")
    server.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        LOG.info("Stopping, be patient")
        server.stop()
        server.wait()
