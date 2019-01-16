# Copyright 2011 OpenStack Foundation
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

"""RequestContext: context for requests."""

import copy

from oslo_config import cfg
from oslo_context import context
from oslo_log import log as logging

from novajoin import policy


CONF = cfg.CONF

LOG = logging.getLogger(__name__)


class RequestContext(context.RequestContext):
    """Security context and request information.

    Represents the user taking a given action within the system.

    """
    def __init__(self, *args, **kwargs):

        super(RequestContext, self).__init__(*args, **kwargs)

        # We need to have RequestContext attributes defined
        # when policy.check_is_admin invokes request logging
        # to make it loggable.
        if self.is_admin is None:
            self.is_admin = policy.check_is_admin(self.roles, self)
        elif self.is_admin and 'admin' not in self.roles:
            self.roles.append('admin')

    def to_dict(self):
        result = super(RequestContext, self).to_dict()
        result['user_id'] = self.user_id
        result['user_name'] = self.user_name
        result['project_id'] = self.project_id
        result['project_name'] = self.project_name
        return result

    def elevated(self, read_deleted=None, overwrite=False):
        """Return a version of this context with admin flag set."""
        context = self.deepcopy()
        context.is_admin = True

        if 'admin' not in context.roles:
            context.roles.append('admin')

        if read_deleted is not None:
            context.read_deleted = read_deleted

        return context

    def deepcopy(self):
        return copy.deepcopy(self)
