"""Stage the Thor platform translation unit without editing its component checkout."""

from pathlib import Path
import shutil
import subprocess
import tempfile


def prepare_thor_platform(device, source, output):
    patch = device / "patches/pong-adreno/0001-thor-serialize-gpu-startup.patch"
    with tempfile.TemporaryDirectory(prefix="thor-adreno-") as workspace:
        staging = Path(workspace) / source.name
        shutil.copyfile(source, staging)
        apply = ["git", "apply", str(patch)]
        already_applied = subprocess.run(
            apply + ["--reverse", "--check"],
            cwd=workspace,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
        if not already_applied:
            subprocess.run(apply + ["--whitespace=error"], cwd=workspace, check=True)
        compiled_source = output / source.name
        shutil.copyfile(staging, compiled_source)
    return compiled_source
