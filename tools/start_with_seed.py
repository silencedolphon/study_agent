"""Start the dashboard after preparing data when no existing local library exists."""
import os
from pathlib import Path
import subprocess
import sys

from prepare_seed import PROJECT_ROOT, data_home, has_existing_local_library, prepare

if __name__ == '__main__':
    explicit_home = os.environ.get('KB_HOME', '').strip()
    if explicit_home or not has_existing_local_library():
        target = data_home()
        prepare(target)
        os.environ['KB_HOME'] = str(target)
    else:
        print('Using the existing local library.', flush=True)
    raise SystemExit(subprocess.call([sys.executable, '-X', 'utf8', str(PROJECT_ROOT / 'tools' / 'dashboard.py'), *sys.argv[1:]]))
