novajoin Package
==================

This Python package provides a dynamic vendordata plugin for the OpenStack
nova metadata service to manage host instantiation in an IPA server.

It consists of two services:

    - REST service
    - notification listener

The REST service will respond to dynamic requests from the nova metadata
server. This is used to add hosts into IPA.

The notification listener will handle instance delete requests and remove
the appropriate host from IPA as well as floating IP associate and
disassociate requests and update IPA DNS.

Build
=====

In this directory, run::

  python setup.py build


Installation
============

In this directory, run::

  python setup.py install


Package Requirements
====================

Beyond those packages normally installed by Openstack, these are also
required::

    {free}ipa-python

These may be pip-installable but as of March 2017 it has only been
extensively tested with real packages installed on RHEL/CentOS/Fedora.


Configuration
=============

There are two installation scenarios:

1. Triple-O Quickstart (Pike and above)

This is primary use-case to date. In this scenario the heat and
puppet code manages most of the configuration. There is even some
code to configure everything if you're willing to provide the
IPA admin password.

If not you can pre-create the entries needed by running this on
any machine that has access to your IPA server. It can be
run from a git clone of novajoin by setting
PYTHONPATH=/path/to/novajoin if you'd like::

    novajoin-ipa-setup --precreate  --principal admin --password password \
        --server ipa.example.com --realm EXAMPLE.COM --domain example.com \
        --hostname undercloud.example.com

The machine executing this script does NOT need to be enrolled as
an IPA client.

The output from the script is the OTP you'll set in undercloud_ipa_otp
in the yaml configuration for OOOQ.

The idea here is that the undercloud node is pre-created and an OTP
generated. The OOOQ script knows how to enroll hosts and will pass this
OTP onto ipa-client-install, retrieve the keytab for the novajoin
service principal and configure and start the services. In OOOQ this is
done by setting enable_tls_everywhere to true and and prepare_novajoin
to false.

novajoin-ipa-setup will configure the project name to be service. This
may not match your installation. Adjust as needed.

2. Manual setup

