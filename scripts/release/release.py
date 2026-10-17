"""Build once, transport and deploy a content-addressed Compose release (stdlib)."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.request import urlopen

SERVICES = ('gateway', 'rag-api', 'dotnet-ingest')


def run(*args, cwd=None):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def inspect(image):
    return json.loads(run('docker', 'image', 'inspect', image))[0]


def build(root, out):
    source = run('git', 'rev-parse', 'HEAD', cwd=root)
    if run('git', 'status', '--porcelain', cwd=root):
        raise ValueError('release requires a clean checkout')
    out.mkdir(parents=True, exist_ok=False)
    images = {}
    for service in SERVICES:
        tag = f'ai-platform/{service}:{source}'
        run('docker', 'build', '--label', f'org.opencontainers.image.revision={source}',
            '--tag', tag, str(root / 'services' / service))
        images[service] = inspect(tag)['Id']
    run('docker', 'pull', 'pgvector/pgvector:pg16')
    images['postgres'] = inspect('pgvector/pgvector:pg16')['Id']
    # No provider keys and no public database/backend ports: synthetic mock profile.
    compose = {'services': {
        'postgres': {'image': images['postgres'], 'environment': {
            'POSTGRES_USER': 'ai', 'POSTGRES_PASSWORD': 'synthetic-demo-only', 'POSTGRES_DB': 'aiplatform'},
            'healthcheck': {'test': ['CMD-SHELL', 'pg_isready -U ai -d aiplatform'],
                            'interval': '3s', 'timeout': '3s', 'retries': 30},
            'volumes': ['pgdata:/var/lib/postgresql/data']},
        'rag-api': {'image': images['rag-api'], 'environment': {
            'DATABASE_URL': 'postgresql+psycopg://ai:synthetic-demo-only@postgres:5432/aiplatform',
            'EMBEDDING_MODE': 'mock'},
            'depends_on': {'postgres': {'condition': 'service_healthy'}}},
        'dotnet-ingest': {'image': images['dotnet-ingest'], 'environment': {
            'ConnectionStrings__Default': 'Host=postgres;Port=5432;Database=aiplatform;Username=ai;Password=synthetic-demo-only',
            'RAG_API_URL': 'http://rag-api:8000', 'EMBEDDING_MODE': 'mock'},
            'depends_on': {'postgres': {'condition': 'service_healthy'}}},
        'gateway': {'image': images['gateway'], 'environment': {
            'PORT': '3000', 'RAG_API_URL': 'http://rag-api:8000', 'DOTNET_API_URL': 'http://dotnet-ingest:8080'},
            'ports': ['127.0.0.1:3002:3000'], 'depends_on': ['rag-api', 'dotnet-ingest']}
    }, 'volumes': {'pgdata': {}}}
    save(out / 'compose.json', compose)
    run('docker', 'save', '-o', str(out / 'images.tar'), *images.values())
    manifest = {'schema': 1, 'source_revision': source, 'execution_mode': 'synthetic_mock',
                'images': images, 'compose_sha256': sha(out / 'compose.json'),
                'archive_sha256': sha(out / 'images.tar'),
                'built_at_utc': datetime.now(timezone.utc).isoformat()}
    save(out / 'manifest.json', manifest)
    return manifest


def validate(bundle, expected_source):
    manifest = json.loads((bundle / 'manifest.json').read_text())
    if not re.fullmatch('[0-9a-f]{40}', expected_source) or manifest['source_revision'] != expected_source:
        raise ValueError('unexpected source revision')
    if manifest['schema'] != 1 or manifest['execution_mode'] != 'synthetic_mock':
        raise ValueError('unsupported release schema or execution mode')
    if set(manifest['images']) != set(SERVICES) | {'postgres'}:
        raise ValueError('incomplete image set')
    if not all(re.fullmatch(r'sha256:[0-9a-f]{64}', i) for i in manifest['images'].values()):
        raise ValueError('image references must be immutable IDs')
    for filename, field in [('compose.json', 'compose_sha256'), ('images.tar', 'archive_sha256')]:
        if sha(bundle / filename) != manifest[field]:
            raise ValueError('release checksum mismatch: ' + filename)
    compose = json.loads((bundle / 'compose.json').read_text())
    if set(compose['services']) != set(manifest['images']):
        raise ValueError('unexpected Compose services')
    for name, image in manifest['images'].items():
        if compose['services'][name]['image'] != image or 'build' in compose['services'][name]:
            raise ValueError('Compose image does not match manifest')
    return manifest


def healthy(payload):
    return (payload.get('status') == 'ok' and len(payload.get('services', [])) == 2
            and all(s.get('status') == 'ok' for s in payload['services']))


def deploy(bundle, expected_source, receipt_path, project):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,40}', project):
        raise ValueError('invalid Compose project')
    if receipt_path.exists():
        raise ValueError('choose a new receipt path')
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {'source_revision': expected_source, 'status': 'failed', 'project': project,
               'started_at_utc': datetime.now(timezone.utc).isoformat()}
    try:
        manifest = validate(bundle, expected_source)
        receipt.update(images=manifest['images'], configuration_sha256=manifest['compose_sha256'],
                       manifest_sha256=sha(bundle / 'manifest.json'), execution_mode=manifest['execution_mode'])
        run('docker', 'load', '-i', str(bundle / 'images.tar'))
        for service in SERVICES:
            label = inspect(manifest['images'][service])['Config']['Labels'].get('org.opencontainers.image.revision')
            if label != expected_source:
                raise ValueError('image source label mismatch')
        cmd = ['docker', 'compose', '-p', project, '-f', str(bundle / 'compose.json')]
        run(*cmd, 'up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120')
        running = {}
        for service, image in manifest['images'].items():
            container = run(*cmd, 'ps', '-q', service)
            actual = json.loads(run('docker', 'inspect', container))[0]
            if actual['Image'] != image or not actual['State']['Running']:
                raise ValueError('running image mismatch: ' + service)
            running[service] = actual['Image']
        receipt['running_images'] = running
        for _ in range(60):
            try:
                with urlopen('http://127.0.0.1:3002/health', timeout=3) as response:
                    payload = json.load(response)
                receipt['health'] = payload
                if healthy(payload):
                    receipt['status'] = 'passed'
                    return receipt
            except (OSError, ValueError):
                pass
            time.sleep(2)
        raise RuntimeError('gateway or backend health did not pass')
    except Exception as exc:
        receipt['error_type'] = type(exc).__name__
        raise
    finally:
        receipt['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        save(receipt_path, receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build'); b.add_argument('--root', type=Path, default=Path.cwd()); b.add_argument('--out', type=Path, required=True)
    d = sub.add_parser('deploy'); d.add_argument('--bundle', type=Path, required=True)
    d.add_argument('--expected-source', required=True); d.add_argument('--receipt', type=Path, required=True)
    d.add_argument('--project', default='ai-platform')
    args = parser.parse_args()
    if args.command == 'build':
        build(args.root.resolve(), args.out.resolve())
    else:
        deploy(args.bundle.resolve(), args.expected_source, args.receipt.resolve(), args.project)


if __name__ == '__main__':
    main()
