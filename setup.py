#!/usr/bin/env python
#
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

from setuptools import setup

setup(
    name='novajoin',
    version='1.0.0',

    description='Nova integration to enroll IPA clients',

    author='Rob Crittenden',
    author_email='rcritten@redhat.com',

    url='https://github.com/rcritten/novajoin.git',

    classifiers=['Development Status :: 3 - Alpha',
                 'License :: OSI Approved :: Apache Software License',
                 'Programming Language :: Python',
                 'Programming Language :: Python :: 2',
                 'Programming Language :: Python :: 2.7',
                 'Programming Language :: Python :: 3',
                 'Programming Language :: Python :: 3.4',
                 'Intended Audience :: Developers',
                 'Environment :: Console',
                 ],

    license="Apache License, Version 2.0",

    platforms=['Any'],

    packages=['novajoin'],

    data_files=[('/usr/share/novajoin', ['files/cloud-config.json',
                                         'files/freeipa.json',
                                         'files/join.conf.template',
                                         ],),
                ('/etc/join', ['files/api-paste.ini'],),
                ('/usr/sbin', ['scripts/novajoin-notify'],),
                ('/usr/sbin', ['scripts/novajoin-server'],),
                ('/usr/sbin', ['scripts/novajoin-install'],),
                ('/usr/libexec', ['scripts/novajoin-ipa-setup.sh']),
                ('/usr/share/man/man1', ['man/novajoin-install.1',
                                         'man/novajoin-notify.1',
                                         'man/novajoin-server.1',
                                         ]),
                ],

    zip_safe=False,
)
