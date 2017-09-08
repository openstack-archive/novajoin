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

import os
import time
import uuid

try:
    from gssapi.exceptions import GSSError
    from ipalib import api
    from ipalib import errors
    ipalib_imported = True
except ImportError:
    # ipalib/ipapython are not available in PyPy yet, don't make it
    # a showstopper for the tests.
    ipalib_imported = False

if ipalib_imported:
    try:
        from ipapython.ipautil import kinit_keytab
    except ImportError:
        # The import moved in freeIPA 4.5.0
        try:
            from ipalib.install.kinit import kinit_keytab
        except ImportError:
            ipalib_imported = False

from novajoin.util import get_domain
from oslo_config import cfg
from oslo_log import log as logging
from six.moves.configparser import SafeConfigParser


CONF = cfg.CONF

LOG = logging.getLogger(__name__)


class IPANovaJoinBase(object):

    def __init__(self, backoff=0):
        try:
            self.ntries = CONF.connect_retries
        except cfg.NoSuchOptError:
            self.ntries = 1
        if not ipalib_imported:
            return
        self.ccache = "MEMORY:" + str(uuid.uuid4())
        os.environ['KRB5CCNAME'] = self.ccache
        if self._ipa_client_configured() and not api.isdone('finalize'):
            (hostname, realm) = self.get_host_and_realm()
            kinit_keytab(str('nova/%s@%s' % (hostname, realm)),
                         CONF.keytab, self.ccache)
            api.bootstrap(context='novajoin')
            api.finalize()
        self.batch_args = list()
        self.backoff = backoff

    def split_principal(self, principal):
        """Split a principal into its components. Copied from IPA 4.0.0"""
        service = hostname = realm = None

        # Break down the principal into its component parts, which may or
        # may not include the realm.
        sp = principal.split('/')
        if len(sp) != 2:
            raise errors.MalformedServicePrincipal(reason=_('missing service'))

        service = sp[0]
        if len(service) == 0:
            raise errors.MalformedServicePrincipal(reason=_('blank service'))
        sr = sp[1].split('@')
        if len(sr) > 2:
            raise errors.MalformedServicePrincipal(
                reason=_('unable to determine realm'))

        hostname = sr[0].lower()
        if len(sr) == 2:
            realm = sr[1].upper()
            # At some point we'll support multiple realms
            if realm != api.env.realm:
                raise errors.RealmMismatch()
        else:
            realm = api.env.realm

        # Note that realm may be None.
        return (service, hostname, realm)

    def split_hostname(self, hostname):
        """Split a hostname into its host and domain parts"""
        parts = hostname.split('.')
        domain = ('.'.join(parts[1:]) + '.').decode('UTF-8')
        return (parts[0], domain)

    def get_host_and_realm(self):
        """Return the hostname and IPA realm name.

           IPA 4.4 introduced the requirement that the schema be
           fetched when calling finalize(). This is really only used by
           the ipa command-line tool but for now it is baked in.
           So we have to get a TGT first but need the hostname and
           realm. For now directly read the IPA config file which is
           in INI format and pull those two values out and return as
           a tuple.
        """
        config = SafeConfigParser()
        config.read('/etc/ipa/default.conf')
        hostname = config.get('global', 'host')
        realm = config.get('global', 'realm')

        return (hostname, realm)

    def __backoff(self):
        LOG.debug("Backing off %s seconds", self.backoff)
        time.sleep(self.backoff)
        if self.backoff < 1024:
            self.backoff = self.backoff * 2

    def __get_connection(self):
        """Make a connection to IPA or raise an error."""
        tries = 0

        while (tries <= self.ntries) or (self.backoff > 0):
            if self.backoff == 0:
                LOG.debug("Attempt %d of %d", tries, self.ntries)
            if api.Backend.rpcclient.isconnected():
                api.Backend.rpcclient.disconnect()
            try:
                api.Backend.rpcclient.connect()
                # ping to force an actual connection in case there is only one
                # IPA master
                api.Command[u'ping']()
            except (errors.CCacheError,
                    errors.TicketExpired,
                    errors.KerberosError) as e:
                LOG.debug("kinit again: %s", e)
                # pylint: disable=no-member
                try:
                    kinit_keytab(str('nova/%s@%s' %
                                 (api.env.host, api.env.realm)),
                                 CONF.keytab,
                                 self.ccache)
                except GSSError as e:
                    LOG.debug("kinit failed: %s", e)
                if tries > 0 and self.backoff:
                    self.__backoff()
                tries += 1
            except errors.NetworkError:
                tries += 1
                if self.backoff:
                    self.__backoff()
            else:
                return

    def start_batch_operation(self):
        """Start a batch operation.

           IPA method calls will be collected in a batch job
           and submitted to IPA once all the operations have collected
           by a call to _flush_batch_operation().
        """
        self.batch_args = list()

    def _add_batch_operation(self, command, *args, **kw):
        """Add an IPA call to the batch operation"""
        self.batch_args.append({
            "method": command,
            "params": [args, kw],
        })

    def flush_batch_operation(self):
        """Make an IPA batch call."""
        LOG.debug("flush_batch_operation")
        if not self.batch_args:
            return None

        kw = {}
        LOG.debug(self.batch_args)

        return self._call_ipa('batch', *self.batch_args, **kw)

    def _call_ipa(self, command, *args, **kw):
        """Make an IPA call."""
        if not api.Backend.rpcclient.isconnected():
            self.__get_connection()
        if 'version' not in kw:
            kw['version'] = u'2.146'  # IPA v4.2.0 for compatibility

        while True:
            try:
                result = api.Command[command](*args, **kw)
                LOG.debug(result)
                return result
            except (errors.CCacheError,
                    errors.TicketExpired,
                    errors.KerberosError):
                LOG.debug("Refresh authentication")
                self.__get_connection()
            except errors.NetworkError:
                if self.backoff:
                    self.__backoff()
                else:
                    raise

    def _ipa_client_configured(self):
        """Determine if the machine is an enrolled IPA client.

           Return boolean indicating whether this machine is enrolled
           in IPA. This is a rather weak detection method but better
           than nothing.
        """

        return os.path.exists('/etc/ipa/default.conf')


