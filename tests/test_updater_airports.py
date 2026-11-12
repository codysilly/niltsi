"""Offline configuration checks: parse source only; never import or run updaters."""
import ast
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def tree(name):
    path = ROOT / 'scripts' / name
    source = path.read_bytes()
    compile(source, str(path), 'exec')  # Syntax check without execution.
    return ast.parse(source, filename=str(path))


def assigned(node, name):
    for item in ast.walk(node):
        if isinstance(item, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in item.targets
        ):
            return ast.literal_eval(item.value)
    raise AssertionError(f'Missing assignment: {name}')


class UpdaterAirportTests(unittest.TestCase):
    def test_metar_allowlist_matches_query_and_preserves_endpoint(self):
        module = tree('update_metar.py')
        airports = assigned(module, 'AIRPORTS')
        self.assertEqual(airports, {'KORD', 'KLAX', 'KJFK', 'KMEM'})
        url = urlsplit(assigned(module, 'URL'))
        self.assertEqual((url.scheme, url.netloc, url.path),
                         ('https', 'aviationweather.gov', '/api/data/metar'))
        self.assertEqual(parse_qs(url.query), {
            'ids': ['KORD,KLAX,KJFK,KMEM'], 'format': ['json']})

    def test_atis_excludes_unverified_kmem_and_preserves_existing_airports(self):
        module = tree('update_atis.py')
        self.assertEqual(assigned(module, 'AIRPORTS'),
                         ('KORD', 'KLAX', 'KJFK'))
        self.assertEqual(assigned(module, 'URL'), 'https://atis.info/api/')

    def test_runways_include_mem_in_selection_and_completeness_check(self):
        module = tree('update_runways.py')
        self.assertEqual(assigned(module, 'AIRPORTS'),
                         {'ORD', 'LAX', 'JFK', 'MEM'})
        generate = next(item for item in module.body
                        if isinstance(item, ast.FunctionDef) and item.name == 'generate')
        self.assertEqual(assigned(generate, 'expected'),
                         {'ORD': 16, 'LAX': 8, 'JFK': 8, 'MEM': 8})


if __name__ == '__main__':
    unittest.main()
