"""Build a bounded archive via file descriptors, without following links."""
import io
import os
import stat
import sys
import tarfile

LIMIT = 64 * 1024 * 1024
output = io.BytesIO()
count = 0


def visit(archive, folder, prefix):
    global count
    for name in sorted(os.listdir(folder)):
        count += 1
        if count > 10000:
            raise ValueError('artifact_too_large')
        info = os.stat(name, dir_fd=folder, follow_symlinks=False)
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('unsafe_artifact')
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        try:
            actual = os.fstat(fd)
            entry = tarfile.TarInfo(prefix+'/'+name)
            entry.mode = 0o600
            if stat.S_ISDIR(actual.st_mode):
                entry.type = tarfile.DIRTYPE
                entry.mode = 0o700
                archive.addfile(entry)
                visit(archive, fd, entry.name)
            elif stat.S_ISREG(actual.st_mode):
                if actual.st_size + output.tell() + 10240 > LIMIT:
                    raise ValueError('artifact_too_large')
                entry.size = actual.st_size
                with os.fdopen(os.dup(fd), 'rb') as stream:
                    archive.addfile(entry, stream)
            else:
                raise ValueError('unsafe_artifact')
            if output.tell() > LIMIT:
                raise ValueError('artifact_too_large')
        finally:
            os.close(fd)


try:
    root = os.open('/work', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with tarfile.open(fileobj=output, mode='w') as archive:
            visit(archive, root, 'work')
    finally:
        os.close(root)
    if output.tell() > LIMIT:
        raise ValueError('artifact_too_large')
    sys.stdout.buffer.write(output.getvalue())
except (ValueError, OSError, tarfile.TarError) as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(1)