class IPAClient(IPANovaJoinBase):

    def add_host(self, hostname, ipaotp, metadata=None, image_metadata=None):
        """Add a host to IPA.

        If requested in the metadata, add a host to IPA. The assumption
        is that hostname is already fully-qualified.

        Because this is triggered by a metadata request, which can happen
        multiple times, first we try to update the OTP in the host entry
        and if that fails due to NotFound the host is added.
        """

        LOG.debug('In IPABuildInstance')

        if not self._ipa_client_configured():
            LOG.debug('IPA is not configured')
            return False

        if metadata is None:
            metadata = {}
        if image_metadata is None:
            image_metadata = {}

        params = [hostname]

        hostclass = metadata.get('ipa_hostclass', '')
        location = metadata.get('ipa_host_location', '')
        osdistro = image_metadata.get('os_distro', '')
        osver = image_metadata.get('os_version', '')
        # 'description': 'IPA host for %s' % inst.display_description,
        hostargs = {
            'description': u'IPA host for OpenStack',
            'userpassword': ipaotp.decode('UTF-8'),
            'force': True  # we don't have an ip addr yet so
                           # use force to add anyway
        }
        if hostclass:
            hostargs['userclass'] = hostclass
        if osdistro or osver:
            hostargs['nsosversion'] = '%s %s' % (osdistro, osver)
            hostargs['nsosversion'] = hostargs['nsosversion'].strip()
        if location:
            hostargs['nshostlocation'] = location

        modargs = {
            'userpassword': ipaotp.decode('UTF-8'),
        }

        if not ipalib_imported:
            return True

        try:
            self._call_ipa('host_mod', *params, **modargs)
        except errors.NotFound:
            try:
                self._call_ipa('host_add', *params, **hostargs)
            except (errors.DuplicateEntry, errors.ValidationError,
                    errors.DNSNotARecordError):
                pass
        except errors.ValidationError:
            # Updating the OTP on an enrolled-host is not allowed
            # in IPA and really a no-op.
            return False

        return True

    def add_subhost(self, hostname):
        """Add a subhost to IPA.

        Servers can have multiple network interfaces, and therefore can
        have multiple aliases.  Moreover, they can part of a service using
        a virtual host (VIP).  These aliases are denoted 'subhosts',
        """
        LOG.debug('Adding subhost: ' + hostname)
        params = [hostname]
        hostargs = {'force': True}
        self._add_batch_operation('host_add', *params, **hostargs)

    def delete_subhost(self, hostname, batch=True):
        """Delete a subhost from IPA.

        Servers can have multiple network interfaces, and therefore can
        have multiple aliases.  Moreover, they can part of a service using
        a virtual host (VIP).  These aliases are denoted 'subhosts',
        """
        LOG.debug('Deleting subhost: ' + hostname)
        host_params = [hostname]

        (hn, domain) = self.split_hostname(hostname)

        dns_params = [domain, hn]

        # If there is no DNS entry, this operation fails
        host_kw = {'updatedns': False, }

        dns_kw = {'del_all': True, }

        if batch:
            self._add_batch_operation('host_del', *host_params, **host_kw)
            self._add_batch_operation('dnsrecord_del', *dns_params,
                                      **dns_kw)
        else:
            self._call_ipa('host_del', *host_params, **host_kw)
            try:
                self._call_ipa('dnsrecord_del', *dns_params, **dns_kw)
            except (errors.NotFound, errors.ACIError):
                # Ignore DNS deletion errors
                pass

    def delete_host(self, hostname, metadata=None):
        """Delete a host from IPA and remove all related DNS entries."""
        LOG.debug('In IPADeleteInstance')

        if not self._ipa_client_configured():
            LOG.debug('IPA is not configured')
            return

        if metadata is None:
            metadata = {}

        # TODO(rcrit): lookup instance in nova to get metadata to see if
        # the host was enrolled. For now assume yes.

        params = [hostname]
        kw = {
            'updatedns': False,
        }
        try:
            self._call_ipa('host_del', *params, **kw)
        except (errors.NotFound, errors.ACIError):
            # Trying to delete a host that doesn't exist will raise an ACIError
            # to hide whether the entry exists or not
            pass

        (hn, domain) = self.split_hostname(hostname)

        dns_params = [domain, hn]

        dns_kw = {'del_all': True, }

        try:
            self._call_ipa('dnsrecord_del', *dns_params, **dns_kw)
        except (errors.NotFound, errors.ACIError):
            # Ignore DNS deletion errors
            pass

    def add_service(self, principal):
        LOG.debug('Adding service: ' + principal)
        params = [principal]
        service_args = {'force': True}
        self._add_batch_operation('service_add', *params, **service_args)

    def service_add_host(self, service_principal, host):
        """Add a host to a service.

        In IPA there is a relationship between a host and the services for
        that host. The host has the right to manage keytabs and SSL
        certificates for its own services. There are reasons that a host
        may want to manage services for another host or service:
        virtualization, load balancing, etc. In order to do this you mark
        the host or service as being "managed by" another host. For services
        in IPA this is done using the service-add-host API.
        """
        LOG.debug('Adding principal ' + service_principal + ' to host ' + host)
        params = [service_principal]
        service_args = {'host': (host,)}
        self._add_batch_operation('service_add_host', *params, **service_args)

    def service_has_hosts(self, service_principal):
        """Return True if hosts other than parent manages this service"""

        LOG.debug('Checking if principal ' + service_principal + ' has hosts')
        params = [service_principal]
        service_args = {}
        try:
            result = self._call_ipa('service_show', *params, **service_args)
        except errors.NotFound:
            raise KeyError
        serviceresult = result['result']

        try:
            (service, hostname, realm) = self.split_principal(
                service_principal
            )
        except errors.MalformedServicePrincipal as e:
            LOG.error("Unable to split principal %s: %s", service_principal, e)
            raise

        for candidate in serviceresult.get('managedby_host', []):
            if candidate != hostname:
                return True
        return False

    def host_has_services(self, service_host):
        """Return True if this host manages any services"""
        LOG.debug('Checking if host ' + service_host + ' has services')
        params = []
        service_args = {'man_by_host': service_host}
        result = self._call_ipa('service_find', *params, **service_args)
        return result['count'] > 0

    def delete_service(self, principal, batch=True):
        LOG.debug('Deleting service: ' + principal)
        params = [principal]
        service_args = {}
        if batch:
            self._add_batch_operation('service_del', *params, **service_args)
        else:
            return self._call_ipa('service_del', *params, **service_args)

    def add_ip(self, hostname, floating_ip):
        """Add a floating IP to a given hostname."""
        LOG.debug('In add_ip')

        if not self._ipa_client_configured():
            LOG.debug('IPA is not configured')
            return

        params = [{"__dns_name__": get_domain() + "."},
                  {"__dns_name__": hostname}]
        kw = {'a_part_ip_address': floating_ip}

        try:
            self._call_ipa('dnsrecord_add', *params, **kw)
        except (errors.DuplicateEntry, errors.ValidationError):
            pass

    def remove_ip(self, hostname, floating_ip):
        """Remove a floating IP from a given hostname."""
        LOG.debug('In remove_ip')

        if not self._ipa_client_configured():
            LOG.debug('IPA is not configured')
            return

        LOG.debug('Current a no-op')
