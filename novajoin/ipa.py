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
import pprint
import requests
import uuid
import kerberos
import six
import re

from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils as json


CONF = cfg.CONF

LOG = logging.getLogger(__name__)

dns_regex = re.compile('[^0-9a-zA-Z]+')


class IPABaseError(Exception):
    error_code = 500
    error_type = 'unknown_ipa_error'
    error_message = None
    errors = None

    def __init__(self, *args, **kwargs):
        self.errors = kwargs.pop('errors', None)
        self.object = kwargs.pop('object', None)

        super(IPABaseError, self).__init__(*args, **kwargs)

        if len(args) > 0 and isinstance(args[0], six.string_types):
            self.error_message = args[0]


class IPAAuthError(IPABaseError):
    error_type = 'authentication_error'


IPA_INVALID_DATA = 3009
IPA_NOT_FOUND = 4001
IPA_DUPLICATE = 4002
IPA_NO_DNS_RECORD = 4019
IPA_NO_CHANGES = 4202


class IPAUnknownError(IPABaseError):
    pass


class IPACommunicationFailure(IPABaseError):
    error_type = 'communication_failure'


class IPAInvalidData(IPABaseError):
    error_type = 'invalid_data'


class IPADuplicateEntry(IPABaseError):
    error_type = 'duplicate_entry'


ipaerror2exception = {
    IPA_INVALID_DATA: {
        'host': IPAInvalidData,
        'dnsrecord': IPAInvalidData
    },
    IPA_NO_CHANGES: {
        'host': None,
        'dnsrecord': None
    },
    IPA_NO_DNS_RECORD: {
        'host': None,  # ignore - means already added
    },
    IPA_DUPLICATE: {
        'host': IPADuplicateEntry,
        'dnsrecord': IPADuplicateEntry
    },
    IPA_NOT_FOUND: {
        'host': None,  # ignore - means tried to delete non-existent host
    }
}


class IPAAuth(requests.auth.AuthBase):
    def __init__(self, keytab, service):
        # store the kerberos credentials in memory rather than on disk
        os.environ['KRB5CCNAME'] = "MEMORY:" + str(uuid.uuid4())
        self.token = None
        self.keytab = keytab
        self.service = service
        if self.keytab:
            os.environ['KRB5_CLIENT_KTNAME'] = self.keytab
        else:
            LOG.warn('No IPA client kerberos keytab file given')

    def __call__(self, request):
        if not self.token:
            self.refresh_auth()
        request.headers['Authorization'] = 'negotiate ' + self.token
        return request

    def refresh_auth(self):
        flags = kerberos.GSS_C_MUTUAL_FLAG | kerberos.GSS_C_SEQUENCE_FLAG
        try:
            (unused, vc) = kerberos.authGSSClientInit(self.service,
                                                      gssflags=flags)
        except kerberos.GSSError as e:
            LOG.error("caught kerberos exception %r" % e)
            raise IPAAuthError(str(e))
        try:
            kerberos.authGSSClientStep(vc, "")
        except kerberos.GSSError as e:
            LOG.error("caught kerberos exception %r" % e)
            raise IPAAuthError(str(e))
        self.token = kerberos.authGSSClientResponse(vc)


