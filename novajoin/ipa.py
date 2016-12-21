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
import uuid

try:
    from ipalib import api
    from ipalib import errors
    from ipapython.ipautil import kinit_keytab
    ipalib_imported = True
except ImportError:
    # ipalib/ipapython are not available in PyPy yet, don't make it
    # a showstopper for the tests.
    ipalib_imported = False

from novajoin.util import get_domain
from oslo_config import cfg
from oslo_log import log as logging


CONF = cfg.CONF

LOG = logging.getLogger(__name__)


class IPANovaJoinBase(object):

    def __init__(self):
        try:
            self.ntries = CONF.connect_retries
        except cfg.NoSuchOptError:
            self.ntries = 1
        if not ipalib_imported:
            return
        self.ccache = "MEMORY:" + str(uuid.uuid4())
        os.environ['KRB5CCNAME'] = self.ccache
        if self._ipa_client_configured() and not api.isdone('finalize'):
            api.bootstrap(context='novajoin')
            api.finalize()
        self.batch_args = list()

    def __get_connection(self):
        """Make a connection to IPA or raise an error."""
        tries = 0

        while tries <= self.ntries:
            try:
                api.Backend.rpcclient.connect()
            except (errors.CCacheError, errors.TicketExpired) as e:
                LOG.debug("kinit again: %s", e)
                # pylint: disable=no-member
                kinit_keytab(str('nova/%s@%s' %
                             (api.env.host, api.env.realm)),
                             CONF.keytab,
                             self.ccache)
                tries += 1
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
        """Make an IPA batch call

           Try twice to run the command. One execution may fail if we
           previously had a connection but the ticket expired.
        """
        LOG.debug("flush_batch_operation")
        if not self.batch_args:
            return None

        if not api.Backend.rpcclient.isconnected():
            self.__get_connection()
        kw = {'version': u'2.146'}  # IPA v4.2.0 for compatibility

        try:
            result = api.Command['batch'](*self.batch_args, **kw)
            LOG.debug(result)
            return result
        except (errors.CCacheError, errors.TicketExpired):
            LOG.debug("Refresh authentication")
            api.Backend.rpcclient.disconnect()
            self.__get_connection()
            result = api.Command['batch'](*self.batch_arhs, **kw)
            LOG.debug(result)
            return result

    def _call_ipa(self, command, *args, **kw):
        """Make an IPA call.

           Try twice to run the command. One execution may fail if we
           previously had a connection but the ticket expired.
        """

        if not api.Backend.rpcclient.isconnected():
            self.__get_connection()
        if 'version' not in kw:
            kw['version'] = u'2.146'  # IPA v4.2.0 for compatibility
        try:
            result = api.Command[command](*args, **kw)
            LOG.debug(result)
            return result
        except (errors.CCacheError, errors.TicketExpired):
            LOG.debug("Refresh authentication")
            api.Backend.rpcclient.disconnect()
            self.__get_connection()
            result = api.Command[command](*args, **kw)
            LOG.debug(result)
            return result

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
        params = [hostname]

        # If there is no DNS entry, this operation fails
        kw = {'updatedns': False, }

        if batch:
            self._add_batch_operation('host_del', *params, **kw)
        else:
            return self._call_ipa('host_del', *params, **kw)

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
            'updatedns': True,
        }
        try:
            self._call_ipa('host_del', *params, **kw)
        except errors.NotFound:
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
        LOG.debug('Checking if principal ' + service_principal + ' has hosts')
        # TODO(alee) Fill in implementation here
        return True

    def host_has_services(self, service_host):
        LOG.debug('Checking if host ' + service_host + ' has services')
        # TODO(alee) Fill in implementation here
        return True

    def delete_service(self, principal, batch=True):
        LOG.debug('Deleting service: ' + principal)
        params = [principal]
        service_args = {'force': True}
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
