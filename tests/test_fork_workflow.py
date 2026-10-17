import os
import subprocess
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fork-image.sh"


def run(args, cwd, env=None):
    return subprocess.run(  # noqa: S603
        args, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )


@pytest.fixture
def release_repo(tmp_path):
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    assert run(["git", "init", "--bare", str(remote)], tmp_path).returncode == 0
    assert run(["git", "clone", str(remote), str(checkout)], tmp_path).returncode == 0
    for command in [
        ["config", "user.name", "Test"],
        ["config", "user.email", "test@example.test"],
        ["checkout", "-b", "main"],
        ["commit", "--allow-empty", "-m", "test base"],
        ["tag", "oliviacpu-2026.10.16"],
        ["push", "origin", "main", "--tags"],
    ]:
        assert run(["git", *command], checkout).returncode == 0
    commit = run(["git", "rev-parse", "HEAD"], checkout).stdout.strip()
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text("""#!/bin/sh
echo "$*" >> "$DOCKER_LOG"
case "$1" in
 pull)
   case "$DOCKER_MODE" in
     missing) echo 'manifest unknown' >&2; exit 1 ;;
     network) echo 'connection refused' >&2; exit 1 ;;
   esac ;;
 image)
   if [ "$DOCKER_MODE" = different ]; then echo different-commit; else echo "$GITHUB_SHA"; fi ;;
esac
""")
    docker.chmod(0o755)
    environment = {
        **os.environ,
        "PATH": str(binary) + os.pathsep + os.defpath,
        "GITHUB_REPOSITORY": "oliviacpu/wizarr",
        "GITHUB_REF": "refs/tags/oliviacpu-2026.10.16",
        "GITHUB_REF_NAME": "oliviacpu-2026.10.16",
        "GITHUB_SHA": commit,
        "GITHUB_OUTPUT": str(tmp_path / "output"),
        "DOCKER_LOG": str(tmp_path / "docker.log"),
        "DOCKER_MODE": "exists",
    }
    return checkout, environment


@pytest.mark.parametrize(
    "mode,success,output",
    [
        ("missing", True, "exists=false"),
        ("exists", True, "exists=true"),
        ("different", False, ""),
        ("network", False, ""),
    ],
)
def test_release_images_are_immutable_and_registry_failures_stop(
    release_repo, mode, success, output
):
    checkout, environment = release_repo
    environment["DOCKER_MODE"] = mode
    result = run(["bash", str(SCRIPT), "prepare"], checkout, environment)
    assert (result.returncode == 0) == success, result.stderr
    if output:
        assert Path(environment["GITHUB_OUTPUT"]).read_text().strip() == output


def test_older_release_cannot_move_latest_backward(release_repo):
    checkout, environment = release_repo
    assert run(["git", "tag", "oliviacpu-2026.10.17"], checkout).returncode == 0
    assert run(["git", "push", "origin", "--tags"], checkout).returncode == 0
    result = run(["bash", str(SCRIPT), "latest"], checkout, environment)
    assert result.returncode == 0, result.stderr
    assert not Path(environment["DOCKER_LOG"]).exists()
    environment.update(
        GITHUB_REF="refs/tags/oliviacpu-2026.10.17",
        GITHUB_REF_NAME="oliviacpu-2026.10.17",
    )
    result = run(["bash", str(SCRIPT), "latest"], checkout, environment)
    assert result.returncode == 0, result.stderr
    assert (
        "imagetools create --tag ghcr.io/oliviacpu/wizarr:latest"
        in Path(environment["DOCKER_LOG"]).read_text()
    )


def test_release_rejects_wrong_repository_and_moved_tag(release_repo):
    checkout, environment = release_repo
    environment["GITHUB_REPOSITORY"] = "wizarrrr/wizarr"
    assert run(["bash", str(SCRIPT), "prepare"], checkout, environment).returncode != 0
    environment["GITHUB_REPOSITORY"] = "oliviacpu/wizarr"
    environment["GITHUB_SHA"] = "0" * 40
    assert run(["bash", str(SCRIPT), "prepare"], checkout, environment).returncode != 0


def test_upstream_jobs_are_guarded_and_fork_images_require_tests():
    workflows = SCRIPT.parents[1] / ".github" / "workflows"
    for path in workflows.glob("*.yml"):
        workflow = yaml.safe_load(path.read_text())
        if path.name == "fork.yml":
            assert workflow["jobs"]["image"]["needs"] == "check"
            assert (
                workflow["jobs"]["image"]["concurrency"]["cancel-in-progress"] is False
            )
        else:
            for job in workflow["jobs"].values():
                assert "github.repository == 'wizarrrr/wizarr'" in job["if"], path.name
