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

import json
import StringIO
import testtools
import time
import uuid

import paramiko

import openstack


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
        self._conn = openstack.connect(
            auth_url='http://127.0.0.1/identity', project_name='demo',
            username='demo', password='secretadmin', region_name='RegionOne',
            user_domain_id='default', project_domain_id='default',
            app_name='functional-tests', app_version='1.0')
        self._key = self._conn.compute.create_keypair(name=TEST_KEY)
        group = self._conn.network.find_security_group('default')
        self._rules = []
        for protocol, port in [('icmp', None), ('tcp', 22), ('tcp', 443)]:
            try:
                self._rules.append(
                    self._conn.network.create_security_group_rule(
                        security_group_id=group.id, direction='ingress',
                        remote_ip_prefix='0.0.0.0/0', protocol=protocol,
                        port_range_max=port, port_range_min=port,
                        ethertype='IPv4'))
            except openstack.exceptions.ConflictException:
                pass
        network = self._conn.network.find_network('public')
        self._ip = self._conn.network.create_ip(floating_network_id=network.id)
        self._server = None

    def tearDown(self):
        super(TestEnrollment, self).setUp()
        self._key.delete()
        for rule in self._rules:
            rule.delete()
        self._conn.network.delete_ip(self._ip)
        self._delete_server()

    def _create_server(self):
        image = self._conn.compute.find_image(TEST_IMAGE)
        flavor = self._conn.compute.find_flavor('m1.small')
        network = self._conn.network.find_network('private')

        self._server = self._conn.compute.create_server(
            name=TEST_INSTANCE, image_id=image.id, flavor_id=flavor.id,
            networks=[{"uuid": network.id}], key_name=self._key.name,
            metadata = {"ipa_enroll": "True"})

        server = self._conn.compute.wait_for_server(self._server)
        self._conn.compute.add_floating_ip_to_server(server, self._ip.id)
        return server

    def _delete_server(self):
        if self._server:
            self._conn.compute.delete_server(self._server)
        self._server = None

    def _ssh_connect(self):
        # NOTE(xek): We are connectiong to the floating IP address.
        # Alternatively we could connect to self._server.access_ipv4, but then
        # we wouldn't be able to connect to keystone from the same namespace.

        pkey = paramiko.RSAKey.from_private_key(
            StringIO.StringIO(self._key.private_key))
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        tries = 48
        connected = False
        while tries:
            try:
                client.connect(self._ip.floating_ip_address,
                               username=TEST_IMAGE_USER, pkey=pkey)
                connected = True
                break
            except paramiko.ssh_exception.NoValidConnectionsError:
                time.sleep(5)
                tries -= 1
        self.assertTrue(connected)
        return client

    def _check_ipa_client_install(self):
        ssh = self._ssh_connect()
        tries = 24
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
            stdout.read())

    def test_enroll_server(self):
        self._create_server()
        self._check_ipa_client_install()
        self._delete_server()
        # TODO(xek): check that it was deleted from freeipa
