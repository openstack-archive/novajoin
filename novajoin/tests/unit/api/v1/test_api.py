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

from oslo_serialization import jsonutils
from testtools.matchers import MatchesRegex

from novajoin.base import Fault
from novajoin import join
from novajoin import test
from novajoin.tests.unit.api import fakes


class JoinTest(test.TestCase):

    def setUp(self):
        self.join_controller = join.JoinController()
        super(JoinTest, self).setUp()

    def test_no_body(self):
        body = None
        req = fakes.HTTPRequest.blank('/v1/')
        req.method = 'POST'
        req.content_type = "application/json"

        # Not using assertRaises because the exception is wrapped as
        # a Fault
        try:
            self.join_controller.create(req, body)
        except Fault as fault:
            assert fault.status_int == 400

    def test_no_instanceid(self):
        body = {"metadata": {"ipa_enroll": "True"},
                "image-id": "b8c88e01-c820-40f6-b026-00926706e374",
                "hostname": "test"}
        req = fakes.HTTPRequest.blank('/v1/')
        req.method = 'POST'
        req.content_type = "application/json"

        # Not using assertRaises because the exception is wrapped as
        # a Fault
        try:
            self.join_controller.create(req, body)
        except Fault as fault:
            assert fault.status_int == 400

    def test_no_imageid(self):
        body = {"metadata": {"ipa_enroll": "True"},
                "instance-id": "e4274dc8-325a-409b-92fd-cfdfdd65ae8b",
                "hostname": "test"}
        req = fakes.HTTPRequest.blank('/v1/')
        req.method = 'POST'
        req.content_type = "application/json"

        # Not using assertRaises because the exception is wrapped as
        # a Fault
        try:
            self.join_controller.create(req, body)
        except Fault as fault:
            assert fault.status_int == 400

    def test_no_hostname(self):
        body = {"metadata": {"ipa_enroll": "True"},
                "instance-id": "e4274dc8-325a-409b-92fd-cfdfdd65ae8b",
                "image-id": "b8c88e01-c820-40f6-b026-00926706e374"}
        req = fakes.HTTPRequest.blank('/v1/')
        req.method = 'POST'
        req.content_type = "application/json"

        # Not using assertRaises because the exception is wrapped as
        # a Fault
        try:
            self.join_controller.create(req, body)
        except Fault as fault:
            assert fault.status_int == 400

    def test_request_no_enrollment(self):
        body = {"metadata": {"ipa_enroll": "False"},
                "instance-id": "e4274dc8-325a-409b-92fd-cfdfdd65ae8b",
                "image-id": "b8c88e01-c820-40f6-b026-00926706e374",
                "hostname": "test"}
        expected = {}
        req = fakes.HTTPRequest.blank('/v1')
        req.method = 'POST'
        req.content_type = "application/json"
        req.body = jsonutils.dump_as_bytes(body)
        res_dict = self.join_controller.create(req, body)
        self.assertEqual(expected, res_dict)

    def test_request(self):
        body = {"metadata": {"ipa_enroll": "True"},
                "instance-id": "e4274dc8-325a-409b-92fd-cfdfdd65ae8b",
                "image-id": "b8c88e01-c820-40f6-b026-00926706e374",
                "hostname": "test"}
        req = fakes.HTTPRequest.blank('/v1')
        req.method = 'POST'
        req.content_type = "application/json"
        req.body = jsonutils.dump_as_bytes(body)
        res_dict = self.join_controller.create(req, body)

        # Manually check the response dict for an OTP pattern and
        # what the default hostname should be.
        self.assertThat(res_dict.get('ipaotp'),
                        MatchesRegex('^[a-z0-9]{32}'))
        self.assertEqual(len(res_dict.get('ipaotp', 0)), 32)
        self.assertEqual(res_dict.get('hostname'), 'test.test')

        # Note that on failures this will generate to stdout a Krb5Error
        # because in all likelihood the keytab cannot be read (and
        # probably doesn't exist. This can be ignored.
