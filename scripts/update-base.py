#!/usr/bin/env python3
"""Re-base the onyx kernel build on a new HyperOS boot image.

The build is pinned to one AOSP GKI commit, one stock kernel config and one
exact release string (that string is the module vermagic).  When Xiaomi ships
new firmware, all three may change.  This script automates the whole loop:

  1. read the kernel and the embedded config out of the supplied boot.img;
  2. parse the release string and resolve the matching kernel_common commit;
  3. write the new config, and update scripts/build.sh, sources.lock.json and
     README.md;
  4. optionally commit/push (which triggers the GitHub Actions build), wait
     for it, download the artifacts and repack a flashable boot image;
  5. optionally publish the result as a GitHub release.

Typical use, after downloading the new firmware's boot.img:

    python scripts/update-base.py --boot new-boot.img
    # review the diff, then run the full pipeline:
    python scripts/update-base.py --boot new-boot.img --push --wait --repack

If the release string is unchanged (same kernel base, new firmware build),
the script skips the rebuild and just repacks the given boot.img with the
existing artifacts/Image.

Only Python 3 and the GitHub CLI (gh) are needed on the local machine; the
kernel itself is compiled on GitHub Actions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_SH = ROOT / "scripts" / "build.sh"
LOCK = ROOT / "sources.lock.json"
README = ROOT / "README.md"
CONFIGS = ROOT / "configs"
ARTIFACTS = ROOT / "artifacts"
WORK = ROOT / "work"

GKI_REPO = "aosp-mirror/kernel_common"
GKI_BRANCH = "android15-6.6"
WORKFLOW = "build.yml"
ARTIFACT_NAME = "onyx-susfs-sukisu"

RELEASE_RE = re.compile(
    r"^(?P<version>\d+\.\d+\.\d+)"
    r"(?:-(?P<android>android\d+-\d+(?:\.\d+)?))?"
    r"-g(?P<sha>[0-9a-f]{7,40})"
    r"(?:-ab(?P<build>\d+))?"
    r"(?P<suffix>.*)$"
)

sys.path.insert(0, str(ROOT / "tools"))
from bootimg import BootImage, BootImageError  # noqa: E402


def log(message: str) -> None:
    print(f"[update-base] {message}", flush=True)


def die(message: str, code: int = 1) -> "NoReturn":  # type: ignore[name-defined]
    print(f"[update-base] error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def run(cmd: list[str], check: bool = True, capture: bool = True) -> str:
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
        )
    except FileNotFoundError:
        die(f"command not found: {cmd[0]}")
    if check and result.returncode != 0:
        output = (result.stdout or "").strip()
        die(f"command failed: {' '.join(cmd)}\n{output}")
    return (result.stdout or "").strip()


def run_status(cmd: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except FileNotFoundError:
        die(f"command not found: {cmd[0]}")
    return result.returncode, (result.stdout or "").strip()


def read_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_release(release: str) -> dict[str, str | None]:
    match = RELEASE_RE.match(release)
    if not match:
        die(
            "unexpected kernel release string "
            f"{release!r}; expected something like "
            "6.6.118-android15-8-ge56cf6b09cca-ab15511674-4k"
        )
    return match.groupdict()


def kernel_release(image: BootImage) -> str:
    kernel = image.kernel
    tokens = []
    start = 0
    while True:
        index = kernel.find(b"Linux version ", start)
        if index < 0:
            break
        token = kernel[index + len(b"Linux version "):].split(b"\0", 1)[0]
        token = token.split(b" ", 1)[0].split(b"\n", 1)[0]
        text = token.decode("ascii", "ignore")
        if re.match(r"^\d+\.\d+\.\d+-", text):
            tokens.append(text)
        start = index + 1
    if not tokens:
        die("could not find a 'Linux version <release>' string in the kernel")
    # The banner can appear more than once; they must all agree.
    if len(set(tokens)) != 1:
        die(f"conflicting release strings in the kernel: {sorted(set(tokens))}")
    return tokens[0]


def current_stock_release() -> str:
    match = re.search(r'^STOCK_RELEASE="([^"]+)"', read_text(BUILD_SH), re.M)
    if not match:
        die("could not find STOCK_RELEASE in scripts/build.sh")
    return match.group(1)


def current_kernel_commit() -> str:
    match = re.search(
        r"^KERNEL_COMMIT=\$\{KERNEL_COMMIT:-([0-9a-f]{40})\}", read_text(BUILD_SH), re.M
    )
    if not match:
        die("could not find KERNEL_COMMIT in scripts/build.sh")
    return match.group(1)


def resolve_commit(prefix: str) -> str:
    """Resolve an abbreviated kernel_common commit to its full SHA."""
    try:
        full = run(["gh", "api", f"repos/{GKI_REPO}/commits/{prefix}", "--jq", ".sha"])
        if re.fullmatch(r"[0-9a-f]{40}", full):
            return full
        log("gh api returned an unexpected value, falling back to the public API")
    except SystemExit:
        log("gh api lookup failed, falling back to the public GitHub API")

    url = f"https://api.github.com/repos/{GKI_REPO}/commits/{prefix}"
    request = urllib.request.Request(url, headers={"User-Agent": "onyx-update-base"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        die(f"could not resolve {GKI_REPO} commit {prefix}: {exc}")
    full = payload.get("sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", full):
        die(f"unexpected GitHub API response while resolving {prefix}")
    return full


def verify_gki_version(commit: str, version: str) -> None:
    url = f"https://raw.githubusercontent.com/{GKI_REPO}/{commit}/Makefile"
    request = urllib.request.Request(url, headers={"User-Agent": "onyx-update-base"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        die(f"could not read the Makefile at {commit}: {exc}")
    parts = {}
    for key in ("VERSION", "PATCHLEVEL", "SUBLEVEL"):
        match = re.search(rf"^{key}\s*=\s*(\d+)", text, re.M)
        if not match:
            die(f"could not parse {key} from the Makefile at {commit}")
        parts[key] = match.group(1)
    found = f"{parts['VERSION']}.{parts['PATCHLEVEL']}.{parts['SUBLEVEL']}"
    if found != version:
        die(
            f"{GKI_REPO}@{commit[:12]} is Linux {found}, but the firmware "
            f"kernel is {version}. The release string does not match that "
            "commit; refusing to pin a build to the wrong source."
        )


def update_build_sh(text: str, commit: str, release: str, config_name: str) -> str:
    substitutions = [
        (
            re.compile(r"^KERNEL_COMMIT=\$\{KERNEL_COMMIT:-[0-9a-f]{40}\}", re.M),
            f"KERNEL_COMMIT=${{KERNEL_COMMIT:-{commit}}}",
        ),
        (
            re.compile(r'^STOCK_RELEASE="[^"]+"', re.M),
            f'STOCK_RELEASE="{release}"',
        ),
        (
            re.compile(r'cp "\$ROOT/configs/[^"]+" "\$OUT/\.config"'),
            f'cp "$ROOT/configs/{config_name}" "$OUT/.config"',
        ),
    ]
    for pattern, replacement in substitutions:
        text, count = pattern.subn(replacement, text)
        if count != 1:
            die(f"expected exactly one match for {pattern.pattern!r}, got {count}")
    return text


def update_lock(text: str, parsed: dict[str, str | None], release: str, commit: str,
                config_name: str) -> str:
    data = json.loads(text)
    kernel = data.setdefault("kernel", {})
    kernel["commit"] = commit
    kernel["version"] = parsed["version"]
    kernel["release"] = release
    kernel["config"] = f"configs/{config_name}"
    device = data.setdefault("device", {})
    firmware = device.get("firmware", "")
    device["firmware"] = re.sub(
        r"kernel \d+\.\d+\.\d+", f"kernel {parsed['version']}", firmware
    )
    return json.dumps(data, indent=2) + "\n"


def update_readme(text: str, release: str, commit: str, parsed: dict[str, str | None]) -> str:
    short = commit[:12]
    rows = [
        (
            re.compile(r"^\| Kernel \| .*$", re.M),
            f"| Kernel | Linux {parsed['version']} (AOSP GKI `{short}`) |",
        ),
        (
            re.compile(r"^\| Release \| .*$", re.M),
            f"| Release | `{release}` |",
        ),
    ]
    for pattern, replacement in rows:
        text, count = pattern.subn(replacement, text)
        if count != 1:
            die(f"expected exactly one README row for {pattern.pattern!r}, got {count}")
    return text


def config_filename(parsed: dict[str, str | None]) -> str:
    tag = f"ab{parsed['build']}" if parsed.get("build") else f"g{str(parsed['sha'])[:12]}"
    return f"onyx-{parsed['version']}-{tag}.config"


def find_boot_image(path: Path) -> BootImage:
    try:
        image = BootImage.load(path)
    except (BootImageError, OSError) as exc:
        die(f"cannot read {path}: {exc}")
    if image.ramdisk_size or image.signature_size:
        die(
            f"{path} has ramdisk_size={image.ramdisk_size} and "
            f"signature_size={image.signature_size}; the repacker only supports "
            "kernel-only boot images with the ramdisk in init_boot.img"
        )
    if image.header_version != 4:
        log(f"warning: boot header is v{image.header_version}, expected v4")
    return image


def latest_run_for(commit: str) -> int:
    for attempt in range(12):
        raw = run(
            [
                "gh", "run", "list",
                "--workflow", WORKFLOW,
                "--commit", commit,
                "--limit", "1",
                "--json", "databaseId,status,conclusion,headSha",
            ]
        )
        runs = json.loads(raw or "[]")
        if runs:
            return int(runs[0]["databaseId"])
        if attempt == 0:
            log("waiting for the workflow run to appear")
        time.sleep(5)
    die("no workflow run appeared for the pushed commit")


def download_artifacts(run_id: int, dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    run(["gh", "run", "download", str(run_id), "--name", ARTIFACT_NAME, "--dir", str(dest)])
    for name in ("Image", "Image.gz", "onyx-susfs-sukisu-anykernel3.zip",
                 "onyx-susfs-sukisu.config", "kernel.release", "build.log"):
        src = dest / name
        if src.exists():
            shutil.copy2(src, ARTIFACTS / name)
    if not (ARTIFACTS / "Image").exists():
        die(f"artifact download did not produce {ARTIFACTS / 'Image'}")


def repack(stock: Path, kernel_path: Path, output: Path) -> Path:
    image = find_boot_image(stock)
    kernel = kernel_path.read_bytes()
    if b"Linux version " not in kernel:
        die(f"{kernel_path} does not look like a kernel image")
    try:
        result = image.repack(kernel)
    except BootImageError as exc:
        die(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    log(f"wrote {output} ({len(result)} bytes, kernel {len(kernel)} bytes)")
    log(f"sha256 {sha256(result)}")
    return output


def release_notes(release: str, parsed: dict[str, str | None]) -> str:
    return f"""SukiSU-Ultra + SUSFS kernel for the **POCO F7 / Redmi Turbo 4 Pro** (`onyx`), built for the HyperOS firmware whose stock kernel is:

