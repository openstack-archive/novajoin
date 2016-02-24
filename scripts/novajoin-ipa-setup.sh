#!/bin/bash

ipa privilege-add 'Nova Host Management' --desc='Nova Host Management'

ipa permission-add 'modify host password' --permissions='write' --type='host' --attrs='userpassword'
ipa permission-add 'write host certificate' --permissions='write' --type='host' --attrs='usercertificate'
ipa permission-add 'modify host userclass' --permissions='write' --type='host' --attrs='userclass'

ipa privilege-add-permission 'Nova Host Management' \
 --permissions='System: add hosts' \
 --permissions='System: remove hosts' \
 --permissions='modify host password' \
 --permissions='modify host userclass' \
 --permissions='modify hosts' \
 --permissions='System: revoke certificate' \
 --permissions='System: manage host keytab' \
 --permissions='System: write host certificate' \
 --permissions='System: retrieve certificates from the ca' \
 --permissions='System: modify services' \
 --permissions='System: manage service keytab' \
 --permissions='System: read dns entries' \
 --permissions='System: remove dns entries' \
 --permissions='System: add dns entries' \
 --permissions='System: update dns entries' 

ipa role-add 'Nova Host Manager' --desc='Nova host management'

ipa role-add-privilege 'Nova Host Manager' --privilege='Nova Host Management'

ipa role-add-member 'Nova Host Manager' --services=nova/`hostname`
