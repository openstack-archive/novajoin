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

"""
Tests enrollment of new OpenStack VMs in FreeIPA.

The test uses the default demo project and credentials and assumes there is a
centos-image present in Glance.
"""

import io
import json
import os
import testtools
import time
import uuid

import openstack
from oslo_service import loopingcall
import paramiko

from novajoin import config
from novajoin.ipa import IPAClient


CONF = config.CONF

EXAMPLE_DOMAIN = '.example.test'
TEST_IMAGE = 'centos-image'
TEST_IMAGE_USER = 'centos'
TEST_INSTANCE = str(uuid.uuid4())
TEST_KEY = str(uuid.uuid4())


class TestEnrollment(testtools.TestCase):
    """Do a live test against a Devstack installation.

    This requires:
        - Devstack running on localhost
        - novajoin configured and running
        - centos-image present in Glance

    This will add and remove server instances.
    """

    def setUp(self):
        super(TestEnrollment, self).setUp()
        CONF.keytab = '/tmp/test.keytab'
        if not os.path.isfile(CONF.keytab):
            CONF.keytab = '/etc/novajoin/krb5.keytab'
        self.ipaclient = IPAClient()
        self.conn = openstack.connect(
            auth_url='http://127.0.0.1/identity', project_name='demo',
            username='demo', password='secretadmin', region_name='RegionOne',
            user_domain_id='default', project_domain_id='default',
            app_name='functional-tests', app_version='1.0')
        self._key = self.conn.compute.create_keypair(name=TEST_KEY)
        group = self.conn.network.find_security_group('default')
        self._rules = []
        for protocol in ['icmp', 'tcp', 'udp']:
            try:
                self._rules.append(
                    self.conn.network.create_security_group_rule(
                        security_group_id=group.id, direction='ingress',
                        remote_ip_prefix='0.0.0.0/0', protocol=protocol,
                        port_range_min=(protocol == 'icmp' and 0 or 1),
                        port_range_max=(protocol == 'icmp' and 255 or 65535),
                        ethertype='IPv4'))
            except openstack.exceptions.ConflictException:
                pass
        network = self.conn.network.find_network('public')
        self._ip = self.conn.network.create_ip(floating_network_id=network.id)
        self._server = None

    def tearDown(self):
        super(TestEnrollment, self).tearDown()
        self.conn.compute.delete_keypair(self._key)
        for rule in self._rules:
            self.conn.network.delete_security_group_rule(rule)
        self._delete_server()
        self.conn.network.delete_ip(self._ip)

    def _create_server(self):
        image = self.conn.compute.find_image(TEST_IMAGE)
        flavor = self.conn.compute.find_flavor('m1.small')
        network = self.conn.network.find_network('private')

        self._server = self.conn.compute.create_server(
            name=TEST_INSTANCE, image_id=image.id, flavor_id=flavor.id,
            networks=[{"uuid": network.id}], key_name=self._key.name,
            metadata = {
                "ipa_enroll": "True",
                'compact_service_http': json.dumps(['test1', 'test2']),
            })

        server = self.conn.compute.wait_for_server(self._server)
        return server

    def _update_server_compact_service_new(self):
        self.conn.compute.set_server_metadata(
            self._server,
            compact_service_rabbitmq=json.dumps(['test3', 'test4']))

    def _update_server_compact_service_old(self):
        self.conn.compute.delete_server_metadata(self._server, [
            'compact_service_http', 'compact_service_rabbitmq'])
        self.conn.compute.set_server_metadata(
            self._server,
            compact_services=json.dumps({'http': ['test5', 'test6']}))

    @loopingcall.RetryDecorator(50, 5, 5, (AssertionError,))
    def _check_server_compact_services(self, service_list):
        services = ['\\'.join([s.split('/', 1)[0].lower(), s.split('.', 2)[1]])
                    for s in self.ipaclient.host_get_services(
                        TEST_INSTANCE + EXAMPLE_DOMAIN)]
        self.assertSetEqual(set(services), set(service_list))

    def _associate_floating_ip(self):
        self.conn.compute.add_floating_ip_to_server(
            self._server, self._ip.floating_ip_address)

    def _disassociate_floating_ip(self):
        self.conn.compute.remove_floating_ip_from_server(
            self._server, self._ip.floating_ip_address)

    def _delete_server(self):
        if self._server:
            self.conn.compute.delete_server(self._server)
        self._server = None

    @loopingcall.RetryDecorator(50, 5, 5, (
        paramiko.ssh_exception.NoValidConnectionsError,))
    def _ssh_connect(self):
        # NOTE(xek): We are connectiong to the floating IP address.
        # Alternatively we could connect to self._server.access_ipv4, but then
        # we wouldn't be able to connect to keystone from the same namespace.

        pkey = paramiko.RSAKey.from_private_key(
            io.StringIO(self._key.private_key))
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(self._ip.floating_ip_address,
                       username=TEST_IMAGE_USER, pkey=pkey)
        return client

    def _check_ipa_client_install(self):
        ssh = self._ssh_connect()
        tries = 100
        while tries:
            stdin, stdout, stderr = ssh.exec_command(
                'cat /run/cloud-init/status.json')
            data = json.load(stdout)
            if data.get("v1", {}).get("datasource"):
                time.sleep(5)
                tries -= 1
            else:  # cloud-init script finished
                break
        stdin, stdout, stderr = ssh.exec_command('id admin')
        self.assertRegex(
            'uid=\d+\(admin\) gid=\d+\(admins\) groups=\d+\(admins\)',
            stdout.read().decode())

    @loopingcall.RetryDecorator(200, 5, 5, (AssertionError,))
    def _check_ipa_client_created(self):
        self.assertTrue(
            self.ipaclient.find_host(TEST_INSTANCE + EXAMPLE_DOMAIN))

    @loopingcall.RetryDecorator(50, 5, 5, (AssertionError,))
    def _check_ipa_client_deleted(self):
        self.assertFalse(
            self.ipaclient.find_host(TEST_INSTANCE + EXAMPLE_DOMAIN))

    @loopingcall.RetryDecorator(50, 5, 5, (AssertionError,))
    def _check_ip_record_added(self):
        self.assertTrue(
            self.ipaclient.find_record(self._ip.floating_ip_address))

    @loopingcall.RetryDecorator(50, 5, 5, (AssertionError,))
    def _check_ip_record_removed(self):
        self.assertFalse(
            self.ipaclient.find_record(self._ip.floating_ip_address))

    def test_enroll_server(self):
        self._create_server()
        self._associate_floating_ip()
        self._check_ipa_client_created()
        self._check_ip_record_added()
        self._disassociate_floating_ip()
        self._check_ip_record_removed()
        self._associate_floating_ip()
        self._check_ip_record_added()

        self._check_ipa_client_install()

        self._check_server_compact_services(['http\\test1', 'http\\test2'])

        self._update_server_compact_service_new()
        self._check_server_compact_services([
            'http\\test1', 'http\\test2',
            'rabbitmq\\test3', 'rabbitmq\\test4'])

        self._update_server_compact_service_old()
        # NOTE(xek), novajoin doesn't support removing of services via update
        self._check_server_compact_services([
            'http\\test1', 'http\\test2', 'http\\test5', 'http\\test6',
            'rabbitmq\\test3', 'rabbitmq\\test4'])

        self._delete_server()
        self._check_ipa_client_deleted()
        self._check_ip_record_removed()
