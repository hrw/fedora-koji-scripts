#!/bin/python

"""
Simple script to list FTFBS packages in Koji

Author: Marcin Juszkiewicz <mjuszkiewicz@redhat.com>
License: GPLv2+
"""

import argparse

from progress.bar import Bar
from common import *

def parse_args():
	parser = argparse.ArgumentParser(description="Query Koji for FTBFS list")
	parser.add_argument("-a", "--arch", dest="arch",
						help="specify architecture to use (defaults to primary koji)")
	parser.add_argument("-l", "--limit", default=50, dest="limit", type=int,
						help="specify an amount of packages to fetch (may display less due to repeats)")
	options = parser.parse_args()

	if options.arch == 'aarch64':
		options.arch = 'arm'
	elif options.arch == 'ppc64':
		options.arch = 'ppc'
	elif options.arch == 's390x':
		options.arch = 's390'
	elif options.arch in ['arm', 'i386', 'x86_64']:
		options.arch = ''

	if options.arch:
		options.arch += '.'
	else:
		options.arch = ''

	server = "http://" + str(options.arch) + "koji.fedoraproject.org/"

	return (server, options.limit)


def list_ftbfs(limit):

	builds = hrwkoji.get_list_of_failed_builds(limit)

	failed_packages = {}

	bar = Bar('Fetching', max=limit)

	for build in builds:
		current_package = "{package_name}-{tag}".format(package_name=build['package_name'], tag=extract_tag(build['release']))

		bar.next()
		if not failed_packages.get(current_package, False):
			failed_packages[current_package] = 1
			hrw_koji.handle_build(build)



try:
	(server, limit) = parse_args()

	hrw_koji = hrw_koji_helper(server)

	list_ftbfs(limit)

except KeyboardInterrupt:
	pass

print("\n")
