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
Unit Tests for WSGI server
"""

import mock
import testtools

from novajoin import wsgi
from oslo_config import cfg


test_service_opts = [
    cfg.StrOpt("test_service_listen",
               help="Host to bind test service to"),
    cfg.IntOpt("test_service_listen_port",
               default=0,
               help="Port number to bind test service to"), ]

CONF = cfg.CONF
CONF.register_opts(test_service_opts)


class TestWSGIService(testtools.TestCase):

    def setUp(self):
        super(TestWSGIService, self).setUp()

    @mock.patch('oslo_service.wsgi.Loader')
    def test_service_random_port(self, mock_loader):
        test_service = wsgi.WSGIService("test_service")
        self.assertEqual(0, test_service.port)
        test_service.start()
        self.assertNotEqual(0, test_service.port)
        test_service.stop()
        self.assertTrue(mock_loader.called)

    @mock.patch('oslo_service.wsgi.Loader')
    def test_reset_pool_size_to_default(self, mock_loader):
        test_service = wsgi.WSGIService("test_service")
        test_service.start()

        # Stopping the service, which in turn sets pool size to 0
        test_service.stop()
        self.assertEqual(0, test_service.server._pool.size)

        # Resetting pool size to default
        test_service.reset()
        test_service.start()
        self.assertEqual(cfg.CONF.wsgi_default_pool_size,
                         test_service.server._pool.size)
        self.assertTrue(mock_loader.called)
