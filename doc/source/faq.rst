FAQ
===
What is novajoin?
-----------------
_novajoin is nova vendordata plugin for the OpenStack nova metadata service
to manage host instantiation in an IPA server.

novajoin has two parts:

1. A REST service which handles adding new IPA hosts
2. A notification listener which handles removing hosts

The REST service will respond to dynamic requests from the nova metadata
server. This is used to add hosts into IPA.

The notification listener will handle instance delete requests and remove
the appropriate host from IPA as well as floating IP associate and
disassociate requests and update IPA DNS.

.. _novajoin: https://github.com/rcritten/novajoin

How does it work?
-----------------

The REST service waits for POST requests which includes the requested
instance name (hostname), instance_id, OpenStack image_id, project_id
and user metadata.

Only those requests which include the metadata ipa_enroll=True
will result in a host being added. The metadata can be specified in
the user metadata or in the associated image.

The image_id is used to return the image metadata from glance.

The domain name configured in join.conf is appended to the instance
name to generate the FQDN that will be used to enroll the host into
IPA.

The host is added along with a randomly generated One-Time Password (OTP)
and the hostname are returned. This is then passed into the instance either
via a config drive or by a metadata call from within the instance.

A cloud-init script is used to pass these values to ipa-client-install
to enroll the host.

The notification service listens on the AMQP notifications queue for
instance delete requests. When one is found the equivalent IPA host,
if any, will be removed.

Can novajoin be used outside the context of OpenStack?
----------------------------------------------------------------------------
Not likely. It is pretty tightly integrated with nova and glance.
