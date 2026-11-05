"""File tools and command launch, executed INSIDE the unprivileged sandbox."""
import base64
import json
import os
from pathlib import PurePosixPath
import stat
import sys

LIMIT = 1024 * 1024


def directory(path):
    if path.startswith('/') or '\\' in path or '\x00' in path or '..' in path.split('/'):
        raise ValueError('invalid_path')
    fd = os.open('/work', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in PurePosixPath(path).parts:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def main(command):
    if command['name'] == 'RunCommand':
        fd = directory(command['cwd'])
        try:
            os.fchdir(fd)
        finally:
            os.close(fd)
        os.execvpe(command['argv'][0], command['argv'], os.environ)
    path = command['path']
    if path.startswith('/') or '\\' in path or '\x00' in path or '..' in path.split('/'):
        raise ValueError('invalid_path')
    parts = PurePosixPath(path)
    parent = directory(str(parts.parent))
    try:
        flags = os.O_NOFOLLOW | os.O_NONBLOCK
        if command['name'] == 'ReadFile':
            fd = os.open(parts.name, os.O_RDONLY | flags, dir_fd=parent)
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('not_regular_file')
                data = stream.read(LIMIT + 1)
                if len(data) > LIMIT:
                    raise ValueError('file_too_large')
            print(base64.b64encode(data).decode())
        else:
            data = base64.b64decode(command['content_base64'], validate=True)
            if len(data) > LIMIT:
                raise ValueError('file_too_large')
            # Do not truncate until type is verified; no FIFOs/devices/symlinks.
            fd = os.open(parts.name, os.O_WRONLY | os.O_CREAT | flags, 0o600, dir_fd=parent)
            with os.fdopen(fd, 'wb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('not_regular_file')
                stream.truncate(0)
                stream.write(data)
    finally:
        os.close(parent)


if __name__ == '__main__':
    try:
        main(json.loads(''.join(sys.argv[1:])))
    except (OSError, ValueError) as exc:
        # Stable tool errors, never tracebacks containing request contents.
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        sys.exit(1)
