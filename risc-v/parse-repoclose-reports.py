#!/usr/bin/python3

'''
Command to get logs:

dnf repoclosure --refresh --forcearch=riscv64 --nogpgcheck --arch=riscv64 \
--repo=fedora-riscv \
--repofrompath=fedora-riscv,https://riscv-koji.fedoraproject.org/repos/f43-build/latest/riscv64/

we need lists of:

- packages not built since gods were walking on surface of Earth
- most common unresolved_deps
- whatever came out of it

Some might be removed, and thus needs removal on our end. Some of these require
new dependencies or something we cannot build anymore, and get stuck. It those
cases probably untagged them would be a better option. Some of these are in
general problem for Fedora.


Packages older than Fedora 41:

- calibre-3.29.0-1.fc29.riscv64
- cgit-1.2.1-3.fc31.riscv64
    FTBFS in F43/44, ignore

- deepin-system-monitor-5.0.0-5.fc33.2.riscv64
    dtk6 needs rebuild with qt6 6.10.1

- et-6.0.13-1.fc33.riscv64
    vendored vendored needs update - fuck it

- fastnetmon-1.2.1-16.20220528git420e7b8.fc40.riscv64
    needs gprbuild and more?

- gcc-nios2-linux-gnu-13.2.1-1.fc40.riscv64

- gstreamer1-plugins-fc-0.2-34.fc42.riscv64
    RETIRED

- mame-0.212-1.fc31.riscv64
- mame-tools-0.212-1.fc31.riscv64

- phosh-0.32.0-1.fc40.riscv64
    Needs upstream help? Or it is linker?

- python3-libkml-1.3.0-45.fc40.riscv64
- python3-pyunicorn-0.7.0~a1-5.20230730gitmaster.fc40.riscv64

- sourcextractor++-0.10-2.fc33.riscv64
- sourcextractor++-devel-0.10-2.fc33.riscv64
    onnxruntime-devel...

'''
import argparse
import re
import sys
from datetime import datetime, timezone
from pprint import pprint

import koji
import dnf
import yaml


parser = argparse.ArgumentParser()
parser.add_argument('-v', '--verbose', action='store_true')
parser.add_argument('-f', '--force', action='store_true',
                    help='force refresh of koji cache')
parser.add_argument('-t', '--tag', default='f45',
                    help='koji tag (default: f45)')
args = parser.parse_args()
verbose = args.verbose

FEDORA_KOJI = 'https://koji.fedoraproject.org/kojihub'
RISCV_KOJI = 'https://riscv-koji.fedoraproject.org/kojihub'

FEDORA_KOJI_WEB = 'https://koji.fedoraproject.org/koji'
RISCV_KOJI_WEB = 'https://riscv-koji.fedoraproject.org/koji'

KOJI_TAG = args.tag
KOJI_CACHE_FILE = f'koji-cache-{KOJI_TAG}.yml'
PKG_NAME_MAP_FILE = 'map-package-names.yml'
SUMMARY_FILE = 'repoclosure-summary.txt'

koji_cache = {}
pkg_name_map = {}


def load_koji_cache():
    global koji_cache
    try:
        with open(KOJI_CACHE_FILE) as f:
            koji_cache = yaml.safe_load(f) or {}
    except FileNotFoundError:
        koji_cache = {}


def save_koji_cache():
    with open(KOJI_CACHE_FILE, 'w') as f:
        yaml.dump(koji_cache, f, default_flow_style=False)


def load_pkg_name_map():
    global pkg_name_map
    try:
        with open(PKG_NAME_MAP_FILE) as f:
            pkg_name_map = yaml.safe_load(f) or {}
    except FileNotFoundError:
        pkg_name_map = {}


def save_pkg_name_map():
    with open(PKG_NAME_MAP_FILE, 'w') as f:
        yaml.dump(pkg_name_map, f, default_flow_style=False,
                  allow_unicode=True)


_repo_sack = None


def get_repo_sack():
    """Lazily build a dnf sack with all available repos loaded."""
    global _repo_sack
    if _repo_sack is None:
        base = dnf.Base()
        base.read_all_repos()
        base.fill_sack(load_system_repo=False, load_available_repos=True)
        _repo_sack = base.sack
    return _repo_sack


