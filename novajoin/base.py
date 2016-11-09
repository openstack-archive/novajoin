# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
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


import paste.urlmap
import routes
import webob.dec

from oslo_config import cfg
from oslo_log import log
from oslo_serialization import jsonutils
from oslo_service import wsgi
from oslo_utils import excutils
from oslo_utils import strutils

import six
import six.moves.urllib.parse as urlparse
import webob.exc

from novajoin import exception

CONF = cfg.CONF
LOG = log.getLogger(__name__)


# Drop all this extra kruft
SUPPORTED_CONTENT_TYPES = (
    'application/json',
)

_MEDIA_TYPE_MAP = {
    'application/json': 'json',
}


class Request(webob.Request):

    def get_content_type(self):
        """Determine content type of the request body.

        Does not do any body introspection, only checks header
        """
        if "Content-Type" not in self.headers:
            return None

        allowed_types = SUPPORTED_CONTENT_TYPES
        content_type = self.content_type

        if content_type not in allowed_types:
            raise exception.InvalidContentType(content_type=content_type)

        return content_type


class Application(object):
    @classmethod
    def factory(cls, global_config, **local_config):
        """Used for paste app factories in paste.deploy config files.

        Any local configuration (that is, values under the [app:APPNAME]
        section of the paste config) will be passed into the `__init__` method
        as kwargs.

        A hypothetical configuration would look like:

            [app:wadl]
            latest_version = 1.3
            paste.app_factory = cinder.api.fancy_api:Wadl.factory

        which would result in a call to the `Wadl` class as

            import cinder.api.fancy_api
            fancy_api.Wadl(latest_version='1.3')

        You could of course re-implement the `factory` method in subclasses,
        but using the kwarg passing it shouldn't be necessary.

        """
        return cls(**local_config)

    def __call__(self, environ, start_response):
        r"""Subclasses will probably want to implement __call__ like this:

        @webob.dec.wsgify(RequestClass=Request)
        def __call__(self, req):
          # Any of the following objects work as responses:

          # Option 1: simple string
          res = 'message\n'

          # Option 2: a nicely formatted HTTP exception page
          res = exc.HTTPForbidden(explanation='Nice try')

          # Option 3: a webob Response object (in case you need to play with
          # headers, or you want to be treated like an iterable)
          res = Response();
          res.app_iter = open('somefile')

          # Option 4: any wsgi app to be run next
          res = self.application

          # Option 5: you can get a Response object for a wsgi app, too, to
          # play with headers etc
          res = req.get_response(self.application)

          # You can then just return your response...
          return res
          # ... or set req.response and return None.
          req.response = res

        See the end of http://pythonpaste.org/webob/modules/dec.html
        for more info.

        """
        raise NotImplementedError('You must implement __call__')


def root_app_factory(loader, global_conf, **local_conf):
    return paste.urlmap.urlmap_factory(loader, global_conf, **local_conf)


class APIMapper(routes.Mapper):
    def routematch(self, url=None, environ=None):
        if url is "":
            result = self._match("", environ)
            return result[0], result[1]
        return routes.Mapper.routematch(self, url, environ)

    def connect(self, *args, **kwargs):
        kwargs.setdefault('requirements', {})
        if not kwargs['requirements'].get('format'):
            kwargs['requirements']['format'] = 'json'
        return routes.Mapper.connect(self, *args, **kwargs)


class ProjectMapper(APIMapper):
    def resource(self, member_name, collection_name, **kwargs):
        routes.Mapper.resource(self,
                               member_name,
                               collection_name,
                               **kwargs)


class APIRouter(wsgi.Router):
    """Routes requests on the API to the appropriate controller and method."""
    ExtensionManager = None  # override in subclasses

    @classmethod
    def factory(cls, global_config, **local_config):
        """Simple paste factory, :class:`cinder.wsgi.Router` doesn't have."""
        return cls()

    def __init__(self, ext_mgr=None):
        mapper = ProjectMapper()
        self.resources = {}
        self._setup_routes(mapper, ext_mgr)
        super(APIRouter, self).__init__(mapper)

    def _setup_routes(self, mapper, ext_mgr):
        raise NotImplementedError


class ActionDispatcher(object):
    """Maps method name to local methods through action name."""

    def dispatch(self, *args, **kwargs):
        """Find and call local method."""
        action = kwargs.pop('action', 'default')
        action_method = getattr(self, six.text_type(action), self.default)
        return action_method(*args, **kwargs)

    def default(self, data):
        raise NotImplementedError()


