#!/bin/python

"""
Simple script to list FTFBS packages in Koji

Author: Marcin Juszkiewicz <mjuszkiewicz@redhat.com>
License: GPLv2+
"""

import argparse
import koji
import rpm



parser = argparse.ArgumentParser(description="Query Koji for FTBFS list")
parser.add_argument("-a", "--arch", dest="arch",
					help="specify architecture to use (defaults to primary koji)")
parser.add_argument("-l", "--limit", default=50, dest="limit", type=int,
					help="specify an amount of packages to fetch (may display less due to repeats)")
options = parser.parse_args()

if options.arch == 'aoptions.arch64':
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

session = koji.ClientSession(server + '/kojihub')

builds = session.listBuilds(state=koji.BUILD_STATES['FAILED'], queryOpts={'limit':options.limit, 'order':'-build_id'})

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

"""
Build:
{
    "build_id": 260598, 
    "owner_name": "pbrobinson", 
    "package_name": "kpilot", 
    "task_id": 2878095, 
    "creation_event_id": 3654419, 
    "creation_time": "2015-02-12 13:36:16.739522", 
    "epoch": null, 
    "nvr": "kpilot-5.3.0-15.fc22", 
    "name": "kpilot", 
    "completion_time": "2015-02-12 13:39:08.39668", 
    "state": 3, 
    "version": "5.3.0", 
    "volume_name": "DEFAULT", 
    "release": "15.fc22", 
    "creation_ts": 1423748176.73952, 
    "completion_ts": 1423748348.39668, 
    "package_id": 3628, 
    "volume_id": 0, 
    "owner_id": 187
}

listTasks:
[
    {
        "weight": 1.92209178312, 
        "awaited": false, 
        "completion_time": "2015-02-12 13:38:53.285377", 
        "create_time": "2015-02-12 13:36:16.826952", 
        "result": "<?xml version='1.0'?>\n<methodResponse>\n<fault>\n<value><struct>\n<member>\n<name>faultCode</name>\n<value><int>1005</int></value>\n</member>\n<member>\n<name>faultString</name>\n<value><string>error building package (arch aarch64), mock exited with status 30; see root.log for more information</string></value>\n</member>\n</struct></value>\n</fault>\n</methodResponse>\n", 
        "owner": 187, 
        "id": 2878096, 
        "state": 5, 
        "label": "aarch64", 
        "priority": 24, 
        "waiting": null, 
        "completion_ts": 1423748333.28538, 
        "method": "buildArch", 
        "owner_name": "pbrobinson", 
        "parent": 2878095, 
        "start_time": "2015-02-12 13:36:24.031515", 
        "start_ts": 1423748184.03151, 
        "create_ts": 1423748176.82695, 
        "host_id": 307, 
        "arch": "aarch64", 
        "request": "<?xml version='1.0'?>\n<methodCall>\n<methodName>buildArch</methodName>\n<params>\n<param>\n<value><string>koji-shadow/1423748139.5371881.cjVRGwXx/kpilot-5.3.0-15.fc22.src.rpm</string></value>\n</param>\n<param>\n<value><int>171</int></value>\n</param>\n<param>\n<value><string>aarch64</string></value>\n</param>\n<param>\n<value><boolean>1</boolean></value>\n</param>\n<param>\n<value><struct>\n<member>\n<name>repo_id</name>\n<value><int>521708</int></value>\n</member>\n</struct></value>\n</param>\n</params>\n</methodCall>\n", 
        "channel_id": 1, 
        "owner_type": 0
    }
]

getTaskInfo:
{
    "weight": 1.92209178312, 
    "parent": 2878095, 
    "completion_time": "2015-02-12 13:38:53.285377", 
    "start_time": "2015-02-12 13:36:24.031515", 
    "start_ts": 1423748184.03151, 
    "state": 5, 
    "awaited": false, 
    "label": "aarch64", 
    "priority": 24, 
    "channel_id": 1, 
    "waiting": null, 
    "create_time": "2015-02-12 13:36:16.826952", 
    "id": 2878096, 
    "create_ts": 1423748176.82695, 
    "owner": 187, 
    "host_id": 307, 
    "completion_ts": 1423748333.28538, 
    "arch": "aarch64", 
    "method": "buildArch"
}
"""
