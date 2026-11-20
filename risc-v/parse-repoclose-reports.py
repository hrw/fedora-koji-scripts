#!/usr/bin/python3

'''
Command to get logs (or just run ./run-repoclosure.sh <tag>):

dnf repoclosure --refresh --forcearch=riscv64 --nogpgcheck --arch=riscv64 \
--repo=fedora-riscv \
--repofrompath=fedora-riscv,https://riscv-koji.fedoraproject.org/repos/f45-build/latest/riscv64/

The log and its stderr summary are read from repoclosure-<tag>.log and
repoclosure-<tag>-summary.txt (that is what run-repoclosure.sh writes).

we need lists of:

- packages not built since gods were walking on surface of Earth
- most common unresolved_deps
- whatever came out of it

Some Might be removed, and thus needs removal on our end. Some of these require
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
import os
import re
import sys
import time
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlencode

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
DEP_PROVIDERS_FILE = 'dep-providers.yml'


LOG_FILE = f'repoclosure-{KOJI_TAG}.log'
SUMMARY_FILE = f'repoclosure-{KOJI_TAG}-summary.txt'

# how many packages to stuff into one koji multicall batch
KOJI_MULTICALL_CHUNK = 25

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
_cmp_sack = None
_libdnf5_base = None


def get_repo_sack():
    """Lazily build a dnf sack with all available repos loaded."""
    global _repo_sack
    if _repo_sack is None:
        base = dnf.Base()
        base.read_all_repos()
        base.fill_sack(load_system_repo=False, load_available_repos=True)
        _repo_sack = base.sack
    return _repo_sack


def get_libdnf5_base():
    """Lazily build a libdnf5 base with the host's repos loaded."""
    global _libdnf5_base
    if _libdnf5_base is None:
        import libdnf5
        base = libdnf5.base.Base()
        base.load_config()
        base.setup()
        repo_sack = base.get_repo_sack()
        repo_sack.create_repos_from_system_configuration()
        repo_sack.load_repos()
        _libdnf5_base = base
    return _libdnf5_base


def get_cmp_sack():
    """Lazily build an empty dnf sack for NEVRA comparisons."""
    global _cmp_sack
    if _cmp_sack is None:
        base = dnf.Base()
        base.fill_sack(load_system_repo=False)
        _cmp_sack = base.sack
    return _cmp_sack


def source_name_for(binary_name):
    """Return the source package name for a binary package, or None."""
    pkgs = list(get_repo_sack().query().filter(name=binary_name).latest())
    if not pkgs:
        return None
    return pkgs[0].source_name


SONAME_DEP_RE = re.compile(r'\.so\.\d')
RISCV_ARCH_RE = re.compile(r'riscv-?64')


def dep_query_variants(dep):
    """Dep string variants to query, in order.

    Besides the dep as-is, arch-qualified deps (MPI builds, e.g.
    'foo.so.1()(64bit)(mpich-riscv64)') are re-tried with the qualifier
    rewritten to the host arch and with the qualifier dropped.
    """
    variants = [dep]
    if RISCV_ARCH_RE.search(dep):
        basearch = dnf.conf.Conf().basearch
        for qual in {basearch, basearch.replace('_', '-')}:
            variants.append(RISCV_ARCH_RE.sub(qual, dep))
        variants.append(re.sub(r'\)\([^()]*\)$', ')', dep))

    unique = []
    for variant in variants:
        if variant not in unique:
            unique.append(variant)
    return unique


# digit runs in soname names, used to normalize versions away
SONAME_NORM_RE = re.compile(r'[0-9._]+')

# soname -> package mappings move slowly; cache resolves for two weeks
DEP_PROVIDERS_TTL = 14 * 24 * 3600

_dep_providers_cache = None


def load_dep_providers():
    global _dep_providers_cache
    if _dep_providers_cache is None:
        try:
            with open(DEP_PROVIDERS_FILE) as f:
                _dep_providers_cache = yaml.safe_load(f) or {}
        except FileNotFoundError:
            _dep_providers_cache = {}


def save_dep_providers():
    with open(DEP_PROVIDERS_FILE, 'w') as f:
        yaml.dump(_dep_providers_cache, f, default_flow_style=False,
                  allow_unicode=True)


def provider_by_stem_glob(base, dep):
    """Identify the owning package of an outdated soname via a glob.

    Matches provides that differ from the dep only in version digits,
    e.g. libavcodec.so.62 vs the libavcodec.so.63 shipped by
    libavcodec-free; other libraries with a similar prefix (libraw vs
    libraw1394) are ignored.
    """
    import libdnf5
    left = dep.split('(', 1)[0].partition('.so.')[0]
    wanted = SONAME_NORM_RE.sub('#', left)
    pattern = SONAME_NORM_RE.sub('*', left) + '*'

    query = libdnf5.rpm.PackageQuery(base)
    query.filter_provides(pattern, libdnf5.common.QueryCmp_GLOB)

    names = set()
    for pkg in query:
        for reldep in pkg.get_provides():
            name = str(reldep).split('(', 1)[0].partition('.so.')[0]
            if SONAME_NORM_RE.sub('#', name) == wanted:
                names.add(pkg.get_name())
                break
    return ', '.join(sorted(names)) if names else None


def resolve_providers(deps):
    """Resolve soname deps to providing package names, bulk + cached.

    Returns {dep: "provider[, provider]" or None}. Exact matches (with
    MPI/arch qualifiers rewritten for the host arch) go through a single
    bulk libdnf5 query; sonames older than the host release ships fall
    back to a stem glob. Results - misses included - are cached in
    dep-providers.yml, as each glob query costs ~0.5s.
    """
    import libdnf5

    load_dep_providers()
    soname_deps = sorted(d for d in deps if SONAME_DEP_RE.search(d))
    result = {}
    todo = []
    now = time.time()

    for dep in soname_deps:
        entry = _dep_providers_cache.get(dep)
        if entry and now - entry.get('ts', 0) < DEP_PROVIDERS_TTL:
            result[dep] = entry.get('providers')
        else:
            todo.append(dep)

    if todo:
        base = get_libdnf5_base()

        # tier 1: exact provides, arch-qualifier variants included, bulk
        variant_map = {}
        for dep in todo:
            for variant in dep_query_variants(dep):
                variant_map.setdefault(variant, []).append(dep)

        query = libdnf5.rpm.PackageQuery(base)
        query.filter_provides(list(variant_map))
        exact_pkgs = [(pkg.get_name(), {str(r) for r in pkg.get_provides()})
                      for pkg in query]

        glob_todo = []
        for dep in todo:
            for variant in dep_query_variants(dep):
                names = sorted({name for name, provides in exact_pkgs
                                if variant in provides})
                if names:
                    result[dep] = ', '.join(names)
                    break
            else:
                glob_todo.append(dep)

        # tier 2: stem glob for outdated sonames
        for dep in glob_todo:
            result[dep] = provider_by_stem_glob(base, dep)

        for dep in todo:
            _dep_providers_cache[dep] = {'providers': result[dep],
                                         'ts': int(now)}
        save_dep_providers()

    return result


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
    return na.evr_cmp(nb, get_cmp_sack()) < 0


def binary_name_from_nvra(nvra):
    """Extract binary package name from an NVR+arch string."""
    nvra = re.sub(r'\.(riscv64|x86_64|aarch64|noarch|i686|s390x|ppc64le)$',
                  '', nvra)
    nvra = re.sub(r'\.riscv64\.fc', '.fc', nvra)
    try:
        subject = dnf.subject.Subject(nvra)
        parsed = subject.get_nevra_possibilities()[0]
        return parsed.name
    except (KeyError, IndexError):
        return nvra.rsplit('-', 2)[0]


def pkg_name_from_nvra(nvra):
    """Extract source package name from a binary NVR+arch string."""
    guessed = binary_name_from_nvra(nvra)

    if guessed in pkg_name_map and pkg_name_map[guessed]:
        return pkg_name_map[guessed]

    return guessed


def empty_entry():
    """A koji cache entry for a package we have no build data for."""
    return {k: {'attempt': None, 'success': None}
            for k in ('fedora', 'riscv')}


def entry_has_data(entry):
    return any(entry[k][f] is not None
               for k in ('fedora', 'riscv')
               for f in ('attempt', 'success'))


def build_to_cache_entry(build):
    """Convert a Koji build dict to a cacheable dict."""
    if not build:
        return None
    state = koji.BUILD_STATES[build['state']]
    return {'nvr': build['nvr'], 'state': state}


_sessions = None


def get_sessions():
    """Lazily create koji client sessions."""
    global _sessions
    if _sessions is None:
        _sessions = {
            'fedora': koji.ClientSession(FEDORA_KOJI),
            'riscv': koji.ClientSession(RISCV_KOJI),
        }
    return _sessions


def _unwrap_multicall(result):
    """Unwrap a koji multicall result entry (single-element list or fault)."""
    if isinstance(result, list) and len(result) == 1:
        return result[0]
    if isinstance(result, dict) and 'faultCode' in result:
        return None
    return result


def _bulk_get_koji_info_chunk(session, pkg_names, tag):
    """get_koji_info() for a batch of packages via one koji multicall.

    Returns {pkg_name: (latest_failure, latest_success)}.
    """
    fc_suffix = '.fc' + tag.lstrip('f')
    results = {name: (None, None) for name in pkg_names}

    # round 1: which of the packages exist on this koji instance at all
    session.multicall = True
    for name in pkg_names:
        session.getPackage(name)
    infos = session.multiCall()

    pkg_ids = {}
    for name, info in zip(pkg_names, infos):
        info = _unwrap_multicall(info)
        if info:
            pkg_ids[name] = info['id']

    # round 2: all builds + builds tagged into our tag
    if pkg_ids:
        session.multicall = True
        for name, pkg_id in pkg_ids.items():
            session.listBuilds(packageID=pkg_id)
            session.listTagged(tag, package=name)
        builds = session.multiCall()

        names = list(pkg_ids)
        for i, name in enumerate(names):
            all_builds = _unwrap_multicall(builds[2 * i]) or []
            tagged_builds = _unwrap_multicall(builds[2 * i + 1]) or []

            # latest non-complete build (failed, building, canceled)
            non_complete = [b for b in all_builds
                            if b['state'] != 1 and fc_suffix in b['nvr']]
            latest_failure = max(non_complete, key=lambda b: b['build_id'],
                                 default=None)

            # latest successful build in the tag
            complete = [b for b in tagged_builds if b['state'] == 1]
            latest_success = max(complete, key=lambda b: b['build_id'],
                                 default=None)

            results[name] = (latest_failure, latest_success)

    return results


def bulk_get_koji_info(session, pkg_names, tag):
    """Fetch latest failed/successful builds for many packages in bulk."""
    results = {}
    for start in range(0, len(pkg_names), KOJI_MULTICALL_CHUNK):
        chunk = pkg_names[start:start + KOJI_MULTICALL_CHUNK]
        results.update(_bulk_get_koji_info_chunk(session, chunk, tag))
        if verbose:
            print(f"  {session.baseurl}: fetched "
                  f"{min(start + KOJI_MULTICALL_CHUNK, len(pkg_names))}/"
                  f"{len(pkg_names)}")
    return results


def fetch_entries_bulk(names):
    """Fetch koji data for all names; returns {name: cache entry}.

    Every requested name gets an entry (possibly empty); entries with any
    build data are stored in koji_cache.
    """
    per_instance = {}
    for label, session in get_sessions().items():
        per_instance[label] = bulk_get_koji_info(session, names, KOJI_TAG)

    entries = {}
    for name in names:
        entry = {}
        for label in per_instance:
            attempt, success = per_instance[label][name]
            entry[label] = {
                'attempt': build_to_cache_entry(attempt),
                'success': build_to_cache_entry(success),
            }
        entries[name] = entry
        if entry_has_data(entry):
            koji_cache[name] = entry

    save_koji_cache()
    return entries


def collect_entries(pkg_names):
    """Return {pkg_name: koji cache entry} for all given package names.

    Serves what it can from koji_cache; the rest is fetched from koji in
    bulk. Packages with no data under their own name are retried under
    their source package name, like the old per-package fetch did.
    """
    entries = {}
    todo = []

    for name in sorted(pkg_names):
        if name in koji_cache:
            entries[name] = koji_cache[name]
        elif name in pkg_name_map and pkg_name_map[name] is None:
            # known to have no koji data; do not query the servers again
            entries[name] = empty_entry()
        else:
            todo.append(name)

    if not todo:
        return entries

    print(f"Fetching koji info for {len(todo)} packages...")
    fetched = fetch_entries_bulk(todo)
    entries.update(fetched)

    # no data under the binary name? retry under the source package name
    retries = {}
    for name in todo:
        if entry_has_data(fetched[name]):
            continue
        source_name = source_name_for(name)
        if source_name and source_name != name:
            if verbose:
                print(f"  no Koji data for '{name}', "
                      f"retrying as '{source_name}'")
            retries[name] = source_name

    map_updated = False
    if retries:
        refetched = fetch_entries_bulk(sorted(set(retries.values())))
        for name, source_name in retries.items():
            entry = refetched.get(source_name)
            if entry and entry_has_data(entry):
                entries[name] = entry
                koji_cache[name] = entry
                pkg_name_map[name] = source_name
                map_updated = True
        save_koji_cache()

    # remember packages with no data at all, so we do not hammer koji with
    # them on every run; remove the null entry from the map to retry
    for name in todo:
        if not entry_has_data(entries[name]):
            if name not in pkg_name_map:
                pkg_name_map[name] = None
                map_updated = True
                print(f"WARNING: no Koji data for '{name}' "
                      f"— add mapping to {PKG_NAME_MAP_FILE} (or remove "
                      f"the null entry to retry on the next run)",
                      file=sys.stderr)

    if map_updated:
        save_pkg_name_map()

    return entries


def print_koji_info(pkg_name, entries):
    """Print Koji build info for a package, from pre-fetched entries."""
    entry = entries.get(pkg_name)
    if not entry:
        return

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


PACKAGE_RE = re.compile(r'^package:\s+(\S+)')
DEP_RE = re.compile(r'^    (.+?)\s*$')


def parse_repoclosure_log(log_file):
    """Parse repoclosure output.

    Returns ({nvra: [unresolved deps]}, {dep: [nvras]}).
    """
    packages = {}
    unresolved_deps = {}
    current = None

    with open(log_file) as log:
        for line in log:
            m = PACKAGE_RE.match(line)
            if m:
                current = m.group(1)
                packages.setdefault(current, [])
                continue

            m = DEP_RE.match(line)
            if m:
                if current is None:
                    print(f"WARNING: dep without a package header: "
                          f"{m.group(1)!r}", file=sys.stderr)
                    continue
                udep = m.group(1)
                packages[current].append(udep)
                unresolved_deps.setdefault(udep, []).append(current)

    return packages, unresolved_deps


def group_deps_by_count(deps):
    """Group {dep: [nvras]} into [(count, dep, [nvras])].

    Most-hit deps first, then alphabetically.
    """
    by_count = {}
    for dep, nvras in deps.items():
        by_count.setdefault(len(nvras), []).append(dep)

    grouped = []
    for count in sorted(by_count, reverse=True):
        for dep in sorted(by_count[count]):
            grouped.append((count, dep, deps[dep]))
    return grouped


def show_unresolved_deps(grouped, entries, providers):
    for count, dep_name, nvras in grouped:
        provider = providers.get(dep_name)
        suffix = f' - {provider}' if provider else ''
        print(f"{dep_name} ({count}){suffix}:")

        for nvra in nvras:
            print(f"    {nvra}")
            print_koji_info(pkg_name_from_nvra(nvra), entries)

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
    query = urlencode({'match': 'exact', 'type': 'build', 'terms': nvr})
    url = f'{koji_web}/search?{query}'.replace('&', '&amp;')
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


def generate_html(deps, entries, providers):
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
.dep-provider { color: #656d76; font-weight: 400; font-size: 0.85em; }
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

    for count, dep_name, nvras in group_deps_by_count(deps):
        provider = providers.get(dep_name)
        suffix = (f' <span class="dep-provider">- {escape(provider)}</span>'
                  if provider else '')
        html.append(f'<tr><td colspan="5" class="dep-header">'
                    f'{escape(dep_name)} ({count}){suffix}</td></tr>')

        pkg_names = sorted({pkg_name_from_nvra(nvra) for nvra in nvras})

        row_idx = 0
        for pkg_name in pkg_names:
            entry = entries.get(pkg_name)
            if entry is None or not entry_has_data(entry):
                continue

            cls = ' class="zebra"' if row_idx % 2 else ''
            row_idx += 1
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


def main():
    if args.force:
        for cache_file in (KOJI_CACHE_FILE, DEP_PROVIDERS_FILE):
            try:
                os.remove(cache_file)
            except FileNotFoundError:
                pass

    if not os.path.exists(LOG_FILE):
        sys.exit(f"repoclosure log '{LOG_FILE}' not found — "
                 f"run ./run-repoclosure.sh {KOJI_TAG} first")

    load_koji_cache()
    load_pkg_name_map()

    # Populate name map with cached entries that have no builds
    map_updated = False
    for pkg_name, entry in koji_cache.items():
        if not entry_has_data(entry) and pkg_name not in pkg_name_map:
            pkg_name_map[pkg_name] = None
            map_updated = True
    if map_updated:
        save_pkg_name_map()

    resolve_null_mappings()

    packages, unresolved_deps = parse_repoclosure_log(LOG_FILE)
    grouped = group_deps_by_count(unresolved_deps)

    names = {pkg_name_from_nvra(nvra)
             for _, _, nvras in grouped
             for nvra in nvras}
    entries = collect_entries(names)
    providers = resolve_providers(unresolved_deps)

    if verbose:
        show_unresolved_deps(grouped, entries, providers)

    generate_html(unresolved_deps, entries, providers)


if __name__ == '__main__':
    main()
