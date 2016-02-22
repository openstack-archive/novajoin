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
import base64
import six

from oslo_config import cfg
from oslo_config import types
from oslo_log import log as logging
from oslo_serialization import jsonutils as json

from nova.i18n import _
from nova.i18n import _LE
from nova.i18n import _LI
from nova.i18n import _LW

NOVACONF = cfg.CONF
CONF = cfg.ConfigOpts()

CONF.register_opts([
    cfg.StrOpt('url', default=None,
               help='IPA JSON RPC URL (e.g. '
                    'https://ipa.host.domain/ipa/json)'),
    cfg.StrOpt('keytab', default='/etc/krb5.keytab',
               help='Kerberos client keytab file'),
    cfg.StrOpt('service_name', default=None,
               help='HTTP IPA Kerberos service name '
                    '(e.g. HTTP@ipa.host.domain)'),
    cfg.StrOpt('cacert', default='/etc/ipa/ca.crt',
               help='CA certificate for use with https to IPA'),
    cfg.StrOpt('domain', default='test',
               help='Domain for new hosts'),
    cfg.IntOpt('connect_retries', default=1,
               help='How many times to attempt to retry '
               'the connection to IPA before giving up'),
    cfg.MultiOpt('inject_files', item_type=types.String(), default=[],
                 help='Files to inject into the new VM. '
                      'Specify as /path/to/file/on/host '
                      '[/path/to/file/in/vm/if/different]')
])

CONF(['--config-file', '/etc/nova/ipaclient.conf'])

LOG = logging.getLogger(__name__)


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
    }
}


def getvmdomainname():
    rv = NOVACONF.dhcp_domain or CONF.domain
    LOG.debug("getvmdomainname rv = " + rv)
    return rv


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
            LOG.warn(_LW('No IPA client kerberos keytab file given'))

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
            LOG.error(_LE("caught kerberos exception %r") % e)
            raise IPAAuthError(str(e))
        try:
            kerberos.authGSSClientStep(vc, "")
        except kerberos.GSSError as e:
            LOG.error(_LE("caught kerberos exception %r") % e)
            raise IPAAuthError(str(e))
        self.token = kerberos.authGSSClientResponse(vc)


class IPANovaHookBase(object):

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
            # sigh. Fix me again. Do I need to call update-ca-certificates?
            cls.session.verify = False
        if not cls.inject_files:
            for fn in CONF.inject_files:
                hostvm = fn.split(' ')
                hostfile = hostvm[0]
                if len(hostvm) > 1:
                    vmfile = hostvm[1]
                else:
                    vmfile = hostfile
                with file(hostfile, 'r') as f:
                    cls.inject_files.append([vmfile,
                                             base64.b64encode(f.read())])

    def __init__(self):
        IPANovaHookBase.start()
        self.session = IPANovaHookBase.session
        self.ntries = CONF.connect_retries
        self.inject_files = IPANovaHookBase.inject_files

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
                    LOG.error(_LE("Error: could not authenticate to IPA - "
                                  "please check for correct keytab file"))
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


class IPABuildInstanceHook(IPANovaHookBase):

    def _get_metadata(self, metadata, name, default_value=None):
        """
        Try to get metadata values first from the instance properties
        then the glance-provided metadata.

        Returns the value if found or a default value if specified.
        """
        image = metadata.get('image', {})
        properties = image.get('properties', {})
        instance_type = image.get('instance_type', {})
        LOG.debug("instance_type: %s", instance_type)
        instance_properties = metadata.get('instance_properties', {})
        instance_metadata = instance_properties.get('metadata', {})

        if name in instance_metadata:
            return instance_metadata.get(name)
        elif name in properties:
            return properties.get(name)
        elif name in instance_type:
            return str(instance_type.get(name))
        return default_value

    def pre(self, *args, **kwargs):
        """
        The positional arguments seem to break down into:
             0 - ContextManager
             1 - Context
             2 - instance
             3 - image
             4 - request_spec
             5 - filter_properties
             6 - admin_password
             7 - injected_files
             8 - requested_networks
             9 - security_groups
            10 - block_device_mapping
            11 - node
            12 - limits
        """
        LOG.debug('In IPABuildInstanceHook.pre: args [%s] kwargs [%s]',
                  pprint.pformat(args), pprint.pformat(kwargs))

        # the injected_files parameter array values are:
        #   ('filename', 'base64 encoded contents')
        ipaotp = uuid.uuid4().hex
        ipainject = ('/tmp/ipaotp', base64.b64encode(ipaotp))
        args[7].extend(self.inject_files)
        args[7].append(ipainject)

        # call ipa host add to add the new host
        inst = args[2]
        ipareq = {'method': 'host_add', 'id': 0}
        hostname = '%s.%s' % (inst.hostname, getvmdomainname())
        params = [hostname]
        userclass = self._get_metadata(args[4], 'ipa_userclass', '')
        location = self._get_metadata(args[4], 'ipa_host_location', '')
        osdistro = self._get_metadata(args[4], 'os_distro', '')
        osver = self._get_metadata(args[4], 'os_version', None)
        platform = self._get_metadata(args[4], 'extra_specs', None)
        hostargs = {
            'description': 'IPA host for %s' % inst.display_description,
            'userpassword': ipaotp,
            'force': True  # we don't have an ip addr ye so
                           # use force to add anyway
        }
        if userclass:
            hostargs['userclass'] = userclass
        if osdistro or osver:
            hostargs['nsosversion'] = '%s %s' % (osdistro, osver)
            hostargs['nsosversion'] = hostargs['nsosversion'].strip()
        if location:
            hostargs['nshostlocation'] = location
        if platform:
            hostargs['nshardwareplatform'] = platform
        ipareq['params'] = [params, hostargs]
        self._call_and_handle_error(ipareq)


class IPADeleteInstanceHook(IPANovaHookBase):

    def pre(self, *args, **kwargs):
        LOG.debug('In IPADeleteInstanceHook.pre: args [%s] kwargs [%s]',
                  pprint.pformat(args), pprint.pformat(kwargs))
        inst = args[2]
        # call ipa host delete to remove the host
        ipareq = {'method': 'host_del', 'id': 0}
        hostname = '%s.%s' % (inst.hostname, getvmdomainname())
        params = [hostname]
        args = {
            'updatedns': True,
        }
        ipareq['params'] = [params, args]
        self._call_and_handle_error(ipareq)


class IPANetworkInfoHook(IPANovaHookBase):

    def post(self, *args, **kwargs):
        LOG.debug('In IPANetworkInfoHook.post: args [%s] kwargs [%s]',
                  pprint.pformat(args), pprint.pformat(kwargs))
        if 'nw_info' not in kwargs:
            return
        inst = args[3]
        for fip in kwargs['nw_info'].floating_ips():
            LOG.debug("IPANetworkInfoHook.post fip is [%s] [%s]",
                      fip, pprint.pformat(fip.__dict__))
            ipareq = {'method': 'dnsrecord_add', 'id': 0}
            params = [{"__dns_name__": getvmdomainname() + "."},
                      {"__dns_name__": inst.hostname}]
            args = {'a_part_ip_address': fip['address']}
            ipareq['params'] = [params, args]
            self._call_and_handle_error(ipareq)
