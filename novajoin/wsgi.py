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

import sys

from novajoin import config
from novajoin import exception
from novajoin import keystone_client
from oslo_concurrency import processutils
from oslo_log import log
from oslo_service import service
from oslo_service import wsgi


CONF = config.CONF

LOG = log.getLogger(__name__)


class WSGIService(service.ServiceBase):
    """Provides ability to launch API from a 'paste' configuration."""

    def __init__(self, name, loader=None):
        """Initialize, but do not start the WSGI server.

        :param name: The name of the WSGI server given to the loader.
        :param loader: Loads the WSGI application using the given name.
        :returns: None

        """
        self.name = name
        self.loader = loader or wsgi.Loader(CONF)
        self.app = self.loader.load_app(name)
        self.host = getattr(CONF, '%s_listen' % name, "0.0.0.0")
        self.port = getattr(CONF, '%s_listen_port' % name, 0)
        self.workers = (getattr(CONF, '%s_workers' % name, None) or
                        processutils.get_worker_count())
        if self.workers and self.workers < 1:
            worker_name = '%s_workers' % name
            msg = ("%(worker_name)s value of %(workers)d is invalid, "
                   "must be greater than 0." %
                   {'worker_name': worker_name,
                    'workers': self.workers})
            raise exception.InvalidInput(msg)

        self.server = wsgi.Server(CONF,
                                  name,
                                  self.app,
                                  host=self.host,
                                  port=self.port)

    def start(self):
        """Start serving this service using loaded configuration.

        Also, retrieve updated port number in case '0' was passed in, which
        indicates a random port should be used.

        :returns: None

        """
        self.server.start()
        self.port = self.server.port
        LOG.info("Starting on port %d" % self.port)

    def stop(self):
        """Stop serving this API.

        :returns: None

        """
        self.server.stop()

    def wait(self):
        """Wait for the service to stop serving this API.

        :returns: None

        """
        self.server.wait()

    def reset(self):
        """Reset server greenpool size to default.

        :returns: None

        """
        self.server.reset()


def process_launcher():
    return service.ProcessLauncher(CONF)


def main():

    keystone_client.register_keystoneauth_opts(CONF)
    CONF(sys.argv[1:], version='1.0.11',
         default_config_files=config.find_config_files())
    log.setup(CONF, 'join')
    launcher = process_launcher()
    server = WSGIService('join')
    launcher.launch_service(server, workers=server.workers)
    launcher.wait()
