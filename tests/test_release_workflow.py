"""Check branch-isolated deployment policy without GitHub, SSH, or Docker.

The small extractors below read this repository's deliberately conventional
workflow layout, not arbitrary YAML. Keeping these tests in the standard-library
suite avoids adding a YAML parser to the application just to test CI wiring.
"""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/release.yml'
COMMIT = 'a' * 40


def section(document, name, indent):
    """Return an indentation-delimited mapping block, retaining its indentation."""
    lines = document.splitlines()
    marker = re.compile(r'^' + ' ' * indent + re.escape(name) + r':\s*(?:#.*)?$')
    starts = [index for index, line in enumerate(lines) if marker.fullmatch(line)]
    if len(starts) != 1:
        raise AssertionError(f'Expected one {name!r} mapping at indentation {indent}')
    start = starts[0] + 1
    end = len(lines)
    for index in range(start, len(lines)):
        line = lines[index]
        if line.strip() and not line.lstrip().startswith('#'):
            if len(line) - len(line.lstrip()) <= indent:
                end = index
                break
    return '\n'.join(lines[start:end])


def scalar(document, name, indent):
    matches = re.findall(
        r'^' + ' ' * indent + re.escape(name) + r':[ \t]*(\S[^\n]*)$',
        document, flags=re.MULTILINE,
    )
    if len(matches) != 1:
        raise AssertionError(f'Expected one {name!r} value at indentation {indent}')
    return matches[0].strip()


def named_steps(job):
    matches = list(re.finditer(r'^      - name: ([^\n]+)$', job, re.MULTILINE))
    for match in matches:
        following = re.search(r'^      - ', job[match.end():], re.MULTILINE)
        end = match.end() + following.start() if following else len(job)
        yield match.group(1), job[match.end():end]


def shell_body(step):
    marker = re.search(r'^        run: \|[ \t]*$', step, re.MULTILINE)
    if marker is None:
        raise AssertionError('Expected a literal Bash run block')
    return textwrap.dedent(step[marker.end():]).strip() + '\n'


def step_environment(step, contexts):
    """Substitute simple GitHub contexts using local, non-secret test fixtures."""
    environment = {}
    for name, expression in re.findall(r'^          ([A-Z_]+): ([^\n]+)$', step, re.MULTILINE):
        if expression.startswith('${{'):
            context = expression.removeprefix('${{').removesuffix('}}').strip()
            if context.startswith('secrets.') or context == 'github.token':
                environment[name] = 'test-placeholder-not-a-secret'
            elif context in contexts:
                environment[name] = contexts[context]
            else:
                raise AssertionError(f'Unrecognized workflow test context: {context}')
        else:
            environment[name] = expression.strip('"\'')
    return environment


