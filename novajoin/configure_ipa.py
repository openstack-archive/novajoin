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
import six
import socket
import string
import sys
import tempfile

from ipalib import api
from ipalib import errors
from ipalib import x509
from ipapython import certdb
from ipapython import ipaldap
from ipapython.ipautil import CalledProcessError
from ipapython.ipautil import ipa_generate_password
from ipapython.ipautil import realm_to_suffix
from ipapython.ipautil import run
from ipapython.ipautil import user_input
from ipapython.ipautil import write_tmp_file
from ipapython import version
from novajoin.errors import ConfigurationError

try:
    from ipalib import certstore
except ImportError:
    # The import moved in freeIPA 4.5.0
    from ipalib.install import certstore

try:
    from ipapython.ipautil import kinit_password
except ImportError:
    # The import moved in freeIPA 4.5.0
    from ipalib.install.kinit import kinit_password

if version.NUM_VERSION >= 40500:
    from cryptography.hazmat.primitives import serialization

import nss.nss as nss

logger = logging.getLogger()

allowed_chars = string.letters + string.digits

KRB5_CONF_TMPL = """
includedir /var/lib/sss/pubconf/krb5.include.d/

[libdefaults]
  default_realm = $REALM
  dns_lookup_realm = false
  dns_lookup_kdc = false
  rdns = false
  ticket_lifetime = 24h
  forwardable = yes
  udp_preference_limit = 0
  default_ccache_name = KEYRING:persistent:%{uid}

[realms]
  $REALM = {
    kdc = $MASTER:88
    master_kdc = $MASTER:88
    admin_server = $MASTER:749
    default_domain = $DOMAIN

  }
[domain_realm]
  .$DOMAIN = $REALM
  $DOMAIN = $REALM
"""


class NovajoinRole(object):
    """One-stop shopping for creating the IPA permissions, privilege and role.

    Assumes that ipalib is imported and initialized and an RPC context
    already exists.
    """

    def __init__(self, keytab='/etc/nova/krb5.keytab', user='nova',
                 hostname=None):
        self.keytab = keytab
        self.user = user
        if not hostname:
            self.hostname = self._get_fqdn()
        else:
            self.hostname = hostname
        self.service = u'nova/%s' % self.hostname
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

    def write_tmp_krb5_conf(self, opts, filename):
        options = {'MASTER': opts.server,
                   'DOMAIN': opts.domain,
                   'REALM': opts.realm}

        template = string.Template(KRB5_CONF_TMPL)
        text = template.substitute(options)
        with open(filename, 'w+') as f:
            f.write(text)

    def create_krb5_conf(self, opts):
        (krb_fd, krb_name) = tempfile.mkstemp()
        os.close(krb_fd)

        self.write_tmp_krb5_conf(opts, krb_name)

        return krb_name

    def _get_ca_certs(self, server, realm):
        basedn = realm_to_suffix(realm)
        if version.NUM_VERSION >= 40500:
            ldap_uri = ipaldap.get_ldap_uri(server)
            try:
                conn = ipaldap.LDAPClient(ldap_uri, sasl_nocanon=True)
                conn.gssapi_bind()
                certs = certstore.get_ca_certs(conn, basedn, realm, False)
            except Exception as e:
                raise ConfigurationError("get_ca_certs() error: %s" % e)
        else:
            try:
                conn = ipaldap.IPAdmin(server, sasl_nocanon=True)
                conn.do_sasl_gssapi_bind()
                certs = certstore.get_ca_certs(conn, basedn, realm, False)
            except Exception as e:
                raise ConfigurationError("get_ca_certs() error: %s" % e)

        certs = [x509.load_certificate(c[0], x509.DER) for c in certs
                 if c[2] is not False]

        return certs

    def create_nssdb(self, server, realm):
        """Retrieve IPA CA certificate chain to NSS database.

        Retrieve the CA cert chain from IPA and add it to a
        temporary NSS database and return the path to it.

        NOTE: For IPA v4.4.0.
        """
        nss.nss_init_nodb()
        nss_db = certdb.NSSDatabase()

        ca_certs = self._get_ca_certs(server, realm)
        ca_certs = [cert.der_data for cert in ca_certs]

        # Add CA certs to a temporary NSS database
        try:
            pwd_file = write_tmp_file(ipa_generate_password())
            nss_db.create_db(pwd_file.name)
            for i, cert in enumerate(ca_certs):
                nss_db.add_cert(cert, 'CA certificate %d' % (i + 1), 'C,,')
        except CalledProcessError:
            raise ConfigurationError(
                'Failed to add CA to temporary NSS database.')

        return nss_db

    def create_cafile(self, server, realm):
        """Retrieve IPA CA certificate chain to a file

        Retrieve the CA cert chain from IPA and add it to a
        temporary file and return the name of the file.

        The caller is responsible for removing the temporary file.

        NOTE: For IPA v4.5.0+
        """
        (cafile_fd, cafile_name) = tempfile.mkstemp()
        os.close(cafile_fd)

        ca_certs = self._get_ca_certs(server, realm)
        ca_certs = [cert.public_bytes(serialization.Encoding.PEM)
                    for cert in ca_certs]
        x509.write_certificate_list(ca_certs, cafile_name)

        return cafile_name

    def kinit(self, principal, realm, password, config=None):
        ccache_dir = tempfile.mkdtemp(prefix='krbcc')
        self.ccache_name = os.path.join(ccache_dir, 'ccache')

        current_ccache = os.environ.get('KRB5CCNAME')
        os.environ['KRB5CCNAME'] = self.ccache_name

        if principal.find('@') == -1:
            # pylint: disable=no-member
            principal = '%s@%s' % (principal, realm)

        try:
            kinit_password(principal, password, self.ccache_name,
                           config=config)
        except RuntimeError as e:
            raise ConfigurationError("Kerberos authentication failed: %s" % e)
        finally:
            if current_ccache:
                os.environ['KRB5CCNAME'] = current_ccache

        return ccache_dir

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
        logging.debug('Add permissions')
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
        self._call_ipa(u'permission_add',
                       u'Modify service managedBy attribute',
                       {'ipapermright': u'write',
                        'type': u'service',
                        'attrs': u'managedby'})

    def _add_privileges(self):
        logging.debug('Add privileges')
        self._call_ipa(u'privilege_add', u'Nova Host Management',
                       {'description': u'Nova Host Management'})

        self._call_ipa(u'privilege_add_permission', u'Nova Host Management',
                       {u'permission': [
                           u'System: add hosts',
                           u'System: remove hosts',
                           u'modify host password',
                           u'modify host userclass',
                           u'modify hosts',
                           u'modify service managedBy attribute',
                           u'System: Add krbPrincipalName to a Host',
                           u'System: Add Services',
                           u'System: Remove Services',
                           u'System: revoke certificate',
                           u'System: manage host keytab',
                           u'System: write host certificate',
                           u'System: retrieve certificates from the ca',
                           u'System: modify services',
                           u'System: manage service keytab',
                           u'System: read dns entries',
                           u'System: remove dns entries',
                           u'System: add dns entries',
                           u'System: update dns entries',
                           u'Retrieve Certificates from the CA',
                           u'Revoke Certificate']})

    def _add_role(self):
        logging.debug('Add role')
        self._call_ipa(u'role_add', u'Nova Host Manager',
                       {'description': u'Nova Host Manager'})
        self._call_ipa(u'role_add_privilege', u'Nova Host Manager',
                       {'privilege': u'Nova Host Management'})
        self._call_ipa(u'role_add_member', u'Nova Host Manager',
                       {u'service': self.service})

    def _add_host(self, filename):
        logging.debug('Add host %s', self.hostname)
        if version.NUM_VERSION >= 40500:
            otp = ipa_generate_password(special=None)
        else:
            otp = ipa_generate_password(allowed_chars)

        self._call_ipa(u'host_add', six.text_type(self.hostname),
                       {'description': u'Undercloud host',
                        'userpassword': six.text_type(otp),
                        'force': True})
        if filename:
            with open(filename, "w") as fd:
                fd.write("%s\n" % otp)
        else:
            return otp

    def _add_service(self):
        logging.debug('Add service %s', self.service)
        self._call_ipa(u'service_add', self.service, {'force': True})

    def _get_keytab(self):
        logging.debug('Getting keytab %s for %s', self.keytab, self.service)
        if self.ccache_name:
            current_ccache = os.environ.get('KRB5CCNAME')
            os.environ['KRB5CCNAME'] = self.ccache_name
        else:
            current_ccache = None

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

    def configure_ipa(self, precreate, otp_filename=None):
        otp = None
        if precreate:
            otp = self._add_host(otp_filename)
        self._add_service()
        if not precreate:
            self._get_keytab()
        self._add_permissions()
        self._add_privileges()
        self._add_role()
        if otp:
            print(otp)


