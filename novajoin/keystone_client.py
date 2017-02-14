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

import logging

from keystoneauth1 import exceptions as keystone_exception
from keystoneauth1.identity import v3
from keystoneauth1 import session as ksc_session
from keystoneclient.v3 import client as ks_client_v3
from oslo_config import cfg

CONF = cfg.CONF
LOG = logging.getLogger(__name__)


class Session(object):
    """A Keysone auth session.

       This is session is expected to be generated early in
       processing and re-used during the lifetime of a request
       to novajoin.

       It uses the credentials passed in.
    """

    def __init__(self, token, project_name,
                 project_domain_name='default'):
        try:
            self.auth_url = CONF['keystone_authtoken'].auth_url
        except cfg.NoSuchOptError:
            LOG.error("auth_url is not defined in [keystone_authtoken]")
            self.auth_url = None
        self.token = token
        self.project_name = project_name
        self.project_domain_name = project_domain_name

    def get_session(self):
        auth = v3.Token(auth_url=self.auth_url,
                        token=self.token,
                        project_domain_name=self.project_domain_name,
                        project_name=self.project_name)

        return ksc_session.Session(auth=auth)


def get_client(session):
    """Return a client for keystone v3 endpoint."""
    return ks_client_v3.Client(session=session)


def get_service_catalog(client):
    return client.session.auth.get_access(client.session).service_catalog


def get_auth_token(client):
    return client.session.auth.get_access(client.session).auth_token


def get_project_name(project_id):
    """Given a keystone project-id return the name of the project."""
    ks = get_client()

    try:
        data = ks.get('projects/%s' % project_id)
    except keystone_exception.NotFound:
        return None
    else:
        project_data = data[1].get('project', {})
        return project_data.get('name')


def get_user_name(user_id):
    """Given a keystone user-id return the name of the user."""
    ks = get_client()

    try:
        data = ks.get('users/%s' % user_id)
    except keystone_exception.NotFound:
        return None
    else:
        user_data = data[1].get('user', {})
        return user_data.get('name')
