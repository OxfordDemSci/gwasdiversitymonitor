from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from app import DataLoader


class TraitFilterPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'summary' / 'uniq_dis_trait.txt'
        self.path.parent.mkdir()
        self.path.write_text(
            'Height\nType 2 diabetes (adult)\nHeight\nCafé response\n\n'
            'HEIGHT\nBlood-pressure\n', encoding='utf-8',
        )
        self.loader = DataLoader.DataLoader(str(self.root))
        with DataLoader._trait_index_lock:
            DataLoader._trait_index_cache.clear()
        self.addCleanup(self.clear_cache)

    @staticmethod
    def clear_cache():
        with DataLoader._trait_index_lock:
            DataLoader._trait_index_cache.clear()

    def legacy_traits(self):
        return {
            name: name.lower().replace(' ', '-').replace('(', '').replace(')', '')
            for name in self.path.read_text().splitlines()
        }

    def test_search_results_preserve_legacy_values_order_and_matching(self):
        traits = self.legacy_traits()
        self.assertEqual(self.loader.getTraitsList(), traits)
        for query in ('', 'HEIGHT', 'type-2', '(adult)', 'CAFÉ', ' ', 'missing'):
            needle = query.lower()
            expected = [
                {'id': identifier, 'text': name}
                for name, identifier in traits.items()
                if needle in name.lower() or needle in identifier.lower()
            ]
            with self.subTest(query=query):
                self.assertEqual(self.loader.filterTraits(query), expected)

    def test_repeated_requests_and_loader_instances_read_source_once(self):
        with mock.patch('app.DataLoader.open', wraps=open) as source_open:
            self.loader.filterTraits('height')
            self.loader.filterTraits('diabetes')
            self.loader.getTraitsList()
            DataLoader.DataLoader(str(self.root)).filterTraits('height')
        source_open.assert_called_once_with(str(self.path))

    def test_returned_mappings_and_rows_do_not_mutate_cached_index(self):
        expected = self.loader.filterTraits('height')
        rows = self.loader.filterTraits('height')
        rows[0]['text'] = 'Changed'
        rows.append({'id': 'invented', 'text': 'Invented'})
        mapping = self.loader.getTraitsList()
        mapping.clear()
        self.assertEqual(self.loader.filterTraits('height'), expected)
        self.assertEqual(self.loader.getTraitsList(), self.legacy_traits())

    def test_new_contents_invalidate_cache_even_with_preserved_size_and_mtime(self):
        self.path.write_text('Height\n')
        self.assertEqual(self.loader.filterTraits('')[0]['text'], 'Height')
        original_stat = self.path.stat()
        self.path.write_text('Weight\n')
        os.utime(self.path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        self.assertEqual(self.path.stat().st_size, original_stat.st_size)
        self.assertEqual(self.path.stat().st_mtime_ns, original_stat.st_mtime_ns)
        self.assertEqual(self.loader.filterTraits(''), [
            {'id': 'weight', 'text': 'Weight'},
        ])

    def test_atomic_replacement_and_missing_file_do_not_reuse_old_index(self):
        self.path.write_text('Height\n')
        self.loader.filterTraits('')
        original_stat = self.path.stat()
        replacement = self.path.with_suffix('.replacement')
        replacement.write_text('Weight\n')
        os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        replacement.replace(self.path)
        self.assertNotEqual(self.path.stat().st_ino, original_stat.st_ino)
        self.assertEqual(self.loader.filterTraits('')[0]['text'], 'Weight')
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.loader.filterTraits('')

    def test_different_release_paths_are_independent_and_cache_is_bounded(self):
        expected = self.loader.filterTraits('')
        with mock.patch.object(DataLoader, 'TRAIT_INDEX_CACHE_LIMIT', 2):
            for number in range(2):
                release = self.root / f'release-{number}'
                (release / 'summary').mkdir(parents=True)
                (release / 'summary' / self.path.name).write_text(f'Release {number}\n')
                self.assertEqual(
                    DataLoader.DataLoader(str(release)).filterTraits(''),
                    [{'id': f'release-{number}', 'text': f'Release {number}'}],
                )
            self.assertEqual(len(DataLoader._trait_index_cache), 2)
            with mock.patch('app.DataLoader.open', wraps=open) as source_open:
                self.assertEqual(self.loader.filterTraits(''), expected)
            source_open.assert_called_once_with(str(self.path))
            self.assertEqual(len(DataLoader._trait_index_cache), 2)

    def test_concurrent_cold_searches_share_one_immutable_index(self):
        with mock.patch('app.DataLoader.open', wraps=open) as source_open:
            with ThreadPoolExecutor(max_workers=8) as workers:
                results = list(workers.map(self.loader.filterTraits, ['height'] * 16))
        source_open.assert_called_once_with(str(self.path))
        for result in results:
            self.assertEqual(result, results[0])
        self.assertIsNot(results[0], results[1])
        self.assertIsNot(results[0][0], results[1][0])


if __name__ == '__main__':
    unittest.main()
