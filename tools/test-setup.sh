#!/bin/bash -xe

NOVA_PASSWORD=${SERVICE_PASSWORD:-secretservice}

pwd

sudo python /home/zuu/src/git.openstack.org/openstack/novajoin/setup.py install

source /opt/stack/devstack/openrc admin admin

sudo -E novajoin-install --debug --principal admin --password password \
    --user stack --nova-password $NOVA_PASSWORD \
    --keystone-auth-url http://127.0.0.1/identity