class TextDeserializer(ActionDispatcher):
    """Default request body deserialization."""

    def deserialize(self, datastring, action='default'):
        return self.dispatch(datastring, action=action)

    def default(self, datastring):
        return {}


class JSONDeserializer(TextDeserializer):

    def _from_json(self, datastring):
        try:
            return jsonutils.loads(datastring)
        except ValueError:
            msg = "cannot understand JSON"
            LOG.error(msg)
            raise exception.MalformedRequestBody(reason=msg)

    def default(self, datastring):
        return {'body': self._from_json(datastring)}


class DictSerializer(ActionDispatcher):
    """Default request body serialization."""

    def serialize(self, data, action='default'):
        return self.dispatch(data, action=action)

    def default(self, data):
        return ""


class JSONDictSerializer(DictSerializer):
    """Default JSON request body serialization."""

    def default(self, data):
        return jsonutils.dump_as_bytes(data)


def action_peek_json(body):
    """Determine action to invoke."""

    try:
        decoded = jsonutils.loads(body)
    except ValueError:
        msg = "cannot understand JSON"
        raise exception.MalformedRequestBody(reason=msg)

    # Make sure there's exactly one key...
    if len(decoded) != 1:
        msg = "too many body keys"
        raise exception.MalformedRequestBody(reason=msg)

    # Return the action and the decoded body...
    return list(decoded.keys())[0]


class ResponseObject(object):
    """Bundles a response object with appropriate serializers.

    Object that app methods may return in order to bind alternate
    serializers with a response object to be serialized.  Its use is
    optional.
    """

    def __init__(self, obj, code=None, headers=None, **serializers):
        """Binds serializers with an object.

        Takes keyword arguments akin to the @serializer() decorator
        for specifying serializers.  Serializers specified will be
        given preference over default serializers or method-specific
        serializers on return.
        """

        self.obj = obj
        self.serializers = serializers
        self._default_code = 200
        self._code = code
        self._headers = headers or {}
        self.serializer = None
        self.media_type = None

    def __getitem__(self, key):
        """Retrieves a header with the given name."""

        return self._headers[key.lower()]

    def __setitem__(self, key, value):
        """Sets a header with the given name to the given value."""

        self._headers[key.lower()] = value

    def __delitem__(self, key):
        """Deletes the header with the given name."""

        del self._headers[key.lower()]

    def _bind_method_serializers(self, meth_serializers):
        """Binds method serializers with the response object.

        Binds the method serializers with the response object.
        Serializers specified to the constructor will take precedence
        over serializers specified to this method.

        :param meth_serializers: A dictionary with keys mapping to
                                 response types and values containing
                                 serializer objects.
        """

        # We can't use update because that would be the wrong
        # precedence
        for mtype, serializer in meth_serializers.items():
            self.serializers.setdefault(mtype, serializer)

    def get_serializer(self, content_type, default_serializers=None):
        """Returns the serializer for the wrapped object.

        Returns the serializer for the wrapped object subject to the
        indicated content type.  If no serializer matching the content
        type is attached, an appropriate serializer drawn from the
        default serializers will be used.  If no appropriate
        serializer is available, raises InvalidContentType.
        """

        default_serializers = default_serializers or {}

        try:
            mtype = _MEDIA_TYPE_MAP.get(content_type, content_type)
            if mtype in self.serializers:
                return mtype, self.serializers[mtype]
            else:
                return mtype, default_serializers[mtype]
        except (KeyError, TypeError):
            raise exception.InvalidContentType(content_type=content_type)

    def preserialize(self, content_type, default_serializers=None):
        """Prepares the serializer that will be used to serialize.

        Determines the serializer that will be used and prepares an
        instance of it for later call.  This allows the serializer to
        be accessed by extensions for, e.g., template extension.
        """

        mtype, serializer = self.get_serializer(content_type,
                                                default_serializers)
        self.media_type = mtype
        self.serializer = serializer()

    def attach(self, **kwargs):
        """Attach slave templates to serializers."""

        if self.media_type in kwargs:
            self.serializer.attach(kwargs[self.media_type])

    def serialize(self, request, content_type, default_serializers=None):
        """Serializes the wrapped object.

        Utility method for serializing the wrapped object.  Returns a
        webob.Response object.
        """

        if self.serializer:
            serializer = self.serializer
        else:
            _mtype, _serializer = self.get_serializer(content_type,
                                                      default_serializers)
            serializer = _serializer()

        response = webob.Response()
        response.status_int = self.code
        for hdr, value in self._headers.items():
            response.headers[hdr] = six.text_type(value)
        response.headers['Content-Type'] = six.text_type(content_type)
        if self.obj is not None:
            body = serializer.serialize(self.obj)
            if isinstance(body, six.text_type):
                body = body.encode('utf-8')
            response.body = body

        return response

    @property
    def code(self):
        """Retrieve the response status."""

        return self._code or self._default_code

    @property
    def headers(self):
        """Retrieve the headers."""

        return self._headers.copy()


