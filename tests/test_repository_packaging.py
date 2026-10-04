"""Guard clean-checkout inputs and exclusions without requiring Docker or .git."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
RETIRED_FILES = (
    "deploy/deploy.sh",
    "app/static/js/imports/queue.v1.min.js",
    "app/static/js/imports/topojson.v1.min.js",
    "app/static/images/logo-footer.svg",
    "app/static/images/logo-type.svg",
    *(
        f"app/static/fonts/PTSansNarrow/PTSansNarrow-{weight}.{extension}"
        for weight in ("Bold", "Regular")
        for extension in ("eot", "svg", "ttf")
    ),
)
LOCAL_FILES = (
    ".idea/workspace.xml",
    ".vscode/settings.json",
    ".agents/local-notes.md",
    ".codex/config.toml",
    ".aws/credentials",
    ".ssh/id_ed25519",
    "gwasdiversitymonitor.iml",
    "static_figure/gwas_growth_diversity_figure.ipynb",
    "static_figure/gwas_growth_diversity_main_figure.png",
    "emails/draft.txt",
    "config.py",
    ".env",
    ".env.production",
    "private-key.pem",
    "private-key.key",
    "app/logging/application.log",
    "backups/snapshot.zip",
    "artifacts/browser/trace.zip",
    "test-results/result.json",
    "playwright-report/index.html",
    "node_modules/example/index.js",
    ".ruff_cache/cache-entry",
    "__pycache__/module.cpython-313.pyc",
    "data/catalog/raw/Cat_Stud.tsv",
    "data/funders/pubmed_grants.json",
    "data/support/generated-cache.json",
    *RETIRED_FILES,
)
REQUIRED_FILES = (
    "README.md",
    ".gitignore",
    ".dockerignore",
    ".env.example",
    "data_static.zip",
    "data/funders/funder_cleaner.json",
    "data/support/cohort_cleaner.json",
    "requirements.txt",
    "requirements-dev.txt",
    "docker-compose.yml",
    "generate_data.py",
    "generated_data_validation.py",
    "upstream_validation.py",
    "funder_pipeline.py",
    "gwasdiversitymonitor.py",
    "app/__init__.py",
    "app/routes.py",
    "app/templates/index.html",
    "app/static/css/app.scss.css",
    "app/static/sass/app.scss",
    "app/static/js/imports/d3.v4.min.js",
    "app/static/fonts/PTSansNarrow/PTSansNarrow-Regular.427aa961c68c.woff2",
    "app/static/fonts/PTSansNarrow/PTSansNarrow-Bold.ee484e3d8bba.woff2",
    "app/static/fonts/PTSansNarrow/PTSansNarrow-Regular.woff",
    "app/static/fonts/PTSansNarrow/PTSansNarrow-Bold.woff",
    "deploy/config.py",
    "deploy/flask.Dockerfile",
    "deploy/data.Dockerfile",
    "deploy/nginx.Dockerfile",
    "deploy/compose.release.yml",
    "deploy/release.py",
    "docs/DATA_PIPELINE.md",
    "docs/OPERATIONS.md",
    "docs/TESTING.md",
    "scripts/performance/browser_metrics.py",
    "tests/browser/run_unit_tests.py",
    "tests/browser/run_browser_tests.py",
    "tests/test_repository_packaging.py",
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    ".github/workflows/monitor.yml",
)


def docker_copy_sources(dockerfile):
    """Read local COPY sources, including JSON form and continued lines."""
    contents = dockerfile.read_text(encoding="utf-8").replace("\\\n", " ")
    for line in contents.splitlines():
        instruction, _, arguments = line.strip().partition(" ")
        if instruction.upper() != "COPY":
            continue
        from_stage = False
        while arguments.startswith("--"):
            option, _, arguments = arguments.partition(" ")
            from_stage = from_stage or option.startswith("--from=")
            arguments = arguments.lstrip()
        if from_stage:
            continue
        paths = json.loads(arguments) if arguments.startswith("[") else shlex.split(arguments)
        if len(paths) < 2:
            raise AssertionError(f"Malformed COPY in {dockerfile}: {line}")
        yield from paths[:-1]


class RepositoryPackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="gwas-packaging-")
        cls.addClassCleanup(cls.workspace.cleanup)
        cls.git_root = Path(cls.workspace.name)
        shutil.copyfile(REPOSITORY / ".gitignore", cls.git_root / ".gitignore")
        cls.environment = {
            key: value for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        cls.environment.update({
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TEMPLATE_DIR": "",
        })
        subprocess.run(
            ["git", "init", "--quiet", str(cls.git_root)],
            env=cls.environment, check=True, capture_output=True, text=True,
        )

    def ignored_paths(self, paths):
        paths = tuple(paths)
        result = subprocess.run(
            [
                "git", "-C", str(self.git_root), "-c",
                f"core.excludesFile={os.devnull}", "check-ignore",
                "--no-index", "--stdin", "-z",
            ],
            input="\0".join(paths) + "\0", env=self.environment,
            capture_output=True, text=True,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return set(filter(None, result.stdout.split("\0")))

    def test_local_artifacts_are_ignored(self):
        self.assertEqual(self.ignored_paths(LOCAL_FILES), set(LOCAL_FILES))

    def test_reproduction_inputs_remain_versionable(self):
        self.assertEqual(self.ignored_paths(REQUIRED_FILES), set())
        for relative in REQUIRED_FILES:
            with self.subTest(path=relative):
                self.assertTrue((REPOSITORY / relative).is_file(), relative)

    def test_docker_copy_sources_exist_and_are_versionable(self):
        dockerfiles = sorted((REPOSITORY / "deploy").glob("*.Dockerfile"))
        self.assertTrue(dockerfiles, "No deployment Dockerfiles found")
        sources = []
        for dockerfile in dockerfiles:
            copied = list(docker_copy_sources(dockerfile))
            self.assertTrue(copied, f"No local COPY sources in {dockerfile}")
            for source in copied:
                with self.subTest(dockerfile=dockerfile.name, source=source):
                    self.assertFalse(Path(source).is_absolute(), source)
                    self.assertNotIn("..", Path(source).parts, source)
                    matches = list(REPOSITORY.glob(source))
                    self.assertTrue(matches, f"Missing Docker COPY source: {source}")
                    sources.extend(path.relative_to(REPOSITORY).as_posix() for path in matches)
        self.assertEqual(self.ignored_paths(sources), set())

    def test_docker_context_excludes_local_and_retired_material(self):
        rules = {
            line.strip().strip("/")
            for line in (REPOSITORY / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        expected = {
            ".idea", ".vscode", ".agents", ".codex", "*.iml",
            ".aws", ".ssh", "*.pem", "*.key", ".env", ".env.*",
            "static_figure", "emails", "backups", "artifacts",
            "test-results", "playwright-report", "node_modules",
            *RETIRED_FILES,
        }
        self.assertEqual(expected - rules, set(), "Missing Docker context exclusions")


if __name__ == "__main__":
    unittest.main()
