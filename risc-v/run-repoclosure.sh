#!/bin/bash


FEDORA_VERSION=$1

if [ -z "$FEDORA_VERSION" ]; then FEDORA_VERSION=f45; fi

# Per-tag log/summary files, so data for different tags does not get
# overwritten by the next run.
LOG_FILE="repoclosure-${FEDORA_VERSION}.log"
SUMMARY_FILE="repoclosure-${FEDORA_VERSION}-summary.txt"

dnf clean metadata

FEDORA_REPO="${FEDORA_VERSION}-build"

if [ "$FEDORA_VERSION" == 'f46' ]; then
    FEDORA_REPO=rawhide
fi

LC_ALL=C dnf repoclosure \
    --refresh \
    --forcearch=riscv64 \
    --nogpgcheck \
    --repofrompath="fedora-riscv,https://riscv-koji.fedoraproject.org/repos/${FEDORA_REPO}/latest/riscv64/" \
    --repo=fedora-riscv \
    --arch=riscv64 \
    >"$LOG_FILE" 2>"$SUMMARY_FILE"

grep -q 'unresolved dependencies ([0-9]*) across [0-9]* packages' "$SUMMARY_FILE"
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
