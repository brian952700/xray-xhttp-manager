#!/usr/bin/env bash
# CI ONLY: deterministic certificate installation adapter; never contacts a CA.
set -euo pipefail
mode= key= cert= reload= domain=
LOG=/tmp/xy-acme-fixture.log
while (($#)); do
    case $1 in
        --upgrade|--install-cert|--remove|--renew|--issue) mode=$1; shift;;
        --key-file) key=$2; shift 2;;
        --fullchain-file) cert=$2; shift 2;;
        --reloadcmd) reload=$2; shift 2;;
        --home) shift 2;;
        -d) domain=$2; shift 2;;
        --auto-upgrade|--ecc) shift;;
        *) printf 'Unexpected fixture argument: %s\n' "$1" >&2; exit 1;;
    esac
done
printf '%s %s\n' "$mode" "$domain" >> "$LOG"
case $mode in
    --upgrade|--remove|--renew|--issue) exit 0;;
    --install-cert)
        install -m 0600 /etc/xray-cert/acme/${domain}_ecc/key.pem "$key"
        install -m 0600 /etc/xray-cert/acme/${domain}_ecc/fullchain.cer "$cert"
        printf '%s\n' "$reload" > "/tmp/xy-reload-$domain"
        bash -c "$reload";;
    *) exit 1;;
esac
