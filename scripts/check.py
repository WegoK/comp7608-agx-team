"""Offline structural checks; does not simulate either competition."""
import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def check():
    manifest = json.loads((ROOT / 'sources' / 'Source_Manifest.json').read_text())
    for item in manifest['files'] + manifest['archives']:
        assert hashlib.sha256((ROOT / item['path']).read_bytes()).hexdigest() == item['sha256'], item['path']
    assert hashlib.sha256((ROOT / manifest['source_pdf']).read_bytes()).hexdigest() == manifest['source_sha256']
    for game in ('racing', 'fps'):
        folder = ROOT / 'strategies' / game
        source = (folder / 'agent.py').read_text()
        tree = ast.parse(source)
        assert any(isinstance(n, ast.FunctionDef) and n.name == 'act' for n in tree.body), game
        for path in folder.rglob('*.py'):
            compile(path.read_text(), str(path), 'exec')
        for path in folder.rglob('*.json'):
            json.loads(path.read_text())
    json.loads((ROOT / 'strategies/racing/design.json').read_text())
    print('PASS: official source hashes, Python syntax, act entry points, JSON')

if __name__ == '__main__':
    check()
