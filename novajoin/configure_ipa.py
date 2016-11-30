#!/usr/bin/python
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

import getpass
import logging
import os
import pwd
import socket
import sys
import tempfile

from ipalib import api
from ipalib import errors
from ipapython.ipautil import kinit_password
from ipapython.ipautil import run
from ipapython.ipautil import user_input
from novajoin.errors import ConfigurationError

logger = logging.getLogger()


class NovajoinRole(object):
    """One-stop shopping for creating the IPA permissions, privilege and role.

    Assumes that ipalib is imported and initialized and an RPC context
    already exists.
    """

    def __init__(self, keytab='/etc/nova/krb5.keytab', user='nova'):
        self.keytab = keytab
        self.user = user
        self.service = u'nova/%s' % self._get_fqdn()
        self.ccache_name = None

    def _get_fqdn(self):
        """Try to determine the fully-qualfied domain name of this box"""
        fqdn = ""
        try:
            fqdn = socket.getfqdn()
        except Exception:  # pylint: disable=broad-except
            try:
                # assume it is in the IPA domain if it comes back
                # not fully-qualified
                fqdn = socket.gethostname()
                # pylint: disable=no-member
                fqdn = fqdn + '.' + api.env.domain
            except Exception:  # pylint: disable=broad-except
                fqdn = ""
        return fqdn

    def kinit(self, principal, password):
        ccache_dir = tempfile.mkdtemp(prefix='krbcc')
        self.ccache_name = os.path.join(ccache_dir, 'ccache')

        current_ccache = os.environ.get('KRB5CCNAME')
        os.environ['KRB5CCNAME'] = self.ccache_name

        if principal.find('@') == -1:
            # pylint: disable=no-member
            principal = '%s@%s' % (principal, api.env.realm)

        try:
            kinit_password(principal, password, self.ccache_name)
        except RuntimeError as e:
            raise ConfigurationError("Kerberos authentication failed: %s" % e)
        finally:
            if current_ccache:
                os.environ['KRB5CCNAME'] = current_ccache

    def _call_ipa(self, command, args, kw):
        """Call into the IPA API.

        Duplicates are ignored to be idempotent. Other errors are
        ignored implitly because they are encapsulated in the result
        for some calls.
        """
        try:
            api.Command[command](args, **kw)
        except errors.DuplicateEntry:
            pass
        except Exception as e:  # pylint: disable=broad-except
            logger.error("Unhandled exception: %s", e)

    def _add_permissions(self):
        self._call_ipa(u'permission_add', u'Modify host password',
                       {'ipapermright': u'write',
                        'type': u'host',
                        'attrs': u'userpassword'})
        self._call_ipa(u'permission_add', u'Write host certificate',
                       {'ipapermright': u'write',
                        'type': u'host',
                        'attrs': u'usercertificate'})
        self._call_ipa(u'permission_add', u'Modify host userclass',
                       {'ipapermright': u'write',
                        'type': u'host',
                        'attrs': u'userclass'})

    def _add_privileges(self):
        self._call_ipa(u'privilege_add', u'Nova Host Management',
                       {'description': u'Nova Host Management'})

        self._call_ipa(u'privilege_add_permission', u'Nova Host Management',
                       {u'permission': [
                           u'System: add hosts',
                           u'System: remove hosts',
                           u'modify host password',
                           u'modify host userclass',
                           u'modify hosts',
                           u'System: revoke certificate',
                           u'System: manage host keytab',
                           u'System: write host certificate',
                           u'System: retrieve certificates from the ca',
                           u'System: modify services',
                           u'System: manage service keytab',
                           u'System: read dns entries',
                           u'System: remove dns entries',
                           u'System: add dns entries',
                           u'System: update dns entries']})

    def _add_role(self):
        self._call_ipa(u'role_add', u'Nova Host Manager',
                       {'description': u'Nova Host Manager'})
        self._call_ipa(u'role_add_privilege', u'Nova Host Manager',
                       {'privilege': u'Nova Host Management'})
        self._call_ipa(u'role_add_member', u'Nova Host Manager',
                       {u'service': self.service})

    def _add_service(self):
        self._call_ipa(u'service_add', self.service, {'force': True})

    def _get_keytab(self):
        if self.ccache_name:
            current_ccache = os.environ.get('KRB5CCNAME')
            os.environ['KRB5CCNAME'] = self.ccache_name

        try:
            if os.path.exists(self.keytab):
                os.unlink(self.keytab)
        except OSError as e:
            sys.exit('Could not remove %s: %s' % (self.keytab, e))

        try:
            run(['ipa-getkeytab',
                 '-s', api.env.server,  # pylint: disable=no-member
                 '-p', self.service,
                 '-k', self.keytab])
        finally:
            if current_ccache:
                os.environ['KRB5CCNAME'] = current_ccache

        # s/b already validated
        user = pwd.getpwnam(self.user)

        os.chown(self.keytab, user.pw_uid, user.pw_gid)
        os.chmod(self.keytab, 0o600)

    def configure_ipa(self):
        self._add_service()
        self._get_keytab()
        self._add_permissions()
        self._add_privileges()
        self._add_role()


def ipa_options(parser):
    parser.add_argument('--no-kinit',
                        help='Assume the user has already done a kinit',
                        action="store_true", default=False)
    parser.add_argument('--user',
                        help='User that nova services run as',
                        default='nova')
    parser.add_argument('--principal', dest='principal', default='admin',
                        help='principal to use to setup IPA integration')
    parser.add_argument('--password', dest='password',
                        help='password for the principal')
    parser.add_argument('--password-file', dest='passwordfile',
                        help='path to file containing password for '
                             'the principal')
    return parser


def validate_options(args):
    if args.get('no_kinit', False):
        return args

    if not args['principal']:
        args['principal'] = user_input("IPA admin user", "admin",
                                       allow_empty=False)

    if args['passwordfile']:
        try:
            with open(args['passwordfile']) as f:
                args['password'] = f.read()
        except IOError as e:
            raise ConfigurationError('Unable to read password file: %s'
                                     % e)
    if not args['password']:
        try:
            args['password'] = getpass.getpass("Password for %s: " %
                                               args['principal'])
        except EOFError:
            args['password'] = None
        if not args['password']:
            raise ConfigurationError('Password must be provided.')

    try:
        pwd.getpwnam(args['user'])
    except KeyError:
        raise ConfigurationError('User: %s not found on the system' %
                                 args['user'])