class ResourceExceptionHandler(object):
    """Context manager to handle Resource exceptions.

    Used when processing exceptions generated by API implementation
    methods (or their extensions).  Converts most exceptions to Fault
    exceptions, with the appropriate logging.
    """

    def __enter__(self):
        return None

    def __exit__(self, ex_type, ex_value, ex_traceback):
        if not ex_value:
            return True
        if isinstance(ex_value, exception.NotAuthorized):
            msg = six.text_type(ex_value)
            raise Fault(webob.exc.HTTPForbidden(explanation=msg))
        elif isinstance(ex_value, exception.Invalid):
            raise Fault(exception.ConvertedException(
                code=ex_value.code, explanation=six.text_type(ex_value)))
        elif isinstance(ex_value, TypeError):
            exc_info = (ex_type, ex_value, ex_traceback)
            LOG.error(
                'Exception handling resource: %s',
                ex_value, exc_info=exc_info)
            raise Fault(webob.exc.HTTPBadRequest())
        elif isinstance(ex_value, Fault):
            LOG.info("Fault thrown: %s", six.text_type(ex_value))
            raise ex_value
        elif isinstance(ex_value, webob.exc.HTTPException):
            LOG.info("HTTP exception thrown: %s", six.text_type(ex_value))
            raise Fault(ex_value)
        LOG.info("HTTP exception thrown: %s", six.text_type(ex_value))

        # We didn't handle the exception
        return False


