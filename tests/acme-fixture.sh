#!/usr/bin/env bash
# CI ONLY: deterministic certificate installation adapter; never contacts a CA.
set -euo pipefail
mode= key= cert= reload=
while (($#)); do
    case $1 in
        --upgrade|--install-cert|--remove) mode=$1; shift;;
        --key-file) key=$2; shift 2;;
        --fullchain-file) cert=$2; shift 2;;
        --reloadcmd) reload=$2; shift 2;;
        --home|-d) shift 2;;
        --auto-upgrade|--ecc) shift;;
        *) printf 'Unexpected fixture argument: %s\n' "$1" >&2; exit 1;;
    esac
done
case $mode in
    --upgrade|--remove) exit 0;;
    --install-cert)
        install -m 0600 /etc/xray-cert/acme/ci.example.test_ecc/key.pem "$key"
        install -m 0600 /etc/xray-cert/acme/ci.example.test_ecc/fullchain.cer "$cert"
        bash -c "$reload";;
    *) exit 1;;
esac
