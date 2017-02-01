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

"""
Integration Tests for IPA connection code.

This requires a full deployment and a copy of the novajoin keytab that can be
read by the user, currently hardcoced to use /tmp/test.keytab.

To enable quite verbose logging enable debug = True in /etc/ipa/default.conf
and comment-out/remove the console.setLevel(logging.WARN).
"""

import logging
import os
import testtools
import time
import uuid

from ipapython.ipa_log_manager import log_mgr

from ipalib import api

from novajoin import config
from novajoin.ipa import IPAClient


CONF = config.CONF

hostname = None


class TestIPAService(testtools.TestCase):
    """Do a live test against an IPA master.

    This requires:
        - the machine to be enrolled in IPA
        - a keytab to use

    This will add and remove entries from the IPA master so beware.
    """

    def setUp(self):
        global hostname
        CONF.keytab = '/tmp/test.keytab'
        super(TestIPAService, self).setUp()
        self.ipaclient = IPAClient()
        # suppress the Forwarding messages from ipa
        console = log_mgr.get_handler('console')
        console.setLevel(logging.WARN)
        if hostname is None:
            hostname = unicode(str(uuid.uuid4()) + '.' + api.env.domain)
        os.environ['KRB5_CONFIG'] = 'krb5.conf'

    def test_host_add(self):
        global hostname
        ipaotp = str(uuid.uuid4())
        metadata = {}
        image_metadata = {}
        self.ipaclient.add_host(hostname, ipaotp, metadata, image_metadata)

    def test_host_add_again(self):
        global hostname
        ipaotp = str(uuid.uuid4())
        metadata = {}
        image_metadata = {}
        self.ipaclient.add_host(hostname, ipaotp, metadata, image_metadata)

    def test_host_subhost(self):
        global hostname
        subhost = unicode(str(uuid.uuid4()) + '.' + api.env.domain)
        self.ipaclient.add_subhost(subhost)
        self.ipaclient.flush_batch_operation()

        self.ipaclient.start_batch_operation()
        self.ipaclient.delete_subhost(subhost)
        self.ipaclient.flush_batch_operation()

    def test_host_del(self):
        global hostname
        self.ipaclient.delete_host(hostname)

    def test_host_expired_ticket(self):
        global hostname
        # The local krb5.conf is setup to issue tickets for 1 minute
        time.sleep(60)

        self.ipaclient.delete_host(hostname)

    def test_host_service(self):
        global hostname
        ipaotp = str(uuid.uuid4())
        metadata = {}
        image_metadata = {}
        subhost = unicode(str(uuid.uuid4()) + '.' + api.env.domain)
        service_principal = u'test/%s' % subhost
        self.ipaclient.add_host(hostname, ipaotp, metadata, image_metadata)
        self.ipaclient.add_host(subhost, ipaotp, metadata, image_metadata)
        self.ipaclient.add_service(service_principal)
        self.ipaclient.service_add_host(service_principal, hostname)
        self.ipaclient.delete_subhost(subhost)
        self.ipaclient.delete_host(hostname)
        self.ipaclient.flush_batch_operation()
