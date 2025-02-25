#!/usr/bin/env python3

# Copyright 2021 Xavier Claessens <xclaesse@gmail.com>

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
import json
import subprocess
import collections
import configparser
import re

from pathlib import Path
from utils import Version

PERMITTED_FILES = ['generator.sh', 'meson.build', 'meson_options.txt', 'LICENSE.build']
PER_PROJECT_PERMITTED_FILES = {
    'openssl': [
        'bn_conf.h',
        'dso_conf.h',
        'buildinf.h',
        'generate_gypi.pl.patch',
        'meson.build.tmpl',
        'README.md',
    ],
}
NO_TABS_FILES = ['meson.build', 'meson_options.txt']


class TestReleases(unittest.TestCase):
    def test_releases(self):
        # Take list of git tags
        stdout = subprocess.check_output(['git', 'tag'])
        tags = [t.strip() for t in stdout.decode().splitlines()]

        with open('releases.json', 'r') as f:
            releases = json.load(f)

        # All tags must be in the releases file
        for t in tags:
            name, version = t.rsplit('_', 1)
            self.assertIn(name, releases)
            self.assertIn(version, releases[name]['versions'])

        # Verify keys are sorted
        self.assertEqual(sorted(releases.keys()), list(releases.keys()))

        for name, info in releases.items():
            print('Checking', name)

            # We do extra checks in the case a new release is being made. This
            # is because some wraps are not passing all tests but we force making
            # them compliant next time we do a release.
            versions = info['versions']
            latest_tag = f'{name}_{versions[0]}'
            extra_checks = latest_tag not in tags

            # Make sure we can load wrap file
            config = configparser.ConfigParser()
            config.read(f'subprojects/{name}.wrap')

            # Basic checks
            self.assertTrue(re.fullmatch('[a-z][a-z0-9._-]*', name))
            self.assertEqual(config.sections()[0], 'wrap-file')
            wrap_section = config['wrap-file']
            self.assertIn('directory', wrap_section)
            self.check_has_no_path_separators(wrap_section['directory'])
            self.assertIn('source_filename', wrap_section)
            self.check_has_no_path_separators(wrap_section['source_filename'])
            self.assertIn('source_url', wrap_section)
            self.assertIn('source_hash', wrap_section)

            # FIXME: Not all wraps currently complies, only check for wraps we modify.
            if extra_checks:
                self.assertIn('provide', config.sections())

            patch_directory = wrap_section.get('patch_directory')
            if patch_directory:
                patch_path = Path('subprojects', 'packagefiles', patch_directory)

                self.assertTrue(patch_path.is_dir())
                # FIXME: Not all wraps currently complies, only check for wraps we modify.
                if extra_checks:
                    self.assertTrue(Path(patch_path, 'LICENSE.build').is_file())
                    self.check_files(name, patch_path)

            # Make sure it has the same deps/progs provided
            progs = []
            deps = []
            if 'provide' in config.sections():
                provide = config['provide']
                progs = [i.strip() for i in provide.get('program_names', '').split(',')]
                deps = [i.strip() for i in provide.get('dependency_names', '').split(',')]
                for k in provide:
                    if k not in {'dependency_names', 'program_names'}:
                        deps.append(k.strip())
            progs = [i for i in progs if i]
            deps = [i for i in deps if i]
            self.assertEqual(sorted(progs), sorted(info.get('program_names', [])))
            self.assertEqual(sorted(deps), sorted(info.get('dependency_names', [])))

            # Verify versions are sorted
            versions = info['versions']
            self.assertGreater(len(versions), 0)
            versions_obj = [Version(v) for v in versions]
            self.assertEqual(sorted(versions_obj, reverse=True), versions_obj)

            # The first version could be a new release, all others must have
            # a corresponding tag already.
            for i, v in enumerate(versions):
                t = f'{name}_{v}'
                ver, rev = v.rsplit('-', 1)
                self.assertTrue(re.fullmatch('[a-z0-9._]+', ver))
                self.assertTrue(re.fullmatch('[0-9]+', rev))
                if i == 0:
                    self.check_source_url(name, wrap_section, ver)
                if i == 0 and t not in tags:
                    self.check_new_release(name, info, wrap_section)
                else:
                    self.assertIn(t, tags)

    def check_has_no_path_separators(self, value):
        self.assertNotIn('/', value)
        self.assertNotIn('\\', value)

    def check_source_url(self, name, wrap_section, version):
        if name == 'sqlite3':
            segs = version.split('.')
            assert(len(segs) == 3)
            version = segs[0] + segs[1] + '0' + segs[2]
        elif name == 're2':
            version = f'{version[:4]}-{version[4:6]}-{version[6:8]}'
        elif name == 'netstring-c':
            # There is no specific version for netstring-c
            return True
        source_url = wrap_section['source_url']
        version_ = version.replace('.', '_')
        self.assertTrue(version in source_url or version_ in source_url,
                        f'Version {version} not found in {source_url}')

    def check_new_release(self, name, info, wrap_section):
        if not info.get('skip_ci', False):
            options = ['--fatal-meson-warnings', f'-Dwraps={name}']
            for o in info.get('build_options', []):
                if ':' not in o:
                    options.append(f'-D{name}:{o}')
                else:
                    options.append(f'-D{o}')
            if Path('_build', 'meson-private', 'cmd_line.txt').exists():
                options.append('--wipe')
            subprocess.check_call(['meson', 'setup', '_build'] + options)
            subprocess.check_call(['meson', 'compile', '-C', '_build'])
            subprocess.check_call(['meson', 'test', '-C', '_build'])
        else:
            subprocess.check_call(['meson', 'subprojects', 'download', name])

    def is_permitted_file(self, subproject, filename):
        if filename in PERMITTED_FILES:
            return True
        if filename.endswith('.h.meson'):
            return True
        if subproject in PER_PROJECT_PERMITTED_FILES and filename in PER_PROJECT_PERMITTED_FILES[subproject]:
            return True
        return False

    def check_files(self, subproject, patch_path):
        tabs = []
        not_permitted = []
        for f in patch_path.rglob('*'):
            if f.is_dir():
                continue
            elif not self.is_permitted_file(subproject, f.name):
                not_permitted.append(f)
            elif f.name in NO_TABS_FILES and '\t' in f.read_text():
                tabs.append(f)
        if tabs:
            tabs_str = ', '.join([str(f) for f in tabs])
            self.fail('Tabs in meson files are not allows: ' + tabs_str)
        if not_permitted:
            not_permitted_str = ', '.join([str(f) for f in not_permitted])
            self.fail('Not permitted files found: ' + not_permitted_str)


if __name__ == '__main__':
    unittest.main()
