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

import itertools
import os

from oslo_config import cfg
from oslo_log import log

from six import moves


service_opts = [
    cfg.StrOpt('join_listen',
               default="0.0.0.0",
               help='IP address to listen on'),
    cfg.PortOpt('join_listen_port',
                default=9090,
                help='Port to listen on'),
    cfg.StrOpt('keytab', default='/etc/nova/krb5.keytab',
               help='Kerberos client keytab file'),
    cfg.StrOpt('domain', default=None,
               help='Domain for new hosts'),
    cfg.IntOpt('connect_retries', default=2,
               help='How many times to attempt to retry '
               'the connection to IPA before giving up'),
    cfg.BoolOpt('project_subdomain', default=False,
                help='Treat the project as a DNS subdomain '
                'so a hostname would take the form: '
                'instance.project.domain'),
    cfg.BoolOpt('normalize_project', default=True,
                help='Normalize the project name to be a valid DNS label'),
    cfg.ListOpt('glance_api_servers',
                default=None,
                help='A list of the URLs of glance API servers available to '
                     'cinder ([http[s]://][hostname|ip]:port). If protocol '
                     'is not specified it defaults to http.'),
    cfg.IntOpt('glance_num_retries',
               default=0,
               help='Number retries when downloading an image from glance'),
    cfg.StrOpt('auth_strategy', default='keystone',
               help='Strategy to use for authentication.'),
]


def _fixpath(p):
    """Apply tilde expansion and absolutization to a path."""
    return os.path.abspath(os.path.expanduser(p))


def _search_dirs(dirs, basename, extension=""):
    """Search a list of directories for a given filename.

    Iterator over the supplied directories, returning the first file
    found with the supplied name and extension.

    :param dirs: a list of directories
    :param basename: the filename, for example 'glance-api'
    :param extension: the file extension, for example '.conf'
    :returns: the path to a matching file, or None
    """
    for d in dirs:
        path = os.path.join(d, '%s%s' % (basename, extension))
        if os.path.exists(path):
            return path


def find_config_files():
    """Return a list of default configuration files.

    This is loosely based on the oslo.config version but makes it more
    specific to novajoin.

    We look for those config files in the following directories:

      ~/.join/join.conf
      ~/join.conf
      /etc/nova/join.conf
      /etc/join.conf
      /etc/join/join.conf
    """
    cfg_dirs = [
        _fixpath('~/.join/'),
        _fixpath('~'),
        '/etc/nova/',
        '/etc'
        '/etc/join/'
    ]

    config_files = []
    extension = '.conf'
    config_files.append(_search_dirs(cfg_dirs, 'join', extension))

    return list(moves.filter(bool, config_files))


CONF = cfg.CONF
CONF.register_opts(service_opts)
log.register_options(CONF)


def list_opts():
    return [
        ('DEFAULT',
            itertools.chain(
                service_opts,
            )),
    ]
