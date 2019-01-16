# Copyright 2010 OpenStack Foundation
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
"""
Simplified Common Auth Middleware from cinder.
"""


from oslo_config import cfg
from oslo_log import log as logging
import webob.dec
import webob.exc

import novajoin.base
from novajoin import context


CONF = cfg.CONF

LOG = logging.getLogger(__name__)


def pipeline_factory(loader, global_conf, **local_conf):
    """A paste pipeline replica that keys off of auth_strategy."""
    pipeline = local_conf[CONF.auth_strategy]
    if not CONF.api_rate_limit:
        limit_name = CONF.auth_strategy + '_nolimit'
        pipeline = local_conf.get(limit_name, pipeline)
    pipeline = pipeline.split()
    filters = [loader.get_filter(n) for n in pipeline[:-1]]
    app = loader.get_app(pipeline[-1])
    filters.reverse()
    for filt in filters:
        app = filt(app)
    return app


class JoinKeystoneContext(novajoin.base.Middleware):
    """Make a request context from keystone headers."""

    @webob.dec.wsgify(RequestClass=novajoin.base.Request)
    def __call__(self, req):
        try:
            ctx = context.RequestContext.from_environ(req.environ)
        except KeyError:
            LOG.debug("Keystone middleware headers not found in request!")
            return webob.exc.HTTPUnauthorized()
        req.environ['novajoin.context'] = ctx
        return self.application