def source_name_for(binary_name):
    """Return the source package name for a binary package, or None."""
    pkgs = list(get_repo_sack().query().filter(name=binary_name).latest())
    if not pkgs:
        return None
    return pkgs[0].source_name


def resolve_null_mappings():
    """Resolve null binary->source mappings using dnf repos."""
    nulls = [k for k, v in pkg_name_map.items() if v is None]
    if not nulls:
        return

    updated = False
    for binary_name in nulls:
        source_name = source_name_for(binary_name)
        if source_name:
            pkg_name_map[binary_name] = source_name
            updated = True
            if verbose:
                print(f"  {binary_name} -> {source_name}")

    if updated:
        save_pkg_name_map()


def nvr_is_lower(nvr_a, nvr_b):
    """Return True if nvr_a is an older version than nvr_b."""
    na = dnf.subject.Subject(nvr_a).get_nevra_possibilities()[0]
    nb = dnf.subject.Subject(nvr_b).get_nevra_possibilities()[0]
    return na.evr_cmp(nb, _dnf_sack) < 0


def binary_name_from_nvra(nvra):
    """Extract binary package name from an NVR+arch string."""
    nvra = re.sub(r'\.(riscv64|x86_64|aarch64|noarch|i686|s390x|ppc64le)$',
                  '', nvra)
    nvra = re.sub(r'\.riscv64\.fc', '.fc', nvra)
    try:
        subject = dnf.subject.Subject(nvra)
        parsed = subject.get_nevra_possibilities()[0]
        return parsed.name
    except KeyError:
        return nvra.rsplit('-', 2)[0]


def pkg_name_from_nvra(nvra):
    """Extract source package name from a binary NVR+arch string."""
    guessed = binary_name_from_nvra(nvra)

    if guessed in pkg_name_map and pkg_name_map[guessed]:
        return pkg_name_map[guessed]

    return guessed


def get_koji_info(session, pkg_name, tag):
    """Get latest failed build and latest successful build in tag."""
    pkg_info = session.getPackage(pkg_name)

    # Latest non-complete build (failed, building, canceled)
    fc_suffix = '.fc' + tag.lstrip('f')
    latest_failure = None
    if pkg_info:
        all_builds = session.listBuilds(packageID=pkg_info['id'])
        non_complete = [b for b in all_builds
                        if b['state'] != 1 and fc_suffix in b['nvr']]
        if non_complete:
            latest_failure = sorted(non_complete,
                                    key=lambda x: x['build_id'],
                                    reverse=True)[0]

    # Latest successful build in the tag
    latest_success = None
    tagged_builds = session.listTagged(tag, package=pkg_name)
    if tagged_builds:
        complete = [b for b in tagged_builds if b['state'] == 1]
        if complete:
            latest_success = sorted(complete,
                                    key=lambda x: x['build_id'],
                                    reverse=True)[0]

    if not latest_failure and not latest_success:
        return None, None

    return latest_failure, latest_success


def format_build(build):
    """Format a build dict into a display string."""
    if not build:
        return "no builds found"
    state = koji.BUILD_STATES[build['state']]
    ts = build.get('completion_ts') or build.get('creation_ts')
    date = datetime.fromtimestamp(ts).strftime('%Y-%m-%d') if ts else '?'
    return f"{build['nvr']}  {state}  {date}"


def build_to_cache_entry(build):
    """Convert a Koji build dict to a cacheable dict."""
    if not build:
        return None
    state = koji.BUILD_STATES[build['state']]
    return {'nvr': build['nvr'], 'state': state}


