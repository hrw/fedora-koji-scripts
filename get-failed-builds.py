#!/bin/python

"""
Simple script to list FTFBS packages in Koji

Author: Marcin Juszkiewicz <mjuszkiewicz@redhat.com>
License: GPLv2+
"""

import argparse
import koji
import rpm

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


def list_ftbfs(server, limit):

	session = koji.ClientSession(server + '/kojihub')

	builds = session.listBuilds(state=koji.BUILD_STATES['FAILED'], queryOpts={'limit':limit, 'order':'-build_id'})

	failed_packages = {}

	for build in builds:
		# check for newer
		tag=build['release'].split('.')[-1].replace('c','')
		current_package = "{package_name}-{tag}".format(package_name=build['package_name'], tag=tag)

		if not failed_packages.get(current_package, False):
			failed_packages[current_package] = 1
	#        print("Checking package: %s" % (build['nvr']))
			newer_exists = False
			try:
				package_builds = session.listTagged(tag, package=build['package_name'], latest=True)

				for package in package_builds:
	#                print("\tfound version %s" % package['nvr'])
					if 1 == rpm.labelCompare(('1', package['version'], package['release']), ('1', build['version'], build['release'])):
						newer_exists = True
			except Exception:
				pass	# no idea why koji fails for listTagged() on packages which never built

			if not newer_exists:
				task1 = session.listTasks(opts={'parent':build['task_id']})
				print("Failed package: %s %s/koji/taskinfo?taskID=%d" % (build['nvr'], server, task1[0]['id']))


try:
	(server, limit) = parse_args()
	list_ftbfs(server, limit)

except KeyboardInterrupt:
	print("\n")
	exit
