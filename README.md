novajoin Package
==================

This Python package provides a hook in the OpenStack nova compute
service to manage host instantiation in an IPA server.


Build
=====

In this directory, run:

  python setup.py build


Installation
============

In this directory, run:

  python setup.py install


Configuration
=============

Run novajoin-install to install and configure the hooks on a
pre-installed nova server.

Pre-requisites
--------------

You will need the IPA admin password, or an account that can
add privileges, permissions, roles and can retrieve keytabs.

You will need to provide Openstack credentails in the environment
so that the glance metadata upload can occur.

This will:

- copy the cloud-init and enrollment script to /etc/nova
- obtain a keytab to be used to authenticate against IPA when
  doing host management
- call out to a script to create the requisite permissions and
  role in IPA
- add the IPA metadata to the glance metadata service

The nova compute service will need to be manually restarted.

The installer takes the following options:

--hostname: use this value as the FQDN of the server.
--user: user that the nova service runs as. This is needed to
        set filesystem permissions
--principal: the user used to configure IPA integration: create permissions,
             get the keytab, etc. Default is the IPA admin account.
--password: the password for the principal. If this is not set the the
            password is obtained interactively
--password-file: the file containing the password for the principal.

Hook Configuration
==================

The hook is configured in /etc/nova/ipaclient.conf in the DEFAULT
section.  It provides the following options:

url: The JSON RPC URL to an IPA server, e.g. https://ipa.host.domain/ipa/json
keytab: The Kerberos keytab containing the credentails for the user
        nova will use to manage hosts. The default is /etc/krb5.keytab.
service_name: The service name of the JSON RPC handler. This is normally
        HTTP@<ipa master>
domain: If dhcp_domain is not set in nova.conf then this value is used
        as the default domain for IPA hosts.
connect_retries: The number of times to attempt to contact the IPA
        server before failing.
project_subdomain: Use the project the instance is created in as the
        subddomain for the fully-qualified domain name. For example if
        the project is admin and the domain is example.com and the
        instance name is test the FQDN will be test.admin.example.com
normalize_project: A project name can contain values not allowed as a
        DNS label. This will convert invalid values to a dash (-)
        dropping leading and trailing dashes.
inject_files: Files to inject into the VM.


Origin
======

This builds on the work of Rich Megginson and Nathan Kinder. Rich
did the initial implementation visible at
https://github.com/richm/rdo-vm-factory/blob/master/rdo-ipa-nova


Copyright and License
=====================

Copyright 2016 Red Hat, Inc.

   Licensed under the Apache License, Version 2.0 (the "License"); you may
   not use this file except in compliance with the License. You may obtain
   a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
   License for the specific language governing permissions and limitations
   under the License.
