"""Build a traceable upload ZIP from committed strategy files."""
import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from check import ROOT, check

def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True).strip()

parser = argparse.ArgumentParser()
parser.add_argument('game', choices=['racing', 'fps'])
args = parser.parse_args()
check()
if git('status', '--porcelain'):
    raise SystemExit('Commit or resolve working tree changes before packaging.')
sha = git('rev-parse', 'HEAD')
folder = ROOT / 'strategies' / args.game
files = []
for name in git('ls-files', '--', 'strategies/' + args.game).splitlines():
    path = ROOT / name
    if path.is_symlink() or path.suffix not in {'.py', '.json', '.npz'}:
        raise SystemExit('Unsupported package file: ' + name)
    files.append(path)
required = {'agent.py'} | ({'design.json'} if args.game == 'racing' else set())
assert required <= {p.relative_to(folder).as_posix() for p in files}
out = ROOT / 'dist'
out.mkdir(exist_ok=True)
archive = out / f'{args.game}-{sha[:12]}.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
    for path in sorted(files):
        info = zipfile.ZipInfo(path.relative_to(folder).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        z.writestr(info, path.read_bytes())
with zipfile.ZipFile(archive) as z:
    assert z.testzip() is None
    assert required <= set(z.namelist())
record = {'game': args.game, 'git_sha': sha, 'zip_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
          'files': {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},
          'platform_validation': 'not_run', 'competition_activation': 'not_run'}
archive.with_suffix('.json').write_text(json.dumps(record, indent=2) + '\n')
print(archive)
