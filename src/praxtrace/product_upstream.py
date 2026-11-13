"""Find or fetch a pinned public source checkout for PraxTrace."""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "upstreams.json"


def load_pins():
    pins = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for name, pin in pins.items():
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise ValueError(f"Invalid project ID: {name}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", pin["repository"]):
            raise ValueError(f"{name}: expected GitHub owner/repo")
        if not re.fullmatch(r"[0-9a-f]{40}", pin["commit"]):
            raise ValueError(f"{name}: expected a full commit SHA")
        for entry in pin["entry_files"]:
            path = PurePosixPath(entry)
            if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in entry or ":" in entry:
                raise ValueError(f"{name}: invalid entry file")
    return pins


def git(path, *args, timeout=15, hooks=None):
    command = ["git", "--no-optional-locks", "-C", str(path), "-c", "core.fsmonitor=false"]
    if hooks is not None:
        command += ["-c", f"core.hooksPath={hooks}", "-c", "submodule.recurse=false"]
    return subprocess.run(
        [*command, *args],
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=True,
    ).stdout.strip()


def origin_url(pin):
    return f"https://github.com/{pin['repository']}.git"


def ready(path, pin):
    # Reuse only a direct checkout with the expected origin, commit and entry files.
    if path.resolve() != path or not (path / ".git").is_dir() or (path / ".git").resolve() != path / ".git":
        return False
    try:
        return (
            git(path, "config", "--local", "--get", "remote.origin.url").removesuffix(".git")
            == origin_url(pin).removesuffix(".git")
            and git(path, "rev-parse", "--verify", "HEAD") == pin["commit"]
            and not git(path, "status", "--porcelain", "--untracked-files=normal")
            and all((path / entry).is_file() and (path / entry).resolve().is_relative_to(path)
                    for entry in ["LICENSE", *pin["entry_files"]])
        )
    except (OSError, subprocess.SubprocessError):
        return False


def fetch_checkout(cache, destination, pin, timeout):
    # Publish a checkout only after fetching and checking out the exact pin.
    if destination.parent != cache or destination.exists() or destination.is_symlink():
        raise FileExistsError("destination already exists or is outside the cache")
    with TemporaryDirectory(prefix=".fetch-", dir=cache) as temp:
        staging = Path(temp)
        if staging.resolve().parent != cache:
            raise ValueError("staging escaped the cache")
        hooks = staging / ".disabled-hooks"
        git(staging, "init", "--quiet", "--template=", hooks=hooks)
        git(staging, "remote", "add", "origin", origin_url(pin), hooks=hooks)
        git(staging, "fetch", "--depth=1", "--no-tags", "--no-recurse-submodules", "origin", pin["commit"],
            timeout=timeout, hooks=hooks)
        if git(staging, "rev-parse", "--verify", "FETCH_HEAD") != pin["commit"]:
            raise ValueError("downloaded commit differs from the pin")
        git(staging, "checkout", "--quiet", "--detach", "--no-recurse-submodules", "FETCH_HEAD", hooks=hooks)
        if not ready(staging, pin):
            raise ValueError("downloaded checkout failed verification; check the pin and entry files")
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("destination appeared during the download")
        staging.rename(destination)


def main(*, workspace=None, cache_path=Path(".praxtrace/cache/upstream")):
    workspace = Path.cwd() if workspace is None else Path(workspace)
    pins = load_pins()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="*", metavar="PROJECT")
    parser.add_argument("--fetch", action="store_true", help="download named projects only")
    parser.add_argument("--timeout", type=int, default=180, help="fetch timeout in seconds (default: 180)")
    args = parser.parse_args()
    if args.fetch and not args.projects:
        parser.error("--fetch requires at least one project ID")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    invalid = [name for name in args.projects if name not in pins]
    if invalid:
        parser.error(f"unknown project(s): {', '.join(invalid)}; choices: {', '.join(pins)}")

    cache = workspace / cache_path
    if cache.resolve() != cache:
        parser.error("refusing a redirected upstream directory")
    failed = False
    for name in dict.fromkeys(args.projects or pins):
        pin = pins[name]
        legacy = cache / name
        destination = cache / f"{name}-{pin['commit'][:12]}"
        print(f"{name} {origin_url(pin)} @ {pin['commit']}", flush=True)
        local = next((p for p in (destination, legacy) if ready(p, pin)), None)
        if local:
            print(f"  ready: {local.relative_to(workspace)}")
            continue
        if destination.exists() or destination.is_symlink():
            print(f"  blocked: existing checkout could not be verified; kept at {destination.relative_to(workspace)}")
            failed = failed or args.fetch
            continue
        if not args.fetch:
            refs = cache / f"{name}-reference"
            if refs.is_dir():
                state = "reference files present; no verified checkout"
            elif legacy.exists() or legacy.is_symlink():
                state = "older path exists but could not be verified"
            else:
                state = "missing"
            print(f"  {state}; download target: {destination.relative_to(workspace)}")
            continue
        try:
            cache.mkdir(parents=True, exist_ok=True)
            fetch_checkout(cache, destination, pin, args.timeout)
            print(f"  ready: {destination.relative_to(workspace)}")
        except subprocess.TimeoutExpired:
            print("  failed: Git timed out; retry with a larger --timeout if needed")
            failed = True
        except subprocess.CalledProcessError as exc:
            print(f"  failed: Git exit {exc.returncode}; check network or pinned commit availability")
            failed = True
        except FileNotFoundError:
            print("  failed: Git executable or local path not found")
            failed = True
        except (OSError, ValueError) as exc:
            print(f"  failed: {exc}")
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
