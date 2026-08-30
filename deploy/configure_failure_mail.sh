#!/bin/sh
set -eu

# Configure a credential-free, outbound-only mail transfer agent for failure
# alerts. The data container shares the host network and connects only to this
# loopback listener; Postfix then delivers directly to the recipient's MX.

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this script as root (for example, with sudo)." >&2
    exit 1
fi

mail_hostname="${GWAS_MAIL_HOSTNAME:-mail.gwasdiversitymonitor.com}"

printf '%s\n' \
    "postfix postfix/mailname string ${mail_hostname}" \
    "postfix postfix/main_mailer_type select Internet Site" \
    | debconf-set-selections

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y postfix

printf '%s\n' "${mail_hostname}" > /etc/mailname
postconf -e "myhostname = ${mail_hostname}"
postconf -e 'myorigin = $myhostname'
postconf -e 'mydestination = $myhostname, localhost.$mydomain, localhost'
postconf -e 'inet_interfaces = loopback-only'
postconf -e 'mynetworks = 127.0.0.0/8 [::1]/128'
postconf -e 'smtp_helo_name = $myhostname'
postconf -e 'smtp_tls_security_level = may'

postfix check
systemctl enable postfix
systemctl restart postfix

echo "Postfix is listening on loopback only as ${mail_hostname}."
echo "AWS port-25 quota removal and reverse DNS must also be configured."
