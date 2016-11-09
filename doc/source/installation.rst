Installing novajoin
===================
Installing novajoin is easy.

RHEL, CentOS, Fedora packages
-----------------------------
Required dependencies
~~~~~~~~~~~~~~~~~~~~~
::

    yum -y install {free}ipa-client

Development or integration testing dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
::

    yum -y install python-setuptools
    easy_install pip
    pip install tox

Ubuntu, Debian packages
-----------------------
Required dependencies
~~~~~~~~~~~~~~~~~~~~~
::

    apt-get -y install freeipa-client

Development or integration testing dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
::

    apt-get -y install python-pip
    pip install tox

Installing novajoin from trunk source
-------------------------------------
::

    pip install git+https://github.com/rcritten/novajoin


Configuration
-------------
The machine must first be configured as an IPA client. It is not
mandatory to be run on the nova controller but it is recommended.

The installer novajoin-install configures nova to use the novajoin service
as a dynamic metadata provider, configures the novajoin notification and
REST services and configures IPA to grant access to a role that allows
management of hosts.

There are four ways to provide authentication for the IPA integration:

1. kinit before running the script and use the --no-kinit option.
2. Set the Kerberos principal with --principal and pass the password
   on the command-line using --password.
3. Set the Kerberos principal with --principal and pass the password in
   a file using --password-file.
4. Let the installer prompt the user for the password.
