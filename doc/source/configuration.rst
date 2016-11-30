Configuration
=============

novajoin is configured via /etc/nova/join.conf and consists of two
configuration sections, DEFAULT and service_credentials.

DEFAULT
-------

join_listen_port
~~~~~~~~~~~~~~~~
Port that the REST service listens on. Default 9999.

api_paste_config
~~~~~~~~~~~~~~~~
Pointer to the paste configuration.

debug
~~~~~
Enables additional debug logging.

keytab
~~~~~~
Kerberos keytab containing the IPA credentials used to add/delete hosts.

url
~~~
URL pointing to the IPA master JSON endpoint.

domain
~~~~~~
The domain name to add to instances names to create a FQDN.

connect_retries
~~~~~~~~~~~~~~~
The number of times to attempt to contact the IPA server before failing.

service_credentials
-------------------

auth_url
~~~~~~~~
URL of the keystone service.

auth_type
~~~~~~~~~
Keystone authentication method.

password
~~~~~~~~
Password of the novajoin service user.

username
~~~~~~~~
The service username.