class Resource(Application):
    support_api_request_version = False

    def __init__(self, controller, action_peek=None, **deserializers):
        """Initialize Resource.

        :param controller: object that implement methods created by routes lib
        :param action_peek: dictionary of routines for peeking into an action
                            request body to determine the desired action
        """

        self.controller = controller

        default_deserializers = dict(json=JSONDeserializer)
        default_deserializers.update(deserializers)

        self.default_deserializers = default_deserializers
        self.default_serializers = dict(json=JSONDictSerializer)

        self.action_peek = dict(json=action_peek_json)
        self.action_peek.update(action_peek or {})

        # Copy over the actions dictionary
        self.wsgi_actions = {}
        if controller:
            self.register_actions(controller)

        # Save a mapping of extensions
        self.wsgi_extensions = {}
        self.wsgi_action_extensions = {}

    def register_actions(self, controller):
        """Registers controller actions with this resource."""

        actions = getattr(controller, 'wsgi_actions', {})
        for key, method_name in actions.items():
            self.wsgi_actions[key] = getattr(controller, method_name)

    def register_extensions(self, controller):
        """Registers controller extensions with this resource."""

        extensions = getattr(controller, 'wsgi_extensions', [])
        for method_name, action_name in extensions:
            # Look up the extending method
            extension = getattr(controller, method_name)
            if action_name:
                # Extending an action...
                if action_name not in self.wsgi_action_extensions:
                    self.wsgi_action_extensions[action_name] = []
                self.wsgi_action_extensions[action_name].append(extension)
            else:
                # Extending a regular method
                if method_name not in self.wsgi_extensions:
                    self.wsgi_extensions[method_name] = []
                self.wsgi_extensions[method_name].append(extension)

    def get_action_args(self, request_environment):
        """Parse dictionary created by routes library."""

        # NOTE(Vek): Check for get_action_args() override in the
        # controller
        if hasattr(self.controller, 'get_action_args'):
            return self.controller.get_action_args(request_environment)

        try:
            args = request_environment['wsgiorg.routing_args'][1].copy()
        except (KeyError, IndexError, AttributeError):
            return {}

        try:
            del args['controller']
        except KeyError:
            pass

        try:
            del args['format']
        except KeyError:
            pass

        return args

    def get_body(self, request):

        params = urlparse.parse_qs(request.query_string)
        if len(request.body) == 0:
            LOG.debug("Empty body provided in request")
            return None, params

        try:
            content_type = request.get_content_type()
        except exception.InvalidContentType:
            LOG.debug("Unrecognized Content-Type provided in request")
            return None, ''

        if not content_type:
            LOG.debug("No Content-Type provided in request")
            return None, ''

        return content_type, request.body

    def deserialize(self, meth, content_type, body):
        meth_deserializers = getattr(meth, 'wsgi_deserializers', {})
        try:
            mtype = _MEDIA_TYPE_MAP.get(content_type, content_type)
            if mtype in meth_deserializers:
                deserializer = meth_deserializers[mtype]
            else:
                deserializer = self.default_deserializers[mtype]
        except (KeyError, TypeError):
            raise exception.InvalidContentType(content_type=content_type)

        return deserializer().deserialize(body)

    @webob.dec.wsgify(RequestClass=Request)
    def __call__(self, request):
        """WSGI method that controls (de)serialization and method dispatch."""

        LOG.info("%(method)s %(url)s",
                 {"method": request.method,
                  "url": request.url})

        # Identify the action, its arguments, and the requested
        # content type
        action_args = self.get_action_args(request.environ)
        action = action_args.pop('action', None)
        content_type, body = self.get_body(request)
        accept = 'application/json'

        # NOTE(Vek): Splitting the function up this way allows for
        #            auditing by external tools that wrap the existing
        #            function.  If we try to audit __call__(), we can
        #            run into troubles due to the @webob.dec.wsgify()
        #            decorator.
        return self._process_stack(request, action, action_args,
                                   content_type, body, accept)

    def _process_stack(self, request, action, action_args,
                       content_type, body, accept):
        """Implement the processing stack."""

        # Get the implementing method
        try:
            meth, extensions = self.get_method(request, action,
                                               content_type, body)
        except (AttributeError, TypeError):
            return Fault(webob.exc.HTTPNotFound())
        except KeyError as ex:
            msg = "There is no such action: %s" % ex.args[0]
            return Fault(webob.exc.HTTPBadRequest(explanation=msg))
        except exception.MalformedRequestBody:
            msg = "Malformed request body"
            return Fault(webob.exc.HTTPBadRequest(explanation=msg))

        if body:
            msg = ("Action: '%(action)s', calling method: %(meth)s, body: "
                   "%(body)s") % {'action': action,
                                  'body': six.text_type(body),
                                  'meth': six.text_type(meth)}
            LOG.debug(strutils.mask_password(msg))
        else:
            LOG.debug("Calling method '%(meth)s'",
                      {'meth': six.text_type(meth)})

        # Now, deserialize the request body...
        try:
            if content_type:
                contents = self.deserialize(meth, content_type, body)
            else:
                contents = body
        except exception.InvalidContentType:
            msg = "Unsupported Content-Type"
            return Fault(webob.exc.HTTPBadRequest(explanation=msg))
        except exception.MalformedRequestBody:
            msg = "Malformed request body"
            return Fault(webob.exc.HTTPBadRequest(explanation=msg))

        # Update the action args
        action_args.update(contents)

        response = None
        try:
            with ResourceExceptionHandler():
                action_result = self.dispatch(meth, request, action_args)
        except Fault as ex:
            response = ex

        if not response:
            # No exceptions; convert action_result into a
            # ResponseObject
            resp_obj = None
            if isinstance(action_result, dict) or action_result is None:
                resp_obj = ResponseObject(action_result)
            elif isinstance(action_result, ResponseObject):
                resp_obj = action_result
            else:
                response = action_result

            # Run post-processing extensions
            if resp_obj:
                # Do a preserialize to set up the response object
                serializers = getattr(meth, 'wsgi_serializers', {})
                resp_obj._bind_method_serializers(serializers)
                if hasattr(meth, 'wsgi_code'):
                    resp_obj._default_code = meth.wsgi_code
                resp_obj.preserialize(accept, self.default_serializers)

            if resp_obj:
                response = resp_obj.serialize(request, accept,
                                              self.default_serializers)

        try:
            msg_dict = dict(url=request.url, status=response.status_int)
            msg = "%(url)s returned with HTTP %(status)d"
        except AttributeError as e:
            msg_dict = dict(url=request.url, e=e)
            msg = "%(url)s returned a fault: %(e)s"

        LOG.info(msg, msg_dict)

        if hasattr(response, 'headers'):
            for hdr, val in response.headers.items():
                # Headers must be utf-8 strings
                try:
                    # python 2.x
                    response.headers[hdr] = val.encode('utf-8')
                except Exception:  # pylint: disable=broad-except
                    # python 3.x
                    response.headers[hdr] = six.text_type(val)

        return response

    def get_method(self, request, action, content_type, body):
        """Look up the action-specific method and its extensions."""

        # Look up the method
        try:
            if not self.controller:
                meth = getattr(self, action)
            else:
                meth = getattr(self.controller, action)
        except AttributeError as e:
            with excutils.save_and_reraise_exception(e) as ctxt:
                if (not self.wsgi_actions or action not in ['action',
                                                            'create',
                                                            'delete',
                                                            'update']):
                    LOG.exception('Get method error.')
                else:
                    ctxt.reraise = False
        else:
            return meth, self.wsgi_extensions.get(action, [])

        if action == 'action':
            # OK, it's an action; figure out which action...
            mtype = _MEDIA_TYPE_MAP.get(content_type)
            action_name = self.action_peek[mtype](body)
            LOG.debug("Action body: %s", body)
        else:
            action_name = action

        # Look up the action method
        return (self.wsgi_actions[action_name],
                self.wsgi_action_extensions.get(action_name, []))

    def dispatch(self, method, request, action_args):
        """Dispatch a call to the action-specific method."""

        return method(req=request, **action_args)