def fetch_koji_info(pkg_name):
    """Fetch Koji info for a package and store in cache."""
    entry = {}

    for label, session in [("fedora", fedora_session),
                           ("riscv", riscv_session)]:
        attempt, success = get_koji_info(session, pkg_name, KOJI_TAG)
        entry[label] = {
            'attempt': build_to_cache_entry(attempt),
            'success': build_to_cache_entry(success),
        }

    has_data = any(entry[k][f] is not None
                   for k in ('fedora', 'riscv')
                   for f in ('attempt', 'success'))
    if not has_data:
        source_name = source_name_for(pkg_name)
        if source_name and source_name != pkg_name:
            if verbose:
                print(f"  no Koji data for '{pkg_name}', "
                      f"retrying as '{source_name}'")
            for label, session in [("fedora", fedora_session),
                                   ("riscv", riscv_session)]:
                attempt, success = get_koji_info(session, source_name,
                                                 KOJI_TAG)
                entry[label] = {
                    'attempt': build_to_cache_entry(attempt),
                    'success': build_to_cache_entry(success),
                }
            has_data = any(entry[k][f] is not None
                           for k in ('fedora', 'riscv')
                           for f in ('attempt', 'success'))
            if has_data:
                pkg_name_map[pkg_name] = source_name
                save_pkg_name_map()
    if not has_data:
        if pkg_name not in pkg_name_map:
            pkg_name_map[pkg_name] = None
            save_pkg_name_map()
            print(f"WARNING: no Koji data for '{pkg_name}' "
                  f"— add mapping to {PKG_NAME_MAP_FILE}",
                  file=sys.stderr)
    else:
        koji_cache[pkg_name] = entry
        save_koji_cache()

    return entry


def print_koji_info(pkg_name):
    """Print Koji build info for a package, using cache."""
    if pkg_name in koji_cache:
        entry = koji_cache[pkg_name]
    else:
        entry = fetch_koji_info(pkg_name)

    for label, key in [("Fedora", "fedora"), ("RISC-V", "riscv")]:
        data = entry[key]
        attempt = data['attempt']
        success = data['success']

        if attempt is None and success is None:
            pass
        elif attempt is None:
            print(f"\t{label}:")
            print(f"\t\t{success['nvr']}")
        elif success and attempt['nvr'] == success['nvr']:
            print(f"\t{label}:")
            print(f"\t\t{attempt['nvr']}")
        elif success and nvr_is_lower(attempt['nvr'], success['nvr']):
            print(f"\t{label}:")
            print(f"\t\t{success['nvr']}")
        else:
            print(f"\t{label}:")
            print(f"\t\tLast attempt: {attempt['nvr']}")
            if success:
                print(f"\t\tLast success: {success['nvr']}")
            else:
                print("\t\tLast success: none")


def show_older_than(packages, fedora_release):
    print(f"Packages older than Fedora {fedora_release}:\n")
    for pkg in packages:
        try:
            fedora_version = int(pkg.split('fc')[1].split('.')[0])
        except ValueError:
            fedora_version = int(pkg.split('fc')[1].split('~')[0])
        except IndexError:
            print(f"IndexError: {pkg}")

        if fedora_version < fedora_release:
            print(f"- {pkg}")

    print("")


def show_unresolved_deps(deps):
    sorted_deps = {}

    for pkg in deps:
        amount = len(deps[pkg])
        if amount not in sorted_deps:
            sorted_deps[amount] = []

        sorted_deps[amount].append(pkg)

    for i in sorted(sorted_deps, reverse=True):
        for dep_name in sorted(sorted_deps[i]):
            print(f"{dep_name} ({i}):")

            for pkg in deps[dep_name]:
                pkg_name = pkg_name_from_nvra(pkg)
                print(f"    {pkg}")
                print_koji_info(pkg_name)

            print()


def vr_from_nvr(nvr):
    """Extract version-release from an NVR string."""
    parts = nvr.rsplit('-', 2)
    if len(parts) == 3:
        return f"{parts[1]}-{parts[2]}"
    return nvr


def nvr_to_link(nvr, koji_web):
    """Turn an NVR into an HTML link to its Koji build page."""
    vr = vr_from_nvr(nvr)
    url = f"{koji_web}/search?match=exact&amp;type=build&amp;terms={nvr}"
    return f'<a href="{url}">{vr}</a>'


def koji_cell(build, koji_web):
    """Render a single koji build as a table cell."""
    if not build:
        return '<td class="dash">-</td>'
    link = nvr_to_link(build['nvr'], koji_web)
    state_class = build['state'].lower()
    return f'<td class="{state_class}">{link}</td>'


