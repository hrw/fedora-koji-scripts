#!/bin/python

"""
Simple script to list FTFBS packages in Koji

Author: Marcin Juszkiewicz <mjuszkiewicz@redhat.com>
License: GPLv2+
"""

import argparse
import koji
import json
import re
import rpm
import sqlite3


def jprint(sth):
	print( json.dumps(sth, indent=4) )

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

def get_list_of_failed_builds(server, limit):
	return session.listBuilds(state=koji.BUILD_STATES['FAILED'], queryOpts={'limit':limit, 'order':'-build_id'})

def add_build_to_db(build, tag, buildtask):
	cur.execute("""
				INSERT INTO nvrs
				(build_id, package_name, nvr, arch, tag, owner_name, task_id, creation_time, epoch, version, release, state)
				VALUES
				(?,        ?,            ?,   ?,    ?,   ?,          ?,       ?,             ?,     ?,       ?,       ?)
				""",
				(
					build['build_id'], build['package_name'], build['nvr'], buildtask['arch'], tag, buildtask['owner_name'],
					buildtask['id'], buildtask['create_time'], build['epoch'], build['version'],
					build['release'], buildtask['state']
				)
			   )
	conn.commit()

def list_ftbfs(server, limit):

	builds = get_list_of_failed_builds(server, limit)

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
			except koji.GenericError:
				pass	# no idea why koji fails for listTagged() on packages which never built

			if not newer_exists:
				buildarch_tasks = session.listTasks(opts={'parent':build['task_id']})

				for buildtask in buildarch_tasks:
					if buildtask['method'] == 'buildArch':
						add_build_to_db(build, tag, buildtask)

				package_failed_reason = ''

				errorlog = ''

#                if 'root.log' in session.listTaskOutput(buildarch_tasks[0]['id']):
#                    rootlog = session.downloadTaskOutput(buildarch_tasks[0]['id'], 'root.log')

#                    if 'Requires:' in rootlog:
#                        package_failed_reason = ' (missing build dependencies)'

#                        errorlog = re.sub("DEBUG util.py:...:", "", rootlog[rootlog.find('Error:'):rootlog.find('You could try')])

				print("Package failed%s: %s %s/koji/taskinfo?taskID=%d" % (package_failed_reason, build['nvr'], server, buildarch_tasks[0]['id']))

				if errorlog:
					print(errorlog)


try:
	(server, limit) = parse_args()

	session = koji.ClientSession(server + '/kojihub')

	conn = sqlite3.connect('cache.db')
	cur  = conn.cursor()

	list_ftbfs(server, limit)

except KeyboardInterrupt:
	print("\n")


conn.close();
