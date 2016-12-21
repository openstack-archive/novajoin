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

import json
import logging
import traceback
import uuid
import webob.exc

from oslo_config import cfg

from novajoin import base
from novajoin import exception
from novajoin.glance import get_default_image_service
from novajoin.ipa import IPAClient
from novajoin import keystone_client
from novajoin.nova import get_instance
from novajoin.util import get_fqdn


CONF = cfg.CONF

LOG = logging.getLogger(__name__)


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


class JoinController(Controller):

    def __init__(self):
        super(JoinController, self).__init__(None)
        self.ipaclient = IPAClient()

    def _get_allowed_hostclass(self, project_name):
        """Get the allowed list of hostclass from configuration."""
        try:
            group = CONF[project_name]
        except cfg.NoSuchOptError:
            # dynamically add the group into the configuration
            group = cfg.OptGroup(project_name, 'project options')
            CONF.register_group(group)
            CONF.register_opt(cfg.ListOpt('allowed_classes'),
                              group=project_name)
        try:
            allowed_classes = CONF[project_name].allowed_classes
        except cfg.NoSuchOptError:
            LOG.error('No allowed_classes config option in [%s]', project_name)
            return []
        else:
            if allowed_classes:
                return allowed_classes
            else:
                return []

    @response(200)
    def create(self, req, body=None):
        """Generate the OTP, register it with IPA

        Options passed in but as yet-unused are and user-data.
        """
        if not body:
            LOG.error('No body in create request')
            raise base.Fault(webob.exc.HTTPBadRequest())

        instance_id = body.get('instance-id')
        image_id = body.get('image-id')
        project_id = body.get('project-id')
        hostname_short = body.get('hostname')
        metadata = body.get('metadata', {})

        if not instance_id:
            LOG.error('No instance-id in request')
            raise base.Fault(webob.exc.HTTPBadRequest())

        if not hostname_short:
            LOG.error('No hostname in request')
            raise base.Fault(webob.exc.HTTPBadRequest())

        if not image_id:
            LOG.error('No image-id in request')
            raise base.Fault(webob.exc.HTTPBadRequest())

        if not project_id:
            LOG.error('No project-id in request')
            raise base.Fault(webob.exc.HTTPBadRequest())

        enroll = metadata.get('ipa_enroll', '')

        if enroll.lower() != 'true':
            LOG.debug('IPA enrollment not requested in instance creation')

        context = req.environ.get('novajoin.context')
        image_service = get_default_image_service()
        image_metadata = {}
        try:
            image = image_service.show(context, image_id)
        except (exception.ImageNotFound, exception.ImageNotAuthorized) as e:
            msg = 'Failed to get image: %s' % e
            LOG.error(msg)
            raise base.Fault(webob.exc.HTTPBadRequest(explanation=msg))
        else:
            image_metadata = image.get('properties', {})

        # Check the image metadata to see if enrollment was requested
        if enroll.lower() != 'true':
            enroll = image_metadata.get('ipa_enroll', '')
            if enroll.lower() != 'true':
                LOG.debug('IPA enrollment not requested in image')
                return {}
            else:
                LOG.debug('IPA enrollment requested in image')
        else:
            LOG.debug('IPA enrollment requested as property')

        # Ensure this instance exists in nova and retrieve the
        # name of the user that requested it.
        instance = get_instance(instance_id)
        if instance is None:
            msg = 'No such instance-id, %s' % instance_id
            LOG.error(msg)
            raise base.Fault(webob.exc.HTTPBadRequest(explanation=msg))

        # TODO(rcritten): Eventually may check the user for permission
        # as well using:
        # user = keystone_client.get_user_name(instance.user_id)

        hostclass = metadata.get('ipa_hostclass')
        if hostclass:
            project_name = keystone_client.get_project_name(project_id)
            allowed_hostclass = self._get_allowed_hostclass(project_name)
            LOG.debug('hostclass %s, allowed_classes %s' %
                      (hostclass, allowed_hostclass))
            if (hostclass not in allowed_hostclass and
                    '*' not in allowed_hostclass):
                msg = "Not allowed to add to hostclass '%s'" % hostclass
                LOG.error(msg)
                raise base.Fault(webob.exc.HTTPForbidden(explanation=msg))
        else:
            project_name = None

        data = {}

        ipaotp = uuid.uuid4().hex

        data['ipaotp'] = ipaotp
        data['hostname'] = get_fqdn(hostname_short, project_name)

        try:
            res = self.ipaclient.add_host(data['hostname'], ipaotp,
                                          metadata, image_metadata)
            if not res:
                # OTP was not added to host, don't return one
                del data['ipaotp']
        except Exception as e:  # pylint: disable=broad-except
            LOG.error('adding host failed %s', e)
            LOG.error(traceback.format_exc())

        self.ipaclient.start_batch_operation()
        # key-per-service
        managed_services = [metadata[key] for key in metadata.keys()
                            if key.startswith('managed_service_')]
        if managed_services:
            self.handle_services(data['hostname'], managed_services)
        # compact json format
        if 'compact_services' in metadata:
            self.handle_compact_services(hostname_short,
                                         metadata.get('compact_services'))
        self.ipaclient.flush_batch_operation()

        return data

    def handle_services(self, base_host, services):
        """Make any host/principal assignments passed into metadata."""
        LOG.debug("In IPAHandleServices")

        hosts_found = list()
        services_found = list()

        for principal in services:
            principal_host = principal.split('/', 1)[1]

            # add host if not present
            if principal_host not in hosts_found:
                self.ipaclient.add_subhost(principal_host)
                hosts_found.append(principal_host)

            # add service if not present
            if principal not in services_found:
                self.ipaclient.add_service(principal)
                services_found.append(principal)

            self.ipaclient.service_add_host(principal, base_host)

    def handle_compact_services(self, base_host_short, service_repr_json):
        """Make any host/principal assignments passed from metadata

        This takes a representation of the services and networks where the
        services are listening on, and forms appropriate hostnames/service
        principals based on this information. The representation looks as the
        following:

            {
                "service1": [
                    "network1",
                    "network2"
                ],
                "service2": [
                    "network2",
                    "network3"
                ],
            }

        This function will then use the short hostname given for the node, and
        will form the service principals. So, for the example above, the
        resulting principals would be:

            service1/hostname-short.network1.novajoindomain
            service1/hostname-short.network2.novajoindomain
            service2/hostname-short.network2.novajoindomain
            service3/hostname-short.network2.novajoindomain

        assuming that the hostname given in the body of the request was
        "hostname-short" and that the domain is called "novajoindomain".

        This attempts to do a more compact representation since the nova
        metadta entries have a limit of 255 characters.
        """
        LOG.debug("In handle compact services")

        service_repr = json.loads(service_repr_json)
        hosts_found = list()
        services_found = list()
        base_host = get_fqdn(base_host_short)

        for service_name, net_list in service_repr.items():
            for network in net_list:
                host_short = "%s.%s" % (base_host_short, network)
                principal_host = get_fqdn(host_short)
                principal = "%s/%s" % (service_name, principal_host)

                # add host if not present
                if principal_host not in hosts_found:
                    self.ipaclient.add_subhost(principal_host)
                    hosts_found.append(principal_host)

                # add service if not present
                if principal not in services_found:
                    self.ipaclient.add_service(principal)
                    services_found.append(principal)

                self.ipaclient.service_add_host(principal, base_host)
