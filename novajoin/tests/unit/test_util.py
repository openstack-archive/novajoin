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
Unit Tests for util functions
"""

import json
import testtools

from novajoin import util


class TestUtil(testtools.TestCase):

    def setUp(self):
        super(TestUtil, self).setUp()

    def test_get_compact_services(self):
        result = {"http": ["internalapi", "ctlplane", "storage"],
                  "rabbitmq": ["internalapi", "ctlplane"]}
        old_metadata = {"compact_services": json.dumps(result)}
        new_metadata = {
            "compact_service_http": json.dumps(result['http']),
            "compact_service_rabbitmq": json.dumps(result['rabbitmq'])}

        self.assertDictEqual(util.get_compact_services(old_metadata), result)

        self.assertDictEqual(util.get_compact_services(new_metadata), result)

    def test_get_compact_services_empty(self):
        self.assertIsNone(util.get_compact_services({}))
