#!/bin/sh

# get OTP
ii=60
while [ $ii -gt 0 ] ; do
    otp=`cat /tmp/ipaotp`
    if [ -n "$otp" ] ; then
        break
    fi
    sleep 1
    ii=`expr $ii - 1`
done

if [ -z "$otp" ] ; then
    echo Error: could not get IPA OTP after 60 seconds - exiting
    exit 1
fi

# Get the instance hostname out of the metadata
fqdn=`curl http://169.254.169.254/openstack/latest/meta_data.json 2>/dev/null| python -mjson.tool |grep '"hostname"' | awk '{ print $2 }' | sed 's/,//' | sed 's/"//g'`

rm -f /tmp/ipaotp
# run ipa-client-install
ipa-client-install -U -w $otp --hostname $fqdn
