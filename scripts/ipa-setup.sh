#!/bin/bash

ipa privilege-add 'Nova Host Management' --desc='Nova Host Management'

ipa permission-add 'modify host password' --permissions='write' --type='host' --attrs='userpassword'
ipa permission-add 'write host certificate' --permissions='write' --type='host' --attrs='usercertificate'
ipa permission-add 'modify host userclass' --permissions='write' --type='host' --attrs='userclass'

ipa privilege-add-permission 'Nova Host Management' --permissions='add hosts' \
 --permissions='remove hosts' \
 --permissions='modify host password' \
 --permissions='modify host userclass' \
 --permissions='modify hosts' \
 --permissions='revoke certificate' \
 --permissions='manage host keytab' \
 --permissions='write host certificate' \
 --permissions='retrieve certificates from the ca' \
 --permissions='modify services' \
 --permissions='manage service keytab' \
 --permissions='read dns entries' \
 --permissions='remove dns entries' \
 --permissions='add dns entries' \
 --permissions='update dns entries' 

ipa role-add 'Nova Host Manager' --desc='Nova host management'

ipa role-add-privilege 'Nova Host Manager' --privilege='Nova Host Management'

ipa role-add-member 'Nova Host Manager' --services=nova/`hostname`