class IPANovaJoinBase(object):

    session = None
    inject_files = []

    @classmethod
    def start(cls):
        if not cls.session:
            # set up session to share among all instances
            cls.session = requests.Session()
            cls.session.auth = IPAAuth(CONF.keytab, CONF.service_name)
            xtra_hdrs = {'Content-Type': 'application/json',
                         'Referer': CONF.url}
            cls.session.headers.update(xtra_hdrs)
            cls.session.verify = True

    def __init__(self):
        IPANovaJoinBase.start()
        self.session = IPANovaJoinBase.session
        self.ntries = CONF.connect_retries
        self.inject_files = IPANovaJoinBase.inject_files

    def _ipa_error_to_exception(self, resp, ipareq):
        exc = None
        if resp['error'] is None:
            return exc
        errcode = resp['error']['code']
        method = ipareq['method']
        methtype = method.split('_')[0]
        exclass = ipaerror2exception.get(errcode, {}).get(methtype,
                                                          IPAUnknownError)
        if exclass:
            LOG.debug("Error: ipa command [%s] returned error [%s]" %
                      (pprint.pformat(ipareq), pprint.pformat(resp)))
        elif errcode:  # not mapped
            LOG.debug("Ignoring IPA error code %d for command %s: %s" %
                      (errcode, method, pprint.pformat(resp)))
        return exclass

    def _call_and_handle_error(self, ipareq):
        need_reauth = False
        while True:
            status_code = 200
            try:
                if need_reauth:
                    self.session.auth.refresh_auth()
                rawresp = self.session.post(CONF.url,
                                            data=json.dumps(ipareq))
                status_code = rawresp.status_code
            except IPAAuthError:
                status_code = 401
            if status_code == 401:
                if self.ntries == 0:
                    # persistent inability to auth
                    LOG.error("Error: could not authenticate to IPA - "
                              "please check for correct keytab file")
                    # reset for next time
                    self.ntries = CONF.connect_retries
                    raise IPACommunicationFailure()
                else:
                    LOG.debug("Refresh authentication")
                    need_reauth = True
                    self.ntries -= 1
                    time.sleep(1)
            else:
                # successful - reset
                self.ntries = CONF.connect_retries
                break
        try:
            resp = json.loads(rawresp.text)
        except ValueError:
            # response was not json - some sort of error response
            LOG.debug("Error: unknown error from IPA [%s]" % rawresp.text)
            raise IPAUnknownError("unable to process response from IPA")
        # raise the appropriate exception, if error
        exclass = self._ipa_error_to_exception(resp, ipareq)
        if exclass:
            # could add additional info/message to exception here
            raise exclass()
        return resp

    def _ipa_client_configured(self):
        """
        Return boolean indicating whether this machine is enrolled
        in IPA. This is a rather weak detection method but better
        than nothing.
        """
        return os.path.exists('/etc/ipa/default.conf')


class IPAClient(IPANovaJoinBase):

    def add_host(self, hostname, ipaotp, metadata={}, image_metadata={}):
        """
        If requested in the metadata, add a host to IPA. The assumption
        is that hostname is already fully-qualified.
        """
        LOG.debug('In IPABuildInstance')

        if not self._ipa_client_configured():
            LOG.debug('IPA is not configured')
            return

        if metadata is None:
            metadata = {}
        if image_metadata is None:
            image_metadata = {}

        enroll = metadata.get('ipa_enroll', '')
        if enroll.lower() != 'true':
            LOG.debug('IPA enrollment not requested')
            return

        ipareq = {'method': 'host_add', 'id': 0}

        params = [hostname]
        hostclass = metadata.get('ipa_hostclass', '')
        location = metadata.get('ipa_host_location', '')
        osdistro = image_metadata.get('os_distro', '')
        osver = image_metadata.get('os_version', '')
#            'description': 'IPA host for %s' % inst.display_description,
        hostargs = {
            'description': 'IPA host for OpenStack',
            'userpassword': ipaotp,
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
        ipareq['params'] = [params, hostargs]
        self._call_and_handle_error(ipareq)

    def delete_host(self, hostname, metadata={}):
        """
        Delete a host from IPA and remove all related DNS entries.
        """
        LOG.debug('In IPADeleteInstance')

        if not self._ipa_client_configured():
            LOG.debug('IPA is not configured')
            return

        # TODO: lookup instance in nova to get metadata to see if
        #       the host was enrolled. For now assume yes.

        ipareq = {'method': 'host_del', 'id': 0}
        params = [hostname]
        args = {
            'updatedns': True,
        }
        ipareq['params'] = [params, args]
        self._call_and_handle_error(ipareq)

    def add_ip(self, hostname, floating_ip):
        """
        Add a floating IP to a given hostname.
        """
        LOG.debug('In add_ip')

        if not self._ipa_client_configured():
            LOG.debug('IPA is not configured')
            return

        ipareq = {'method': 'dnsrecord_add', 'id': 0}
        params = [{"__dns_name__": CONF.domain + "."},
                  {"__dns_name__": hostname}]
        args = {'a_part_ip_address': floating_ip}
        ipareq['params'] = [params, args]
        self._call_and_handle_error(ipareq)

    def remove_ip(self, hostname, floating_ip):
        """
        Remove a floating IP from a given hostname.
        """
        LOG.debug('In remove_ip')

        if not self._ipa_client_configured():
            LOG.debug('IPA is not configured')
            return

        LOG.debug('Current a no-op')
