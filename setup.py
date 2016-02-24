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

    description='Nova hooks to enroll IPA clients',

    author='Rob Crittenden',
    author_email='rcritten@redhat.com',

    url='https://github.com/rcritten/rdo-vm-factory.git',

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

    entry_points={
        'nova.hooks': [
            'build_instance = novajoin.hooks:IPABuildInstanceHook',
            'delete_instance = novajoin.hooks:IPADeleteInstanceHook',
            'instance_network_info = novajoin.hooks:IPANetworkInfoHook',
        ],
    },

    data_files=[('/usr/share/novajoin', ['files/cloud-config.json',
                                         'files/ipaclient.conf.template',
                                         'files/setup-ipa-client.sh',
                                         'files/freeipa.json',
                                         ],),
                ('/usr/sbin', ['scripts/novajoin-install'],),
                ],

    zip_safe=False,
)
