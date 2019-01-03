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

import mock

from oslo_serialization import jsonutils
from testtools.matchers import MatchesRegex

from novajoin.base import Fault
from novajoin import join
from novajoin import test
from novajoin.tests.unit.api import fakes
from novajoin.tests.unit import fake_constants as fake


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
        else:
            assert(False)

    def test_no_instanceid(self):
        body = {"metadata": {"ipa_enroll": "True"},
                "image-id": fake.IMAGE_ID,
                "project-id": fake.PROJECT_ID,
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
        else:
            assert(False)

    def test_no_hostname(self):
        body = {"metadata": {"ipa_enroll": "True"},
                "instance-id": fake.INSTANCE_ID,
                "project-id": fake.PROJECT_ID,
                "image-id": fake.IMAGE_ID}
        req = fakes.HTTPRequest.blank('/v1/')
        req.method = 'POST'
        req.content_type = "application/json"

        # Not using assertRaises because the exception is wrapped as
        # a Fault
        try:
            self.join_controller.create(req, body)
        except Fault as fault:
            assert fault.status_int == 400
        else:
            assert(False)

    def test_no_project_id(self):
        body = {"metadata": {"ipa_enroll": "True"},
                "instance-id": fake.INSTANCE_ID,
                "image-id": fake.IMAGE_ID,
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
        else:
            assert(False)

    def test_request_no_enrollment(self):
        body = {"metadata": {"ipa_enroll": "False"},
                "instance-id": fake.INSTANCE_ID,
                "project-id": fake.PROJECT_ID,
                "image-id": fake.IMAGE_ID,
                "hostname": "test"}
        expected = {}
        req = fakes.HTTPRequest.blank('/v1')
        req.method = 'POST'
        req.content_type = "application/json"
        req.body = jsonutils.dump_as_bytes(body)
        res_dict = self.join_controller.create(req, body)
        self.assertEqual(expected, res_dict)

    @mock.patch('novajoin.ipa.SafeConfigParser')
    @mock.patch('novajoin.join.get_instance')
    @mock.patch('novajoin.util.get_domain')
    def test_valid_request(self, mock_get_domain,
                           mock_get_instance, mock_conf_parser):
        mock_get_instance.return_value = fake.fake_instance
        mock_get_domain.return_value = "test"

        mock_conf_parser_instance = mock.MagicMock()
        mock_conf_parser_instance.get = mock.Mock(
            side_effect=["novajoin", "REALM"])
        mock_conf_parser.return_value = mock_conf_parser_instance

        body = {"metadata": {"ipa_enroll": "True"},
                "instance-id": fake.INSTANCE_ID,
                "project-id": fake.PROJECT_ID,
                "image-id": fake.IMAGE_ID,
                "hostname": "test"}
        req = fakes.HTTPRequest.blank('/v1')
        req.method = 'POST'
        req.content_type = "application/json"
        req.body = jsonutils.dump_as_bytes(body)
        res_dict = self.join_controller.create(req, body)

        # There should be no OTP because IPA shouldn't be
        # configured, but we'll handle both cases.
        if res_dict.get('ipaotp'):
            self.assertThat(res_dict.get('ipaotp'),
                            MatchesRegex('^[a-z0-9]{32}'))
            self.assertEqual(len(res_dict.get('ipaotp', 0)), 32)
        self.assertEqual(res_dict.get('hostname'), 'test.test')
        self.assertEqual(res_dict.get('krb_realm'), 'REALM')

        # Note that on failures this will generate to stdout a Krb5Error
        # because in all likelihood the keytab cannot be read (and
        # probably doesn't exist. This can be ignored.

    @mock.patch('novajoin.ipa.SafeConfigParser')
    @mock.patch('novajoin.keystone_client.get_project_name')
    @mock.patch('novajoin.join.get_instance')
    @mock.patch('novajoin.util.get_domain')
    def test_valid_hostclass_request(self, mock_get_domain,
                                     mock_get_instance,
                                     mock_get_project_name,
                                     mock_conf_parser):
        mock_get_instance.return_value = fake.fake_instance
        mock_get_domain.return_value = "test"
        mock_get_project_name.return_value = "test"

        mock_conf_parser_instance = mock.MagicMock()
        mock_conf_parser_instance.get = mock.Mock(
            side_effect=["novajoin", "REALM"])
        mock_conf_parser.return_value = mock_conf_parser_instance

        body = {"metadata": {"ipa_enroll": "True"},
                "instance-id": fake.INSTANCE_ID,
                "project-id": fake.PROJECT_ID,
                "image-id": fake.IMAGE_ID,
                "hostname": "test"}
        req = fakes.HTTPRequest.blank('/v1')
        req.method = 'POST'
        req.content_type = "application/json"
        req.body = jsonutils.dump_as_bytes(body)
        res_dict = self.join_controller.create(req, body)

        # There should be no OTP because IPA shouldn't be
        # configured, but we'll handle both cases.
        if res_dict.get('ipaotp'):
            self.assertThat(res_dict.get('ipaotp'),
                            MatchesRegex('^[a-z0-9]{32}'))
            self.assertEqual(len(res_dict.get('ipaotp', 0)), 32)
        self.assertEqual(res_dict.get('hostname'), 'test.test')
        self.assertEqual(res_dict.get('krb_realm'), 'REALM')

        # Note that on failures this will generate to stdout a Krb5Error
        # because in all likelihood the keytab cannot be read (and
        # probably doesn't exist. This can be ignored.

    @mock.patch('novajoin.join.get_instance')
    def test_invalid_instance_id(self, mock_get_instance):
        """Mock the instance to not exist so there is nothing to enroll."""
        mock_get_instance.return_value = None

        body = {"metadata": {"ipa_enroll": "True"},
                "instance-id": "invalid",
                "project-id": fake.PROJECT_ID,
                "image-id": fake.IMAGE_ID,
                "hostname": "test"}
        req = fakes.HTTPRequest.blank('/v1')
        req.method = 'POST'
        req.content_type = "application/json"
        req.body = jsonutils.dump_as_bytes(body)

        # Not using assertRaises because the exception is wrapped as
        # a Fault
        try:
            self.join_controller.create(req, body)
        except Fault as fault:
            assert fault.status_int == 400
        else:
            assert(False)

    @mock.patch('novajoin.join.get_instance')
    @mock.patch('novajoin.keystone_client.get_project_name')
    @mock.patch('novajoin.util.get_domain')
    def test_invalid_project_id(self, mock_get_domain, mock_get_project_name,
                                mock_get_instance):
        mock_get_instance.return_value = None
        mock_get_project_name.return_value = None
        mock_get_domain.return_value = "test"

        body = {"metadata": {"ipa_enroll": "True", "ipa_hostclass": "foo"},
                "instance-id": fake.INSTANCE_ID,
                "project-id": "invalid",
                "image-id": fake.IMAGE_ID,
                "hostname": "test"}
        req = fakes.HTTPRequest.blank('/v1')
        req.method = 'POST'
        req.content_type = "application/json"
        req.body = jsonutils.dump_as_bytes(body)

        # Not using assertRaises because the exception is wrapped as
        # a Fault
        try:
            self.join_controller.create(req, body)
        except Fault as fault:
            assert fault.status_int == 400
        else:
            assert(False)