`{release}`

| | |
| --- | --- |
| Kernel | Linux {parsed['version']} (AOSP GKI `{str(parsed['sha'])[:12]}`) |
| Release string | `{release}` |
| Root | SukiSU-Ultra |
| Hiding | SUSFS |

## Assets

| File | Use |
| --- | --- |
| `boot-susfs-onyx.img` | Ready-to-flash boot image (kernel-only, ramdisk stays in `init_boot.img`). |
| `onyx-susfs-sukisu-anykernel3.zip` | AnyKernel3 installer; keeps your current ramdisk and bootconfig. |
| `Image`, `Image.gz` | Raw / compressed kernel for manual repacking. |
| `onyx-susfs-sukisu.config` | Exact kernel config used for this build. |
| `SHA256SUMS.txt` | Checksums. |

## Install

```sh
adb reboot bootloader
fastboot flash boot boot-susfs-onyx.img
fastboot reboot
```

Install the SukiSU manager APK, open it once, then verify with
`adb shell su -c id`.

## Restore stock

```sh
fastboot flash boot stock-boot.img
```

Built from the stock config with only the KSU/SUSFS options changed. Restore
the stock boot image before taking an OTA, and never relock the bootloader
while this kernel is installed.
"""


def ensure_clean_identity() -> None:
    name = run(["git", "config", "user.name"], check=False)
    email = run(["git", "config", "user.email"], check=False)
    if not name or not email:
        die("git user.name/user.email are not set in this repository")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-base the onyx kernel build on a new HyperOS boot.img",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--boot", required=True, type=Path,
                        help="stock boot.img from the new firmware")
    parser.add_argument("--push", action="store_true",
                        help="commit the updated pins and push to trigger the build")
    parser.add_argument("--wait", action="store_true",
                        help="wait for the pushed build and download its artifacts")
    parser.add_argument("--repack", action="store_true",
                        help="repack a flashable boot image once Image is available")
    parser.add_argument("--release", metavar="TAG",
                        help="publish a GitHub release with this tag")
    parser.add_argument("--image", type=Path,
                        help="use this Image for --repack instead of artifacts/Image")
    parser.add_argument("--dry-run", action="store_true",
                        help="detect and print the plan without writing anything")
    parser.add_argument("--force", action="store_true",
                        help="continue even if the GKI branch changes (a port, not a re-base)")
    args = parser.parse_args(argv)

    if args.wait and not args.push:
        die("--wait requires --push")
    if args.release and not args.repack:
        die("--release requires --repack")

    stock = args.boot.resolve()
    if not stock.exists():
        die(f"{stock} does not exist")

    image = find_boot_image(stock)
    release = kernel_release(image)
    parsed = parse_release(release)
    log(f"stock boot image: {stock}")
    log(f"kernel release:   {release}")
    log(f"version {parsed['version']}, branch {parsed['android']}, "
        f"GKI {parsed['sha']}, build ab{parsed['build']}")

    previous_release = current_stock_release()
    if release == previous_release:
        log("this is the same kernel base that is already pinned; nothing to rebuild")
        kernel_path = args.image
        if kernel_path is None:
            kernel_path = ARTIFACTS / "Image"
        if args.repack:
            if not kernel_path.exists():
                die(
                    f"{kernel_path} is missing; download it from the latest "
                    "release or pass --image"
                )
            output = ARTIFACTS / "boot-susfs-onyx.img"
            repack(stock, kernel_path, output)
        else:
            log("re-run with --repack to produce a flashable boot image from artifacts/Image")
        return 0

    # GKI_BRANCH looks like "android15-6.6"; the "android15-8" token in the
    # release string is the Android release plus KMI generation, not the
    # kernel_common branch, so only the kernel series has to match.
    expected_series = GKI_BRANCH.rsplit("-", 1)[-1]
    actual_series = ".".join(str(parsed["version"]).split(".")[:2])
    if actual_series != expected_series and not args.force:
        die(
            f"the firmware kernel is Linux {parsed['version']}, but this tree "
            f"is set up for {expected_series} ({GKI_BRANCH}). That is a port, "
            "not a re-base. Re-run with --force after reading the 'When "
            "HyperOS updates the kernel' section, and expect to update SUSFS "
            "and possibly the boot header handling."
        )

    commit = resolve_commit(str(parsed["sha"]))
    log(f"resolved {parsed['sha']} -> {commit}")
    verify_gki_version(commit, str(parsed["version"]))

    config_name = config_filename(parsed)
    config_path = CONFIGS / config_name
    config = image.extract_config()
    config_data = config or b""

    build_text = update_build_sh(read_text(BUILD_SH), commit, release, config_name)
    lock_text = update_lock(read_text(LOCK), parsed, release, commit, config_name)
    readme_text = update_readme(read_text(README), release, commit, parsed)

    if config is None and not args.dry_run:
        die("the kernel does not embed IKCONFIG; cannot extract the stock config")

    if config_path.exists() and config_data and config_path.read_bytes() != config_data:
        die(
            f"{config_path} already exists with different content; move it "
            "aside if the new firmware genuinely ships a different config"
        )

    if args.dry_run:
        log("dry run: would write")
        log(f"  {config_path} ({len(config_data)} bytes)")
        log(f"  scripts/build.sh: KERNEL_COMMIT={commit}, STOCK_RELEASE={release}")
        log(f"  sources.lock.json, README.md")
        return 0

    write_text(config_path, config_data.decode("utf-8", "replace"))
    log(f"wrote {config_path} ({len(config_data)} bytes)")

    write_text(BUILD_SH, build_text)
    write_text(LOCK, lock_text)
    write_text(README, readme_text)
    log("updated scripts/build.sh, sources.lock.json and README.md")

    diff = run(["git", "diff", "--stat"], check=False)
    if diff:
        print(diff)

    if not args.push:
        log("not pushed; review the diff and re-run with --push --wait --repack")
        return 0

    ensure_clean_identity()
    files = ["scripts/build.sh", "sources.lock.json", "README.md", f"configs/{config_name}"]
    run(["git", "add", *files])
    run(["git", "commit", "-m", f"kernel: re-base on {release}"])
    commit_head = run(["git", "rev-parse", "HEAD"])
    log(f"pushed {commit_head[:12]} - the build workflow will start now")
    run(["git", "push", "origin", "HEAD"])

    if not args.wait:
        log("re-run with --wait once the workflow finishes to download and repack")
        return 0

    run_id = latest_run_for(commit_head)
    log(f"watching run {run_id} (the build takes about 15 minutes)")
    run(["gh", "run", "watch", str(run_id), "--interval", "20", "--exit-status"],
        check=False, capture=False)
    conclusion = json.loads(
        run(["gh", "run", "view", str(run_id), "--json", "conclusion"])
    ).get("conclusion")
    if conclusion != "success":
        run(["gh", "run", "view", str(run_id), "--log-failed"], check=False,
            capture=False)
        die(f"build {run_id} finished with {conclusion!r}")

    download_artifacts(run_id, WORK / "download")
    log("downloaded kernel artifacts into artifacts/")

    if not args.repack:
        log("re-run with --repack (or repack manually) to produce boot-susfs-onyx.img")
        return 0

    kernel_path = args.image or (ARTIFACTS / "Image")
    output = ARTIFACTS / "boot-susfs-onyx.img"
    repack(stock, kernel_path, output)

    if args.release:
        status, _ = run_status(["gh", "release", "view", args.release])
        if status == 0:
            die(f"release {args.release} already exists")
        notes = Path(tempfile.gettempdir()) / f"onyx-{args.release}.md"
        write_text(notes, release_notes(release, parsed))
        assets = [
            output,
            ARTIFACTS / "onyx-susfs-sukisu-anykernel3.zip",
            ARTIFACTS / "Image",
            ARTIFACTS / "Image.gz",
            ARTIFACTS / "onyx-susfs-sukisu.config",
        ]
        checksums = ARTIFACTS / "SHA256SUMS.txt"
        lines = [
            f"{sha256(path.read_bytes())}  {path.name}"
            for path in assets if path.exists()
        ]
        write_text(checksums, "\n".join(lines) + "\n")
        assets.append(checksums)
        missing = [path.name for path in assets if not path.exists()]
        if missing:
            die(f"missing release assets: {', '.join(missing)}")
        repo_slug = run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
        )
        run([
            "gh", "release", "create", args.release,
            "--title", f"{args.release} - SukiSU-Ultra + SUSFS ({parsed['version']})",
            "--notes-file", str(notes),
            *[str(path) for path in assets],
        ])
        log(f"published https://github.com/{repo_slug}/releases/tag/{args.release}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
