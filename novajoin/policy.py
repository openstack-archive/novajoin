# Copyright (c) 2011 OpenStack Foundation
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

"""Policy Engine"""


from oslo_policy import opts as policy_opts
from oslo_policy import policy

from novajoin import config
from novajoin import exception

CONF = config.CONF
policy_opts.set_defaults(CONF, 'policy.json')

_ENFORCER = None

# We have only one endpoint, so there is not a lot of default rules
_RULES = [
    policy.RuleDefault(
        'context_is_admin', 'role:admin',
        "Decides what is required for the 'is_admin:True' check to succeed."),
    policy.RuleDefault(
        'service_project', 'project_name:service',
        "service project"),
    policy.RuleDefault(
        'compute_service_user', 'user_name:nova and rule:service_project',
        "This is usualy the nova service user, which calls the novajoin API, "
        "configured in [vendordata_dynamic_auth] in nova.conf."),
    policy.DocumentedRuleDefault(
        'join:create', 'rule:compute_service_user',
        'Generate the OTP, register it with IPA',
        [{'path': '/', 'method': 'POST'}]
    )
]


def list_rules():
    return _RULES


def get_enforcer():
    global _ENFORCER  # pylint: disable=global-statement
    if not _ENFORCER:
        _ENFORCER = policy.Enforcer(CONF)
        _ENFORCER.register_defaults(list_rules())
    return _ENFORCER


def authorize_action(context, action):
    """Checks that the action can be done by the given context.

    Applies a check to ensure the context's project_id, user_id and others
    can be applied to the given action using the policy enforcement api.
    """

    return authorize(context, action, context.to_dict())


def authorize(context, action, target):
    """Verifies that the action is valid on the target in this context.

       :param context: novajoin context
       :param action: string representing the action to be checked
           this should be colon separated for clarity.
           i.e. ``compute:create_instance``,
           ``compute:attach_volume``,
           ``volume:attach_volume``

       :param target: dictionary representing the object of the action
           for object creation this should be a dictionary representing the
           location of the object e.g. ``{'project_id': context.project_id}``

       :raises PolicyNotAuthorized: if verification fails.

    """

    return get_enforcer().authorize(action, target, context.to_dict(),
                                    do_raise=True,
                                    exc=exception.PolicyNotAuthorized,
                                    action=action)


def check_is_admin(roles, context=None):
    """Whether or not user is admin according to policy setting.

       Can use roles or user_id from context to determine if user is admin.
       In a multi-domain configuration, roles alone may not be sufficient.
    """

    # include project_id on target to avoid KeyError if context_is_admin
    # policy definition is missing, and default admin_or_owner rule
    # attempts to apply.  Since our credentials dict does not include a
    # project_id, this target can never match as a generic rule.
    target = {'project_id': ''}
    if context is None:
        credentials = {'roles': roles}
    else:
        credentials = {'roles': context.roles,
                       'user_id': context.user_id
                       }

    return get_enforcer().authorize('context_is_admin', target, credentials)
