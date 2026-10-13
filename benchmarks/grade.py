"""External outcome tests. Runs on an artifact copy after conversation execution."""
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

root, responses, case = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
results = {}


def criterion(key, test):
    try:
        test()
        results[key] = {'passed': True, 'evidence': test.__doc__ or key}
    except Exception as exc:
        results[key] = {'passed': False, 'evidence': type(exc).__name__ + ': ' + (str(exc) or key)}


def module(name):
    # Load application-local helpers exactly as a normal invocation from app/ does.
    # Register the module for annotation/dataclass resolution during execution.
    sys.path.insert(0, str(root / 'app'))
    spec = importlib.util.spec_from_file_location('subject', root / 'app' / name)
    value = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = value
    spec.loader.exec_module(value)
    return value


def persistence():
    """Two saved notes reload with stable distinct IDs and original text."""
    value = module('notes.py')
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'nested/notes.json'
        first = value.add(path, 'one')
        second = value.add(path, 'two')
        assert first == {'id': 1, 'text': 'one'}
        assert second == {'id': 2, 'text': 'two'}
        assert module('notes.py').list_notes(path) == [first, second]


def empty_unicode():
    """A missing file returns an empty list; Unicode and surrounding spaces survive persistence."""
    value = module('notes.py')
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'notes.json'
        assert value.list_notes(path) == []
        text = '  한글 📝 café  '
        value.add(path, text)
        assert value.list_notes(path)[0]['text'] == text


def invalid_unchanged():
    """Invalid text and corrupt JSON raise ValueError while retaining original bytes."""
    value = module('notes.py')
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'notes.json'
        original = b'[{"id": 3, "text": "saved"}]'
        for text in ('', ' \n\t', None, 12):
            path.write_bytes(original)
            try:
                value.add(path, text)
            except ValueError:
                pass
            else:
                raise AssertionError('Invalid text accepted')
            assert path.read_bytes() == original
        path.write_bytes(b'{broken')
        try:
            value.add(path, 'valid')
        except ValueError:
            pass
        else:
            raise AssertionError('Corrupt JSON accepted')
        assert path.read_bytes() == b'{broken'


def ids_text():
    """Sparse IDs advance from the maximum, original text is kept, and parents are created."""
    value = module('notes.py')
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'notes.json'
        existing = [{'id': 2, 'text': 'a'}, {'id': 7, 'text': 'b'}]
        path.write_text(json.dumps(existing))
        added = value.add(path, '  한글  ')
        assert added == {'id': 8, 'text': '  한글  '}
        assert value.list_notes(path) == existing + [added]
        value.add(Path(temp) / 'nested/new.json', 'ok')


def facts():
    """The answer identifies TTL 180, expiration at the boundary and source-hash equality."""
    answer = json.loads((root / 'app/answer.json').read_text())
    assert type(answer['ttl_seconds']) is int and answer['ttl_seconds'] == 180
    assert answer['expires_at_boundary'] is True and answer['compares_source_hash'] is True
    assert isinstance(answer['explanation'], str) and answer['explanation'].strip()
    assert isinstance(answer['limitation'], str) and answer['limitation'].strip()


def commands():
    """README commands match usage.json, run unchanged and return the documented Korean note."""
    commands = json.loads((root / 'app/usage.json').read_text())['commands']
    readme = (root / 'app/README.md').read_text().replace('\\\n', ' ')
    documented = [shlex.split(line.strip()) for line in readme.splitlines() if line.strip().startswith('python3 ')]
    assert len(commands) >= 2
    # The fixture starts without demo data. Agent verification may have created it;
    # reset only this grader's copy before replaying the documented sequence.
    (root / 'app/demo.json').unlink(missing_ok=True)
    for index, command in enumerate(commands):
        argv = command['argv']
        assert isinstance(argv, list) and all(isinstance(x, str) for x in argv)
        assert argv[:2] == ['python3', 'notes.py']
        assert argv in documented
        assert argv[2] == ('add' if index == 0 else 'list')
        assert argv[argv.index('--file') + 1] == 'demo.json'
        result = subprocess.run([sys.executable, *argv[1:]], cwd=root / 'app', capture_output=True, timeout=5)
        assert result.returncode == 0, result.stderr.decode()
        assert json.loads(result.stdout) == command['expected'] and command['expected']
        assert any('\uac00' <= c <= '\ud7a3' for text in command['expected'] for c in text)


def conversion():
    """Integer, fractional, zero and negative values convert using sixty seconds per minute."""
    value = module('convert.py')
    for minutes in (0, 1, 2, -3, 1.5, -0.25):
        assert value.seconds(minutes) == minutes * 60


def refresh():
    """The first artifact keeps the old value; the second uses the changed setting and Korean preference."""
    first = json.loads((root / 'alpha/facts.json').read_text())
    second = json.loads((root / 'beta/report.json').read_text())
    assert first['ttl_seconds'] == 60 and second['ttl_seconds'] == 120
    assert second['language'] == 'ko'
    assert first['explanation'].strip() and second['explanation'].strip()
    assert json.loads((root / 'alpha/settings.json').read_text())['ttl_seconds'] == 120


for key, test in {
    'notes-basic': [('persistence', persistence), ('empty_unicode', empty_unicode)],
    'notes-errors': [('invalid_unchanged', invalid_unchanged), ('ids_text', ids_text)],
    'source-explanation': [('facts', facts)], 'readme-contract': [('commands', commands)],
    'small-change': [('conversion', conversion)], 'session-refresh': [('refresh', refresh)]
}[case]:
    criterion(key, test)
print(json.dumps(results, ensure_ascii=False))
