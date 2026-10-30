"""Keep reviewable Python sources and deployable inline Lambda code identical."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'aws/amplify.yaml'


def synchronize(check=False):
    original = TEMPLATE.read_text(encoding='utf-8')
    updated = original
    for resource, filename in [('ChatFunction', 'chat_handler.py'),
                               ('SessionFunction', 'session_handler.py'),
                               ('SessionAuthorizerFunction', 'session_handler.py')]:
        source = (ROOT / 'aws' / filename).read_text(encoding='utf-8')
        pattern = rf'(  {resource}:\n.*?      Code:\n        ZipFile: \|\n).*?(?=\n  \w+:)'
        updated, count = re.subn(pattern,
            lambda match: match[1] + ''.join(('          ' + line if line else '') + '\n' for line in source.splitlines()),
            updated, count=1, flags=re.S)
        if count != 1:
            raise RuntimeError('Missing inline code resource: ' + resource)
    if check:
        if updated != original:
            raise SystemExit('Inline Lambda code is stale. Run python scripts/sync_chat_code.py')
    else:
        TEMPLATE.write_text(updated, encoding='utf-8')


if __name__ == '__main__':
    synchronize('--check' in sys.argv)