It is also possible to setup novajoin within an existing OpenStack
deployment (I've done this in devstack, for example).

The machine running the novajoin service needs to be enrolled
as an IPA client.

Run novajoin-install to install and configure the plugin on a
pre-installed nova server.

nova currently needs to be manually configured to enable the
novajoin REST service and enable notifications in
**/etc/nova/nova.conf**::

    [api]
    vendordata_providers = StaticJSON, DynamicJSON
    vendordata_dynamic_targets = 'join@http://127.0.0.1:9090/v1/'
    vendordata_dynamic_connect_timeout = 5
    vendordata_dynamic_read_timeout = 30
    vendordata_jsonfile_path = /etc/novajoin/cloud-config-novajoin.json

    [oslo_messaging_notifications]
    notification_driver = messaging
    notification_topic = notifications,novajoin_notifications
    notify_on_state_change = vm_state

Novajoin enables keystone authentication by default, as seen in
**/etc/novajoin/join-api-paste.ini**. So credentials need to be set for
nova to be able to communicate with novajoin. This we can set in the
``[vendordata_dynamic_auth]`` section of **/etc/nova/nova.conf**::

    [vendordata_dynamic_auth]
    #
    # Options within this group control the authentication of the vendordata
    # subsystem of the metadata API server (and config drive) with external
    # systems.

    auth_type = password
    password = < service user password  >
    username = < service user >
    project_name = < service project >
    user_domain_name = < service user domain >
    project_domain_name = < service project domain >
    os_region_name = < region >

It is possible to just use the nova credentials here; or create a user just for
this. So choose depending on your requirements.

Pre-requisites
--------------

Cloud-init 0.7.6+ is required to retrieve dynamic metadata when
config_drive is True. 0.7.9 does not seem to work with OOOQ.

You will need the IPA admin password, or an account that can
add privileges, permissions, roles and can retrieve keytabs.

You will need to provide Openstack credentails in the environment
so that the glance metadata upload can occur.

This will:

- copy the cloud-init and enrollment script to **/etc/novajoin**
- obtain a keytab to be used to authenticate against IPA when
  doing host management
- call out to a script to create the requisite permissions and
  role in IPA
- add the IPA metadata to the glance metadata service

The nova-api service will need to be manually restarted.

The installer takes the following options::

    --hostname: use this value as the FQDN of the server.
    --user: user that the nova service runs as. This is needed to
            set filesystem permissions
    --principal: the user used to configure IPA integration: create permissions,
                 get the keytab, etc. Default is the IPA admin account.
    --password: the password for the principal. If this is not set the the
                password is obtained interactively
    --password-file: the file containing the password for the principal rather
                     than passing it interactively or via the command-line


Metadata REST Service Configuration
===================================

The REST service is configured in **/etc/novajoin/join.conf** in the DEFAULT
section.  It provides the following options:

- join_listen_port: The TCP port to listen on. Defaults to 9090.
- api_paste_config: The paste configuration file to use.
- debug: Enable additional debugging output. Default is False.
- auth_strategy: The authentication strategy to use
- url: The JSON RPC URL to an IPA server, e.g. https://ipa.host.domain/ipa/json
- keytab: The Kerberos keytab containing the credentails for the user
          nova will use to manage hosts. The default is **/etc/krb5.keytab**.
- domain: The domain to associate with IPA hosts.
- connect_retries: The number of times to attempt to contact the IPA
          server before failing.
- project_subdomain: Use the project the instance is created in as the
          subddomain for the fully-qualified domain name. For example if
          the project is admin and the domain is example.com and the
          instance name is test the FQDN will be test.admin.example.com
- normalize_project: A project name can contain values not allowed as a
          DNS label. This will convert invalid values to a dash (-)
          dropping leading and trailing dashes.

One must also configure the authtoken middleware in **/etc/novajoin/join.conf** as
specified in the `Keystone middleware documentation`_.

.. _`Keystone middleware documentation`: https://docs.openstack.org/developer/keystonemiddleware/middlewarearchitecture.html#configuration

Notification listener Configuration
===================================

The only special configuration needed here is to configure nova to
send notifications to the novajoin topic in /etc/nova/nova.conf:

    notification_topic = notifications,novajoin_notifications

If you simply use notifications and ceilometer is running then the
notifications will be roughly split between the two services in a
round-robin format.

Usage
=====

This demonstrates how novajoin works once the services are installed,
configured and running:

Sample usage from the command-line::

    $ openstack server create --flavor m1.tiny --image cirros-0.3.4-x86_64-uec test --property ipa_enroll=True
    $ ssh <IP>
    $ curl http://169.254.169.254/openstack/2016-10-06/vendor_data2.json
    $ id admin
    uid=#########(admin) gid=#########(admins) groups=#########(admins)

The curl output will include a "join" element in the returned dict.
This will contain a hostname and ipaotp value. These are used for
enrollment with ipa-client-install via::

    # ipa-client-install -U -w <ipaotp> --hostname <hostname>

The provided cloud-init script should do all this for you, automatically
fetching the OTP and enrolling the client.

This id command confirms that enrollment was successful.

Logging
=======

The REST novajoin-server service logs by default to
/var/log/novajoin/novajoin-server.log

The notification listener service novajoin-notify logs by default to
/var/log/novajoin/novajoin-notify.log

A logrotate script for this is::

    /var/log/novajoin/*log {
        weekly
        rotate 14
        size 10M
        missingok
        compress
    }


Design
======

There are quite a few moving parts in novajoin so here is a high-level
overview of how it fits together.

The OpenStack Newton release added a new type of metadata to the nova
metadata service: dynamic metadata. This is metadata generated on-the-fly
and not stored within nova (for example for security reasons).

For the case of enrolling a client into IPA using a One-Time Password (OTP)
the password needs to be generated when the IPA host created and then
somehow passed to the instance. This is done using dynamic metadata.

The basic sequence of events is:

1. Instance creation is requested to nova, either via Horizon or the
   command-line.
2. nova starts the instance and pushes down a cloud-init script provided
   by novajoin.
3. cloud-init executes the provided script which installs the ipa-client
   package, then executes a script which retrieves the metadata from the
   nova metadata service[*]. This looks like:
   % curl http://169.254.169.254/openstack/2016-10-06/vendor_data2.json
4. This request invokes the novajoin dynamic metadata service provided
   by the novajoin package. This is registered in **/etc/nova/nova.conf**.
5. If the instance was created with the property ipa_enroll=True or
   the host image has this property set then a host in IPA is created and
   an OTP generated. The OTP and generated FQDN are returned to nova as a
   python dictionary. The data is returned from the metadata service as
   JSON. If the glance image has os_distro and os_version set in its
   metadata then this will be reflected in the IPA host.
6. The script provided to cloud-init pulls out the OTP and FQDN and calls
   ipa-client-install

This results in an IPA-enrolled client with no user interaction.

The novajoin-notify service waits for notifications from nova that an
instance deletion has been completed. If that instance or image has the
property ipa_enroll=True then the host is removed from IPA.

.. note::
   In the case of config drive the metadata is retrieved and attached
   to the instance at boot time. cloud-init detects the config drive and
   reads its metadata from there.


Packstack
=========

Some people have had difficulties deploying novajoin within Packstack.
Here is how I did it on RHEL 7.4 using RHOSP 11 (Ocata).

Install packstack

    # packstack <your_favorite_options>

Configure host as an IPA client

    # ipa-client-install ...

Get the [keystone_authtoken] section from /etc/nova/nova.conf and call
the novajoin installer

    # novajoin-install --user nova --keystone-auth-url <auth-url> \
      --nova-password <password> --project services

Add the novajoin user and group

    # groupadd -r novajoin --gid 968
    # useradd -u 968 -r -g novajoin -G novajoin -d /var/lib/novajoin \
      -s /sbin/nologin -c "OpenStack novajoin Daemons" novajoin

Create the log directory

    # mkdir /var/log/novajoin
    # chown novajoin:novajoin /var/log/novajoin

Copy the systemd unit files

    # cp /usr/share/novajoin/*.service /usr/lib/systemd/system/
    # systemd daemon-reload

Restart the necessary services

    # systemctl restart openstack-nova-api novajoin-server novajoin-notify
Origin
======

This builds on the work of Rich Megginson and Nathan Kinder. Rich
did the initial hooks implementation visible at
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
