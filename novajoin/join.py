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

import uuid
import logging
import webob.exc
from oslo_serialization import jsonutils
from oslo_config import cfg
from novajoin.ipa import IPAClient
from novajoin import base
from novajoin import cache

CONF = cfg.CONF

LOG = logging.getLogger(__name__)


def create_version_resource():
    return base.Resource(VersionsController())


def create_join_resource():
    return base.Resource(JoinController())


def response(code):
    """Attaches response code to a method.

    This decorator associates a response code with a method.  Note
    that the function attributes are directly manipulated; the method
    is not wrapped.
    """

    def decorator(func):
        func.wsgi_code = code
        return func
    return decorator


class Versions(base.APIRouter):
    """Route versions requests."""

    def _setup_routes(self, mapper, ext_mgr):
        self.resources['versions'] = create_version_resource()
        mapper.connect('versions', '/',
                       controller=self.resources['versions'],
                       action='all')
        mapper.redirect('', '/')


class Join(base.APIRouter):
    """Route join requests."""

    def _setup_routes(self, mapper, ext_mgr):
        self.resources['join'] = create_join_resource()
        mapper.connect('join', '/',
                       controller=self.resources['join'],
                       action='create')
        mapper.redirect('', '/')


class Controller(object):
    """Default controller."""

    _view_builder_class = None

    def __init__(self, view_builder=None):
        """Initialize controller with a view builder instance."""
        if view_builder:
            self._view_builder = view_builder
        else:
            self._view_builder = None


class VersionsController(Controller):

    def __init__(self):
        super(VersionsController, self).__init__(None)

    @response(300)
    def all(self, req, body=None):
        """Return all known versions."""
        if body:
            return {'views': '%s' % body.get('foo', '')}

        return {'views': 'foo'}


class JoinController(Controller):

    def __init__(self):
        super(JoinController, self).__init__(None)
        self.uuidcache = cache.Cache()

    @response(200)
    def create(self, req, body=None):
        """Generate the OTP, register it with IPA"""
        if not body:
            raise base.Fault(webob.exc.HTTPBadRequest())

        project_id = body.get('project-id')
        instance_id = body.get('instance-id')
        image_id = body.get('image-id')
        user_data = body.get('user-data')
        hostname = body.get('hostname')
        metadata = body.get('metadata')
        system_metadata = body.get('system_metadata')

        enroll = metadata.get('ipa_enroll', '')

        if enroll.lower() != 'true':
            LOG.debug('IPA enrollment not requested')
            return {}

        if instance_id:
            data = self.uuidcache.get(instance_id)
            if data:
                return jsonutils.loads(data)

        data = {}

        ipaotp = uuid.uuid4().hex

        data['ipaotp'] = ipaotp
        if hostname:
            if CONF.project_subdomain:
                hostname = '%s.%s.%s' % (hostname, project, CONF.domain)
            else:
                hostname = '%s.%s' % (hostname, CONF.domain)

            data['hostname'] = hostname

        if instance_id:
            try:
                self.uuidcache.add(instance_id, jsonutils.dumps(data))
                ipaclient = IPAClient()
                ipaclient.add_host(data['hostname'], ipaotp, metadata,
                                   system_metadata)
            except Exception as e:
                LOG.error('caching or adding host failed %s', e)

        return data
