#!/bin/bash


FEDORA_VERSION=$1

if [ -z $FEDORA_VERSION ]; then FEDORA_VERSION=f45; fi

SUMMARY_FILE="repoclosure-summary.txt"

dnf clean metadata

FEDORA_REPO="${FEDORA_VERSION}-build"

if [ $FEDORA_VERSION == 'f46' ]; then
    FEDORA_REPO=rawhide
fi

LC_ALL=C dnf repoclosure \
    --refresh \
    --forcearch=riscv64 \
    --nogpgcheck \
    --repofrompath="fedora-riscv,https://riscv-koji.fedoraproject.org/repos/${FEDORA_REPO}/latest/riscv64/" \
    --repo=fedora-riscv \
    --arch=riscv64 \
    >repoclosure.log 2>"$SUMMARY_FILE"

grep -o 'unresolved dependencies ([0-9]*) across [0-9]* packages' "$SUMMARY_FILE" > /dev/null
if [ $? -eq 0 ]; then
    echo "Repoclosure done."
    grep -o 'unresolved dependencies ([0-9]*) across [0-9]* packages' "$SUMMARY_FILE"
    cat "$SUMMARY_FILE"
else
    echo "Warning: could not parse summary from stderr"
    cat "$SUMMARY_FILE"
fi

EXTRA_ARGS=""
if [ "$2" = "-f" ]; then EXTRA_ARGS="-f"; fi

python3 parse-repoclose-reports.py $EXTRA_ARGS -t "$FEDORA_VERSION"

echo " "
