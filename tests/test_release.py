import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('release', Path(__file__).parents[1] / 'scripts/release/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def fixture(self, folder):
        source = 'a' * 40
        images = {name: 'sha256:' + '1' * 64 for name in (*release.SERVICES, 'postgres')}
        release.save(folder / 'compose.json', {'services': {name: {'image': image} for name, image in images.items()}})
        (folder / 'images.tar').write_bytes(b'synthetic archive, never sent to Docker')
        release.save(folder / 'manifest.json', {'schema': 1, 'source_revision': source,
            'execution_mode': 'synthetic_mock', 'images': images,
            'archive_sha256': release.sha(folder / 'images.tar'), 'compose_sha256': release.sha(folder / 'compose.json')})
        return source

    def test_integrity_then_tampered_archive_blocks(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name); source = self.fixture(folder)
            release.validate(folder, source)
            (folder / 'images.tar').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                release.validate(folder, source)

    def test_wrong_candidate_rejected_and_failure_receipt_written_before_docker(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name); self.fixture(folder)
            with self.assertRaisesRegex(ValueError, 'source revision'):
                release.deploy(folder, 'b' * 40, folder / 'receipt.json', 'test-release')
            self.assertEqual(json.loads((folder / 'receipt.json').read_text())['status'], 'failed')

    def test_mutable_image_rejected(self):
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name); source = self.fixture(folder)
            manifest = json.loads((folder / 'manifest.json').read_text())
            manifest['images']['gateway'] = 'gateway:latest'
            release.save(folder / 'manifest.json', manifest)
            with self.assertRaisesRegex(ValueError, 'immutable'):
                release.validate(folder, source)

    def test_http_200_degraded_payload_is_not_healthy(self):
        self.assertFalse(release.healthy({'status': 'degraded', 'services': [{'status': 'ok'}, {'status': 'error'}]}))
        self.assertFalse(release.healthy({'status': 'ok', 'services': []}))
        self.assertTrue(release.healthy({'status': 'ok', 'services': [{'status': 'ok'}, {'status': 'ok'}]}))


if __name__ == '__main__':
    unittest.main()
