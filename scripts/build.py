"""
scripts/build.py
Empaqueta cada Lambda + shared/ + dependencias en un ZIP para CloudFormation.

Genera en dist/:
  token-refresher.zip
  message-poller.zip
  message-dispatcher.zip
  message-sender.zip

Cada ZIP contiene:
  handler.py            (raiz - Handler: handler.lambda_handler)
  shared/               (modulos compartidos)
  cryptography/, ...    (dependencias pip)

Nota: boto3 ya viene en el runtime de Lambda - no se empaqueta.

Uso: python scripts/build.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DIST = ROOT / 'dist'
BUILD = ROOT / '.build'

LAMBDAS = {
    'token-refresher':    'token_refresher',
    'message-poller':     'message_poller',
    'message-dispatcher': 'message_dispatcher',
    'message-sender':     'message_sender',
}

# boto3/botocore vienen en el runtime - solo empaquetamos cryptography
DEPS = ['cryptography>=42.0.0']


def build_lambda(zip_name: str, module_dir: str, deps_dir: Path):
    staging = BUILD / zip_name
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    # 1. Handler en la raiz del ZIP
    shutil.copy(ROOT / 'lambdas' / module_dir / 'handler.py', staging / 'handler.py')

    # 2. Modulos compartidos
    shutil.copytree(ROOT / 'shared', staging / 'shared')

    # 3. Dependencias pip (pre-instaladas en deps_dir)
    for item in deps_dir.iterdir():
        if item.name.endswith('.dist-info') or item.name == '__pycache__':
            continue
        dest = staging / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy(item, dest)

    # 4. Crear ZIP
    zip_path = DIST / zip_name
    if zip_path.with_suffix('.zip').exists():
        zip_path.with_suffix('.zip').unlink()
    shutil.make_archive(str(zip_path), 'zip', staging)

    size_kb = (DIST / f'{zip_name}.zip').stat().st_size // 1024
    print(f'  OK {zip_name}.zip  ({size_kb} KB)')


def main():
    DIST.mkdir(exist_ok=True)
    BUILD.mkdir(exist_ok=True)

    print('\nInstalando dependencias para Lambda (manylinux)...')
    deps_dir = BUILD / 'deps'
    if deps_dir.exists():
        shutil.rmtree(deps_dir)
    deps_dir.mkdir(parents=True)

    # --platform para binarios compatibles con Lambda (x86_64)
    subprocess.run([
        sys.executable, '-m', 'pip', 'install',
        *DEPS,
        '--target', str(deps_dir),
        '--platform', 'manylinux2014_x86_64',
        '--python-version', '3.12',
        '--only-binary', ':all:',
        '--quiet',
    ], check=True)
    print('  Dependencias instaladas OK\n')

    print('Building Swift SQS Connector Lambdas (Python)...\n')
    for zip_name, module_dir in LAMBDAS.items():
        build_lambda(zip_name, module_dir, deps_dir)

    shutil.rmtree(BUILD)
    print(f'\nOK Build complete -> dist/\n')


if __name__ == '__main__':
    main()
