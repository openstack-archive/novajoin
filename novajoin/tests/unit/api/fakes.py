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

import webob

from novajoin import base
from novajoin import context
from novajoin.join import Join
from novajoin.tests.unit import fake_constants as fake


class FakeRequestContext(context.RequestContext):
    def __init__(self, *args, **kwargs):
        kwargs['auth_token'] = kwargs.get(fake.USER_ID, fake.PROJECT_ID)
        super(FakeRequestContext, self).__init__(*args, **kwargs)


class HTTPRequest(webob.Request):

    @classmethod
    def blank(cls, *args, **kwargs):
        if args is not None:
            if 'v1' in args[0]:
                kwargs['base_url'] = 'http://localhost/v1'
        use_admin_context = kwargs.pop('use_admin_context', False)
        version = kwargs.pop('version', '1.0')
        out = base.Request.blank(*args, **kwargs)
        out.environ['cinder.context'] = FakeRequestContext(
            fake.USER_ID,
            fake.PROJECT_ID,
            is_admin=use_admin_context)
        out.api_version_request = Join(version)
        return out