def ipa_options(parser):
    parser.add_argument('--debug',
                        help='Additional logging output',
                        action="store_true", default=False)
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
    parser.add_argument('--precreate', default=False,
                        help='Pre-create the IPA host with an OTP',
                        action="store_true")
    noconfig = parser.add_argument_group('Pre-create options')
    noconfig.add_argument('--server', dest='server',
                          help='IPA server')
    noconfig.add_argument('--realm', dest='realm',
                          help='IPA realm name')
    noconfig.add_argument('--domain', dest='domain',
                          help='IPA domain name')
    noconfig.add_argument('--hostname', dest='hostname',
                          help='Hostname of IPA host to create')
    noconfig.add_argument('--otp-file', dest='otp_filename',
                          help='File to write OTP to instead of stdout')
    return parser


def validate_options(opts):
    if opts.precreate and not os.path.exists('/etc/ipa/default.conf'):
        if not opts.hostname:
            raise ConfigurationError('hostname is required')

        if not opts.domain:
            raise ConfigurationError('IPA domain is required')

        if not opts.realm:
            raise ConfigurationError('IPA realm is required')

        if not opts.server:
            raise ConfigurationError('IPA server is required')

    if opts.no_kinit:
        return

    if not opts.principal:
        opts.principal = user_input("IPA admin user", "admin",
                                    allow_empty=False)

    if opts.passwordfile:
        try:
            with open(opts.passwordfile) as f:
                opts.password = f.read()
        except IOError as e:
            raise ConfigurationError('Unable to read password file: %s'
                                     % e)
    if not opts.password:
        try:
            opts.password = getpass.getpass("Password for %s: " %
                                            opts.principal)
        except EOFError:
            opts.password = None
        if not opts.password:
            raise ConfigurationError('Password must be provided.')

    if not opts.precreate:
        try:
            pwd.getpwnam(opts.user)
        except KeyError:
            raise ConfigurationError('User: %s not found on the system' %
                                     opts.user)
