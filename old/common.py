import koji
import json
import re
import rpm
import sqlite3


def jprint(sth):
	print( json.dumps(sth, indent=4) )

def extract_tag(release):
	return release.split('.')[-1].replace('c','')

class hrw_koji_helper:

	def __init__(self, server):
		self.session = koji.ClientSession(server + '/kojihub')
		self.conn = sqlite3.connect('cache.db')
		self.cur  = self.conn.cursor()

	def add_build_to_db(self, build, buildtask, errorlog):
		tag = extract_tag(build['release'])

		self.cur.execute("""
					INSERT INTO nvrs
					(build_id, package_name, nvr, arch, tag, owner_name, task_id, creation_time, epoch, version, release, state, rootlog)
					VALUES
					(?,        ?,            ?,   ?,    ?,   ?,          ?,       ?,             ?,     ?,       ?,       ?,     ?)
					""",
					(
						build['build_id'], build['package_name'], build['nvr'], buildtask['arch'], tag, buildtask['owner_name'],
						buildtask['id'], buildtask['create_time'], build['epoch'], build['version'],
						build['release'], buildtask['state'], errorlog
					)
				)
		self.conn.commit()

	def check_for_newer_build(self, package_name, version, release):
		"""
		check was there successful build after the failed one
		"""
		try:
			package_builds = self.session.listTagged(extract_tag(release), package=package_name, latest=True)

			for package in package_builds:
				if 1 == rpm.labelCompare(('1', package['version'], package['release']), ('1', version, release)):
					return True
		except koji.GenericError:
			pass	# no idea why koji fails for listTagged() on packages which never built

		return False

	def handle_build(self, build):
		if not self.check_for_newer_build(build['package_name'], build['version'], build['release']):

			buildarch_tasks = self.session.listTasks(opts={'parent':build['task_id'], 'method':'buildArch'})

			for buildtask in buildarch_tasks:

				exists_already = self.cur.execute("SELECT nvr FROM nvrs WHERE task_id = ?", [buildtask['id']]).fetchall()

				if 0 == len(exists_already):
					errorlog = ''

					if 'root.log' in self.session.listTaskOutput( buildtask['id']):
						rootlog = self.session.downloadTaskOutput(buildtask['id'], 'root.log')

						if 'Requires:' in rootlog:

							start = rootlog.find('Error:')
							errorlog = re.sub("DEBUG util.py:...:", "", rootlog[start:rootlog.find('Child return code was', start)])


					add_build_to_db(build, buildtask, errorlog)

	def get_list_of_failed_builds(self, limit):
		return self.session.listBuilds(state=koji.BUILD_STATES['FAILED'], queryOpts={'limit':limit, 'order':'-build_id'})