def load_summary():
    """Load repoclosure summary from stderr output."""
    try:
        with open(SUMMARY_FILE) as f:
            text = f.read()
        m = re.search(r'(\d+)\) across (\d+) packages', text)
        if m:
            return int(m.group(1)), int(m.group(2))
    except FileNotFoundError:
        pass
    return None, None


def generate_html(deps):
    sorted_deps = {}

    for pkg in deps:
        amount = len(deps[pkg])
        if amount not in sorted_deps:
            sorted_deps[amount] = []
        sorted_deps[amount].append(pkg)

    unresolved, pkg_count = load_summary()

    html = []
    html.append('<!DOCTYPE html>')
    html.append('<html><head>')
    html.append('<meta charset="utf-8">')
    html.append('<title>Repoclosure Report</title>')
    html.append('<style>')
    html.append('''
html, body {
    margin: 0; padding: 0;
}
body {
    font-family: sans-serif;
    padding: 24px 32px;
    background: #f8f9fa; color: #1a1a1a;
}
h1 {
    font-size: 1.6em; font-weight: 600;
    margin: 0 0 4px 0; color: #0d1117;
}
h1 + p { margin: 0 0 4px 0; color: #656d76; font-size: 0.9em; }
.tag-switcher { margin: 0 0 4px 0; font-size: 0.9em; }
.tag-switcher a { text-decoration: none; color: #0969da; }
.tag-switcher a:hover { text-decoration: underline; }
.tag-switcher + p { margin: 0 0 20px 0; }
table {
    border-collapse: collapse; width: 100%;
    background: #fff; border: 1px solid #d1d9e0;
    border-radius: 6px; overflow: hidden;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
thead th {
    position: sticky; z-index: 1;
    background: #f6f8fa; color: #1f2328;
    font-size: 0.8em; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.03em;
    padding: 10px 12px; border-bottom: 2px solid #d1d9e0;
    text-align: left;
}
thead tr:first-child th { top: 0; }
thead tr:nth-child(2) th { top: 34px; }
td {
    padding: 6px 12px; border-bottom: 1px solid #eaeef2;
    font-size: 0.88em; white-space: nowrap;
}
td a { text-decoration: none; }
td a:hover { text-decoration: underline; }
.failed { color: #cf222e; }
.failed a { color: #cf222e; }
.building { color: #bf8700; }
.building a { color: #bf8700; }
.canceled, .deleted { color: #8b949e; }
.canceled a, .deleted a { color: #8b949e; }
.complete { color: #1a7f37; }
.complete a { color: #1a7f37; }
.dep-header {
    background: #edf0f3; font-weight: 700;
    font-size: 1.1em; padding: 12px;
    border-top: 2px solid #d1d9e0;
    color: #1f2328;
}
.zebra { background: #f6f8fa; }
td.dash { color: #c9d1d9; text-align: center; }
    ''')
    html.append('</style>')
    html.append('</head><body>')
    tag_label = KOJI_TAG.replace('f', 'F')
    title = f'Repoclosure Report for {tag_label}'
    if unresolved is not None:
        title += f' — {unresolved} unresolved deps across {pkg_count} packages'
    html.append(f'<h1>{title}</h1>')
    tags = ['f45', 'f46']
    tag_links = []
    for t in tags:
        label = t.replace('f', 'F')
        if t == KOJI_TAG:
            tag_links.append(f'<b>{label}</b>')
        else:
            tag_links.append(f'<a href="repoclosure-{t}.html">{label}</a>')
    html.append(f'<p class="tag-switcher">{" | ".join(tag_links)}</p>')
    generated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')
    html.append(f'<p>Generated: <b>{generated} UTC</b></p>')
    html.append('<table>')
    html.append('<thead>')
    html.append('<tr><th>package name</th>'
                '<th colspan="2">RISC-V</th>'
                '<th colspan="2">Fedora</th></tr>')
    html.append('<tr><th></th>'
                '<th>last failure</th><th>success</th>'
                '<th>last failure</th><th>success</th></tr>')
    html.append('</thead>')
    html.append('<tbody>')

    for i in sorted(sorted_deps, reverse=True):
        for dep_name in sorted(sorted_deps[i]):
            html.append(f'<tr><td colspan="5" class="dep-header">'
                        f'{dep_name} ({i})</td></tr>')

            seen_pkg_names = set()
            pkg_names_ordered = []
            for pkg in deps[dep_name]:
                pkg_name = pkg_name_from_nvra(pkg)
                if pkg_name not in seen_pkg_names:
                    seen_pkg_names.add(pkg_name)
                    pkg_names_ordered.append(pkg_name)

            for row_idx, pkg_name in enumerate(sorted(pkg_names_ordered)):
                if pkg_name not in koji_cache:
                    fetch_koji_info(pkg_name)

                if pkg_name not in koji_cache:
                    continue

                entry = koji_cache[pkg_name]
                has_any = any(entry[k][f] is not None
                              for k in ('fedora', 'riscv')
                              for f in ('attempt', 'success'))
                if not has_any:
                    continue

                cls = ' class="zebra"' if row_idx % 2 else ''
                pkg_url = f'https://src.fedoraproject.org/rpms/{pkg_name}'
                html.append(f'<tr{cls}><td>'
                            f'<a href="{pkg_url}">{pkg_name}</a></td>')

                for key, koji_web in [
                    ("riscv", RISCV_KOJI_WEB),
                    ("fedora", FEDORA_KOJI_WEB),
                ]:
                    data = entry[key]
                    attempt = data['attempt']
                    success = data['success']

                    if attempt is None and success is None:
                        html.append('<td class="dash">-</td>'
                                    '<td class="dash">-</td>')
                    elif attempt is None:
                        link = nvr_to_link(success['nvr'],
                                           koji_web)
                        html.append('<td class="dash">-</td>')
                        html.append(
                            f'<td class="complete">'
                            f'{link}</td>')
                    elif (success
                          and attempt['nvr'] == success['nvr']):
                        link = nvr_to_link(attempt['nvr'],
                                           koji_web)
                        html.append('<td class="dash">-</td>')
                        html.append(
                            f'<td class="complete">'
                            f'{link}</td>')
                    elif (success
                          and nvr_is_lower(attempt['nvr'],
                                           success['nvr'])):
                        link = nvr_to_link(success['nvr'],
                                           koji_web)
                        html.append('<td class="dash">-</td>')
                        html.append(
                            f'<td class="complete">'
                            f'{link}</td>')
                    else:
                        html.append(koji_cell(attempt, koji_web))
                        html.append(koji_cell(success, koji_web))

                html.append('</tr>')

    html.append('</tbody>')
    html.append('</table>')

    html.append('</body></html>')

    outfile = f'repoclosure-{KOJI_TAG}.html'
    with open(outfile, 'w') as f:
        f.write('\n'.join(html))

    if verbose:
        print(f"\nGenerated {outfile}")