def run_step(step, contexts, *, remote_sha=COMMIT, gh_status=0):
    """Execute only workflow control flow; all external tools are inert stubs."""
    with tempfile.TemporaryDirectory(prefix='gwas-workflow-test-') as directory:
        work = Path(directory)
        binaries = work / 'bin'
        binaries.mkdir()
        for executable in ('gh', 'bash', 'python'):
            stub = binaries / executable
            stub.write_text(
                '#!/bin/sh\n'
                f'printf "{executable} %s\\n" "$*" >> "$TEST_CALLS"\n'
                + ('[ "$TEST_GH_STATUS" = 0 ] || exit "$TEST_GH_STATUS"\n'
                   'printf "%s\\n" "$TEST_REMOTE_SHA"\n' if executable == 'gh' else ''),
                encoding='utf-8',
            )
            stub.chmod(0o700)
        output = work / 'output'
        output.touch()
        calls = work / 'calls'
        environment = dict(os.environ)
        environment.update(step_environment(step, contexts))
        environment.update({
            'PATH': str(binaries) + os.pathsep + os.defpath,
            'GITHUB_OUTPUT': str(output),
            'GITHUB_REPOSITORY': contexts['github.repository'],
            'GITHUB_REF_NAME': contexts['github.ref'].removeprefix('refs/heads/'),
            'GITHUB_SHA': COMMIT,
            'TEST_CALLS': str(calls),
            'TEST_GH_STATUS': str(gh_status),
            'TEST_REMOTE_SHA': remote_sha,
        })
        result = subprocess.run(
            ['/bin/bash', '-e', '-o', 'pipefail', '-c', shell_body(step)],
            cwd=work, env=environment, text=True, capture_output=True, timeout=10,
        )
        values = dict(line.split('=', 1) for line in output.read_text().splitlines())
        return result, values, calls.read_text().splitlines() if calls.exists() else []


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding='utf-8')
        cls.jobs = section(cls.workflow, 'jobs', 0)

    def job(self, name):
        return section(self.jobs, name, 2)

    def contexts(self, branch='dev', event='push', manual='false', dev='', main=''):
        return {
            'github.repository': 'OxfordDemSci/gwasdiversitymonitor',
            'github.ref': f'refs/heads/{branch}',
            'github.event_name': event,
            'inputs.deploy': manual,
            'vars.GWAS_DEPLOY_DEV_ENABLED': dev,
            'vars.GWAS_DEPLOY_MAIN_ENABLED': main,
        }

    def test_only_dev_and_main_pushes_and_explicit_manual_deploy(self):
        events = section(self.workflow, 'on', 0)
        branches = scalar(section(events, 'push', 2), 'branches', 4)
        self.assertEqual(
            {value.strip().strip('"\'') for value in branches.strip('[]').split(',')},
            {'dev', 'main'},
        )
        inputs = section(section(events, 'workflow_dispatch', 2), 'inputs', 4)
        self.assertEqual(re.findall(r'^      ([\w-]+):$', inputs, re.MULTILINE), ['deploy'])
        deploy = section(inputs, 'deploy', 6)
        self.assertEqual(scalar(deploy, 'type', 8), 'boolean')
        self.assertEqual(scalar(deploy, 'default', 8), 'false')

    def test_each_branch_is_serialized_without_cancelling_active_deploys(self):
        concurrency = section(self.workflow, 'concurrency', 0)
        self.assertIn('${{ github.ref }}', scalar(concurrency, 'group', 2))
        self.assertEqual(scalar(concurrency, 'cancel-in-progress', 2), 'false')

    def test_branch_jobs_are_independent_and_environment_secrets_are_scoped(self):
        for job_name, branch, domain in (
            ('staging', 'dev', 'dev.gwasdiversitymonitor.com'),
            ('production', 'main', 'gwasdiversitymonitor.com'),
        ):
            with self.subTest(job=job_name):
                job = self.job(job_name)
                environment = section(job, 'environment', 4)
                self.assertEqual(scalar(environment, 'name', 6), job_name)
                self.assertEqual(scalar(environment, 'url', 6), f'https://{domain}')
                condition = scalar(job, 'if', 4)
                self.assertIn("needs.guard.outputs.deploy == 'true'", condition)
                self.assertIn(f"github.ref == 'refs/heads/{branch}'", condition)
                needs = scalar(job, 'needs', 4)
                self.assertEqual(set(re.findall(r'[a-z]+', needs)), {'guard', 'images'})
                self.assertIn(f'DEPLOY_DOMAIN: {domain}', job)
                for secret in ('DEPLOY_HOST', 'DEPLOY_USER', 'DEPLOY_SSH_KEY', 'DEPLOY_KNOWN_HOSTS'):
                    self.assertIn('${{ secrets.' + secret + ' }}', job)
        self.assertNotIn('needs: [images, staging]', self.job('production'))

    def test_push_opt_ins_cannot_enable_the_other_branch(self):
        guard = self.job('guard')
        self.assertEqual(
            scalar(guard, 'if', 4),
            "github.repository == 'OxfordDemSci/gwasdiversitymonitor'",
        )
        step = next(named_steps(guard))[1]
        for branch in ('dev', 'main'):
            for dev in ('', 'false', 'true'):
                for main in ('', 'false', 'true'):
                    with self.subTest(branch=branch, dev=dev, main=main):
                        result, values, calls = run_step(step, self.contexts(branch, dev=dev, main=main))
                        self.assertEqual(result.returncode, 0, result.stderr)
                        expected = dev if branch == 'dev' else main
                        self.assertEqual(values, {'deploy': str(expected == 'true').lower()})
                        self.assertEqual(calls, [])

    def test_manual_dispatch_is_build_only_unless_deploy_is_explicit(self):
        step = next(named_steps(self.job('guard')))[1]
        for branch in ('dev', 'main'):
            for manual in ('', 'false', 'true'):
                with self.subTest(branch=branch, manual=manual):
                    result, values, _ = run_step(
                        step, self.contexts(branch, 'workflow_dispatch', manual, dev='true', main='true'),
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(values, {'deploy': str(manual == 'true').lower()})

    def test_guard_refuses_other_branches_even_with_manual_deployment_requested(self):
        step = next(named_steps(self.job('guard')))[1]
        for branch in ('master', 'feature/new-filter', 'refs/tags/dev'):
            with self.subTest(branch=branch):
                result, values, calls = run_step(
                    step, self.contexts(branch, 'workflow_dispatch', 'true', dev='true', main='true'),
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(values.get('deploy'), 'true')
                self.assertEqual(calls, [])

    def test_latest_tip_is_checked_before_remote_deployment_or_public_probe(self):
        for job_name, branch, domain in (
            ('staging', 'dev', 'dev.gwasdiversitymonitor.com'),
            ('production', 'main', 'gwasdiversitymonitor.com'),
        ):
            steps = [step for _, step in named_steps(self.job(job_name))
                     if 'deploy/remote_release.sh' in step]
            self.assertEqual(len(steps), 1)
            step = steps[0]
            for sha, gh_status in ((COMMIT, 0), ('b' * 40, 0), ('', 1)):
                with self.subTest(job=job_name, sha=sha, gh_status=gh_status):
                    result, _, calls = run_step(step, self.contexts(branch), remote_sha=sha, gh_status=gh_status)
                    self.assertTrue(calls)
                    self.assertEqual(
                        calls[0],
                        f'gh api repos/OxfordDemSci/gwasdiversitymonitor/git/ref/heads/{branch} --jq .object.sha',
                    )
                    if gh_status:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(len(calls), 1)
                    elif sha != COMMIT:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(len(calls), 1)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(calls[1:], [
                            'bash deploy/remote_release.sh release.json',
                            f'python deploy/monitor.py https://{domain} --expected-commit {COMMIT}',
                        ])

    def test_images_remain_tested_commit_stamped_and_digest_pinned(self):
        images = self.job('images')
        self.assertIn('tests', scalar(images, 'needs', 4))
        for service in ('flask', 'data', 'nginx'):
            self.assertIn(f'file: deploy/{service}.Dockerfile', images)
            self.assertIn(f'${{{{ steps.{service}.outputs.digest }}}}', images)
            self.assertIn(f'gwasdiversitymonitor-{service}:${{{{ github.sha }}}}', images)
        self.assertEqual(images.count('build-args: GWAS_BUILD_SHA=${{ github.sha }}'), 3)
        self.assertIn("+ '@' + os.environ[name.upper()]", images)
        self.assertIn('python deploy/release.py validate release.json', images)
        self.assertIn('retention-days: 90', images)
        for job_name in ('staging', 'production'):
            self.assertIn('name: release-${{ github.sha }}', self.job(job_name))


if __name__ == '__main__':
    unittest.main()