class Fault(webob.exc.HTTPException):
    """Wrap webob.exc.HTTPException to provide API friendly response."""

    _fault_names = {400: "badRequest",
                    401: "unauthorized",
                    403: "forbidden",
                    404: "itemNotFound",
                    405: "badMethod",
                    409: "conflictingRequest",
                    413: "overLimit",
                    415: "badMediaType",
                    501: "notImplemented",
                    503: "serviceUnavailable"}

    def __init__(self, exc):
        """Create a Fault for the given webob.exc.exception."""
        self.wrapped_exc = exc
        self.status_int = exc.status_int

    @webob.dec.wsgify(RequestClass=Request)
    def __call__(self, req):
        """Generate a WSGI response based on the exception passed to ctor."""
        # Replace the body with fault details.
        code = self.wrapped_exc.status_int
        fault_name = self._fault_names.get(code, "computeFault")
        explanation = self.wrapped_exc.explanation
        fault_data = {
            fault_name: {
                'code': code,
                'message': explanation}}
        if code == 413:
            retry = self.wrapped_exc.headers.get('Retry-After', None)
            if retry:
                fault_data[fault_name]['retryAfter'] = retry

        content_type = 'application/json'
        serializer = {
            'application/json': JSONDictSerializer(),
        }[content_type]

        body = serializer.serialize(fault_data)
        if isinstance(body, six.text_type):
            body = body.encode('utf-8')
        self.wrapped_exc.body = body
        self.wrapped_exc.content_type = content_type

        return self.wrapped_exc

    def __str__(self):
        return self.wrapped_exc.__str__()


class Middleware(Application):
    """Base WSGI middleware.

    These classes require an application to be
    initialized that will be called next.  By default the middleware will
    simply call its wrapped app, or you can override __call__ to customize its
    behavior.

    """

    @classmethod
    def factory(cls, global_config, **local_config):
        """Used for paste app factories in paste.deploy config files.

        Any local configuration (that is, values under the [filter:APPNAME]
        section of the paste config) will be passed into the `__init__` method
        as kwargs.

        A hypothetical configuration would look like:

            [filter:analytics]
            redis_host = 127.0.0.1
            paste.filter_factory = cinder.api.analytics:Analytics.factory

        which would result in a call to the `Analytics` class as

            import cinder.api.analytics
            analytics.Analytics(app_from_paste, redis_host='127.0.0.1')

        You could of course re-implement the `factory` method in subclasses,
        but using the kwarg passing it shouldn't be necessary.

        """
        def _factory(app):
            return cls(app, **local_config)
        return _factory

    def __init__(self, application):
        self.application = application

    def process_request(self, req):
        """Called on each request.

        If this returns None, the next application down the stack will be
        executed. If it returns a response then that response will be returned
        and execution will stop here.

        """
        return None

    def process_response(self, response):
        """Do whatever you'd like to the response."""
        return response

    @webob.dec.wsgify(RequestClass=Request)
    def __call__(self, req):
        response = self.process_request(req)
        if response:
            return response
        response = req.get_response(self.application)
        return self.process_response(response)