_dnf_base = dnf.Base()
_dnf_base.fill_sack(load_system_repo=False)
_dnf_sack = _dnf_base.sack

if args.force:
    import os
    try:
        os.remove(KOJI_CACHE_FILE)
    except FileNotFoundError:
        pass

load_koji_cache()
load_pkg_name_map()

# Populate name map with cached entries that have no builds
map_updated = False
for pkg_name, entry in koji_cache.items():
    if (entry['fedora']['attempt'] is None
            and entry['riscv']['attempt'] is None
            and pkg_name not in pkg_name_map):
        pkg_name_map[pkg_name] = None
        map_updated = True
if map_updated:
    save_pkg_name_map()

resolve_null_mappings()

fedora_session = koji.ClientSession(FEDORA_KOJI)
riscv_session = koji.ClientSession(RISCV_KOJI)

packages = {}
unresolved_deps = {}

with open("repoclosure.log", "r") as log:
    for line in log.readlines():
        if line.startswith("package:"):
            package_name = line.split(" ")[1]
            packages[package_name] = []
        elif line.startswith("    "):
            udep = line.strip()
            packages[package_name].append(udep)
            if udep not in unresolved_deps:
                unresolved_deps[udep] = []
            unresolved_deps[udep].append(package_name)

# pprint(packages)
# pprint(unresolved_deps)

if verbose:
    show_unresolved_deps(unresolved_deps)

generate_html(unresolved_deps)
