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

"""Handle communication with the Glance image service."""

import copy
import inspect
import itertools
import random
import six
import sys
import time

import glanceclient.exc
from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils
from oslo_utils import timeutils

from novajoin import exception
from novajoin import keystone_client


CONF = cfg.CONF

LOG = logging.getLogger(__name__)

GLANCE_APIVERSION = 2


def get_api_servers():
    """Return iterator of glance api_servers.

    Return iterator of glance api_servers to cycle through the
    list, looping around to the beginning if necessary.
    """

    api_servers = []

    ks = keystone_client.get_client()

    catalog = keystone_client.get_service_catalog(ks)

    image_service = catalog.url_for(service_type='image')
    if image_service:
        api_servers.append(image_service)

    if CONF.glance_api_servers:
        for api_server in CONF.glance_api_servers:
            api_servers.append(api_server)

    random.shuffle(api_servers)
    return itertools.cycle(api_servers)


class GlanceClient(object):
    """Wrapper around glance client."""

    def __init__(self):

        self.version = GLANCE_APIVERSION
        self.api_servers = get_api_servers()
        self.api_server = None
        self.client = None

    def _glance_client(self, context):
        """Instantiate a new glanceclient.Client object."""

        if not self.api_servers:
            return None

        self.api_server = next(self.api_servers)

        params = {}

        session = keystone_client.get_session()
        return glanceclient.Client(str(self.version), self.api_server,
                                   session=session, **params)

    def call(self, context, method, *args, **kwargs):
        """Call a glance client method."""
        if self.client is None:
            self.client = self._glance_client(context)

        retry_excs = (glanceclient.exc.ServiceUnavailable,
                      glanceclient.exc.InvalidEndpoint,
                      glanceclient.exc.CommunicationError)
        retries = CONF.glance_num_retries
        if retries < 0:
            LOG.warning("Treating negative retries as 0")
            retries = 0

        num_attempts = retries + 1

        for attempt in range(1, num_attempts + 1):
            client = self._glance_client(context)
            try:
                controller = getattr(client,
                                     kwargs.pop('controller', 'images'))
                result = getattr(controller, method)(*args, **kwargs)
                if inspect.isgenerator(result):
                    # Convert generator results to a list, so that we can
                    # catch any potential exceptions now and retry the call.
                    return list(result)
                return result
            except retry_excs as e:
                if attempt < num_attempts:
                    extra = "retrying"
                else:
                    extra = "done trying"

                LOG.exception("Error contacting glance server "
                              "'%(server)s' for '%(method)s', "
                              "%(extra)s.",
                              {'server': self.api_server,
                               'method': method, 'extra': extra})
                if attempt == num_attempts:
                    raise exception.GlanceConnectionFailed(
                        server=str(self.api_server), reason=six.text_type(e))
                time.sleep(1)


class GlanceImageService(object):
    """Provides storage and retrieval of disk image objects within Glance."""

    def __init__(self, client=None):
        self._client = client or GlanceClient()
        self._image_schema = None
        self.temp_images = None

    def detail(self, context, **kwargs):
        """Calls out to Glance for a list of detailed image information."""
        params = self._extract_query_params(kwargs)
        try:
            images = self._client.call(context, 'list', **params)
        except Exception:  # pylint: disable=broad-except
            _reraise_translated_exception()
        else:
            if images is None:
                return []

        _images = []
        for image in images:
            _images.append(self._translate_from_glance(context, image))

        return _images

    def _extract_query_params(self, params):
        _params = {}
        accepted_params = ('filters', 'marker', 'limit',
                           'sort_key', 'sort_dir')
        for param in accepted_params:
            if param in params:
                _params[param] = params.get(param)

        return _params

    def _translate_from_glance(self, context, image):
        """Get image metadata from glance image.

        Extract metadata from image and convert it's properties
        to type cinder expected.

        :param image: glance image object
        :return: image metadata dictionary
        """

        if image is None:
            return {}

        # Only v2 is supported in novajoin
        if self._image_schema is None:
            self._image_schema = self._client.call(context, 'get',
                                                   controller='schemas',
                                                   schema_name='image')
        # NOTE(aarefiev): get base image property, store image 'schema'
        #                 is redundant, so ignore it.
        image_meta = {key: getattr(image, key)
                      for key in image.keys()
                      if self._image_schema.is_base_property(key) is True
                      and key != 'schema'}

        # NOTE(aarefiev): nova is expected that all image properties
        # (custom or defined in schema-image.json) stores in
        # 'properties' key.
        image_meta['properties'] = {
            key: getattr(image, key) for key in image.keys()
            if self._image_schema.is_base_property(key) is False}

        image_meta = _convert_timestamps_to_datetimes(image_meta)
        image_meta = _convert_from_string(image_meta)
        return image_meta

    def show(self, context, image_id):
        """Returns a dict with image data for the given opaque image id."""
        try:
            image = self._client.call(context, 'get', image_id)
        except Exception:  # pylint: disable=broad-except
            _reraise_translated_image_exception(image_id)
        else:
            if image is None:
                return {}

        base_image_meta = self._translate_from_glance(context, image)
        return base_image_meta


def _json_loads(properties, attr):
    prop = properties[attr]
    if isinstance(prop, six.string_types):
        properties[attr] = jsonutils.loads(prop)

_CONVERT_PROPS = ('block_device_mapping', 'mappings')


def _convert(method, metadata):
    metadata = copy.deepcopy(metadata)
    properties = metadata.get('properties')
    if properties:
        for attr in _CONVERT_PROPS:
            if attr in properties:
                method(properties, attr)

    return metadata


def _convert_timestamps_to_datetimes(image_meta):
    """Returns image with timestamp fields converted to datetime objects."""
    for attr in ['created_at', 'updated_at', 'deleted_at']:
        if image_meta.get(attr):
            image_meta[attr] = timeutils.parse_isotime(image_meta[attr])
    return image_meta


def _convert_from_string(metadata):
    return _convert(_json_loads, metadata)


def _reraise_translated_image_exception(image_id):
    """Transform the exception for the image but keep its traceback intact."""
    # pylint: disable=unused-variable
    _exc_type, exc_value, exc_trace = sys.exc_info()
    new_exc = _translate_image_exception(image_id, exc_value)
    six.reraise(type(new_exc), new_exc, exc_trace)


def _reraise_translated_exception():
    """Transform the exception but keep its traceback intact."""
    # pylint: disable=unused-variable
    _exc_type, exc_value, exc_trace = sys.exc_info()
    new_exc = _translate_plain_exception(exc_value)
    six.reraise(type(new_exc), new_exc, exc_trace)


def _translate_image_exception(image_id, exc_value):
    if isinstance(exc_value, (glanceclient.exc.Forbidden,
                              glanceclient.exc.Unauthorized)):
        return exception.ImageNotAuthorized(image_id=image_id)
    if isinstance(exc_value, glanceclient.exc.NotFound):
        return exception.ImageNotFound(image_id=image_id)
    if isinstance(exc_value, glanceclient.exc.BadRequest):
        return exception.Invalid(exc_value)
    return exc_value


def _translate_plain_exception(exc_value):
    if isinstance(exc_value, (glanceclient.exc.Forbidden,
                              glanceclient.exc.Unauthorized)):
        return exception.NotAuthorized(exc_value)
    if isinstance(exc_value, glanceclient.exc.NotFound):
        return exception.NotFound(exc_value)
    if isinstance(exc_value, glanceclient.exc.BadRequest):
        return exception.Invalid(exc_value)
    return exc_value


def get_default_image_service():
    return GlanceImageService()
