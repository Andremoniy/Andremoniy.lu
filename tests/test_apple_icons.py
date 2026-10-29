"""Offline artwork checks for the build's image validation and catalogue updates."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
MODULE_SPECIFICATION = importlib.util.spec_from_file_location(
    'apple_icons', PROJECT_DIRECTORY / 'scripts/fetch_apple_icons.py',
)
ARTWORK_MODULE = importlib.util.module_from_spec(MODULE_SPECIFICATION)
MODULE_SPECIFICATION.loader.exec_module(ARTWORK_MODULE)


class ArtworkTests(unittest.TestCase):
    def test_supported_image_signatures_and_invalid_content(self):
        image_formats = (
            (b'\x89PNG\r\n\x1a\nimage', 'png'),
            (b'\xff\xd8\xffimage', 'jpg'),
            (b'RIFF1234WEBPimage', 'webp'),
        )
        for image_content, expected_extension in image_formats:
            with self.subTest(expected_extension=expected_extension):
                self.assertEqual(
                    expected_extension,
                    ARTWORK_MODULE.identify_image_extension(image_content, '123'),
                )
        with self.assertRaisesRegex(RuntimeError, 'Unexpected image bytes'):
            ARTWORK_MODULE.identify_image_extension(b'<html>Error</html>', '123')

    def test_catalogue_build_saves_verified_artwork_and_updates_every_reference(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            build_directory = Path(temporary_directory)
            catalogue_path = build_directory / 'apps.html'
            catalogue_path.write_text(
                '<a class="apple-artwork"><img data-app-id="123" src="old.png"></a>'
                '<img data-app-id="123" alt="Second icon">', encoding='utf-8',
            )
            lookup_response = (
                b'{"results":[{"trackId":123,"trackName":"App",'
                b'"artworkUrl512":"https://apple.example/icon"}]}'
            )
            image_content = b'\x89PNG\r\n\x1a\nimage'
            with patch.object(ARTWORK_MODULE, 'APPLICATION_SLUGS', {'123': 'app'}), patch.object(
                ARTWORK_MODULE, 'download_content', side_effect=[lookup_response, image_content],
            ):
                ARTWORK_MODULE.prepare_application_artwork(build_directory)
            updated_html = catalogue_path.read_text(encoding='utf-8')
            self.assertEqual(2, updated_html.count('src="assets/app-icons/app.png"'))
            self.assertIn('apple-artwork is-loaded', updated_html)
            self.assertEqual(image_content, (build_directory / 'assets/app-icons/app.png').read_bytes())

    def test_missing_application_or_image_node_is_rejected(self):
        with patch.object(ARTWORK_MODULE, 'download_content', return_value=b'{"results":[]}'):
            with self.assertRaisesRegex(RuntimeError, 'expected app ID'):
                ARTWORK_MODULE.find_application_record('123')
        with self.assertRaisesRegex(RuntimeError, 'No image nodes'):
            ARTWORK_MODULE.update_application_images('<p>No icon</p>', '123', 'icon.png')

    def test_insecure_artwork_url_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'HTTPS artwork URL'):
            ARTWORK_MODULE.find_artwork_url({'artworkUrl512': 'http://example.com/icon'}, '123')


if __name__ == '__main__':
    unittest.main()
