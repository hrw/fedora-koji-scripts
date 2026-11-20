#!/bin/sh

for arch in arm aarch64 ppc64 s390
do
	./get-failed-builds.py -a $arch -l 100
done

