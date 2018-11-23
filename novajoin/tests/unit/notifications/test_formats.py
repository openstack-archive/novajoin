# Copyright 2018 Red Hat, Inc.
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

import mock
import os

from oslo_messaging.notify import dispatcher as notify_dispatcher
from oslo_messaging.notify import NotificationResult
from oslo_serialization import jsonutils

from novajoin import notifications
from novajoin import test


SAMPLES_DIR = os.path.dirname(os.path.realpath(__file__))


class NotificationFormatsTest(test.TestCase):

    def _get_event(self, filename):
        json_sample = os.path.join(SAMPLES_DIR, filename)
        with open(json_sample) as sample_file:
            return jsonutils.loads(sample_file.read())

    def _run_dispatcher(self, event):
        dispatcher = notify_dispatcher.NotificationDispatcher(
            [notifications.VersionedNotificationEndpoint()], None)
        return dispatcher.dispatch(mock.Mock(ctxt={}, message=event))

    @mock.patch('novajoin.notifications.NotificationEndpoint'
                '._generate_hostname')
    def test_instance_create(self, generate_hostname):
        event = self._get_event('instance.create.end.json')
        result = self._run_dispatcher(event)
        self.assertEqual(result, NotificationResult.HANDLED)

    @mock.patch('novajoin.notifications.NotificationEndpoint'
                '._generate_hostname')
    def test_instance_create_wrong_version(self, generate_hostname):
        event = self._get_event('instance.create.end.json')
        event['payload']['nova_object.version'] = '999.999'
        result = self._run_dispatcher(event)
        self.assertEqual(result, NotificationResult.REQUEUE)

    @mock.patch('novajoin.notifications.glanceclient')
    @mock.patch('novajoin.notifications.ipaclient')
    @mock.patch('novajoin.notifications.NotificationEndpoint'
                '._generate_hostname')
    def test_instance_update(self, glanceclient, ipaclient, gen_hostname):
        event = self._get_event('instance.update.json')
        result = self._run_dispatcher(event)
        self.assertEqual(result, NotificationResult.HANDLED)

    @mock.patch('novajoin.notifications.glanceclient')
    @mock.patch('novajoin.notifications.ipaclient')
    @mock.patch('novajoin.notifications.NotificationEndpoint'
                '._generate_hostname')
    def test_instance_delete(self, glanceclient, ipaclient, gen_hostname):
        event = self._get_event('instance.delete.end.json')
        result = self._run_dispatcher(event)
        self.assertEqual(result, NotificationResult.HANDLED)

    @mock.patch('novajoin.notifications.neutronclient')
    @mock.patch('novajoin.notifications.novaclient')
    @mock.patch('novajoin.notifications.ipaclient')
    @mock.patch('novajoin.notifications.NotificationEndpoint'
                '._generate_hostname')
    def test_floatingip_associate(self, neutronclient, novaclient,
                                  ipaclient, generate_hostname):
        event = self._get_event('floatingip.update.end_associate.json')
        result = self._run_dispatcher(event)
        self.assertEqual(result, NotificationResult.HANDLED)

    @mock.patch('novajoin.notifications.neutronclient')
    @mock.patch('novajoin.notifications.novaclient')
    @mock.patch('novajoin.notifications.ipaclient')
    @mock.patch('novajoin.notifications.NotificationEndpoint'
                '._generate_hostname')
    def test_floatingip_disassociate(self, neutronclient, novaclient,
                                     ipaclient, generate_hostname):
        event = self._get_event('floatingip.update.end_disassociate.json')
        result = self._run_dispatcher(event)
        self.assertEqual(result, NotificationResult.HANDLED)
