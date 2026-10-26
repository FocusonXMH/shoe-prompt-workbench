"""Read-only structural audit; reports candidates, never deletes business data."""
import ast
import collections
import pathlib
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]

class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.append(attrs['id'])

def main():
    page = Page()
    page.feed((ROOT / 'frontend/index.html').read_text(encoding='utf-8'))
    duplicate_ids = [k for k, v in collections.Counter(page.ids).items() if v > 1]
    routes = collections.defaultdict(list)
    duplicates = []
    files = list((ROOT / 'backend').glob('*.py'))
    for file in files:
        tree = ast.parse(file.read_text(encoding='utf-8-sig'), filename=str(file))
        names = collections.Counter(n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        duplicates.extend(f'{file.name}:{k}' for k, v in names.items() if v > 1)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute) and deco.func.attr in {'get', 'post', 'put', 'patch', 'delete'} and deco.args and isinstance(deco.args[0], ast.Constant):
                    routes[(deco.func.attr.upper(), deco.args[0].value)].append(f'{file.name}:{node.lineno}')
    duplicate_routes = {str(k): v for k, v in routes.items() if len(v) > 1}
    print(f'HTML IDs: {len(page.ids)}; Python files parsed: {len(files)}; routes: {len(routes)}')
    print('Duplicate HTML IDs:', duplicate_ids)
    print('Duplicate top-level Python declarations:', duplicates)
    print('Duplicate HTTP method/path:', duplicate_routes)
    if duplicate_ids or duplicates or duplicate_routes:
        raise SystemExit(1)

if __name__ == '__main__':
    main()
