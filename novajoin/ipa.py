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
            api.Command[command](*args, **kw)
        except (errors.CCacheError, errors.TicketExpired):
            LOG.debug("Refresh authentication")
            api.Backend.rpcclient.disconnect()
            self.__get_connection()
            api.Command[command](*args, **kw)

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
#            'description': 'IPA host for %s' % inst.display_description,
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

    def add_ip(self, hostname, floating_ip):
        """Add a floating IP to a given hostname."""
        LOG.debug('In add_ip')

        if not self._ipa_client_configured():
            LOG.debug('IPA is not configured')
            return

        params = [{"__dns_name__": CONF.domain + "."},
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
