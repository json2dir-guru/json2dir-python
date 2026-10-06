#!/usr/bin/env python3
# json2dir: create the directory tree a JSON document describes, in the current directory.
# Implements RFC J2D-1 (https://github.com/kitsunoff/awesome-json2dir/blob/main/spec/rfc-json2dir.md).
# Standard library only.
import json
import os
import stat
import sys


class Json2dirError(Exception):
    pass


def reject_constant(name):
    raise Json2dirError("input is not valid JSON: %s is not allowed" % name)


# Section 3: strict UTF-8 and strict JSON. A leading BOM is ignored, as section 3 allows.
def parse(data):
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise Json2dirError("input is not valid UTF-8")
    try:
        # object_pairs_hook=dict: duplicate names keep the last value, as section 4.2 allows.
        return json.loads(text, object_pairs_hook=dict, parse_constant=reject_constant)
    except ValueError as e:
        raise Json2dirError("input is not valid JSON: %s" % e)


def encode(s, where):
    # Section 3.5: a string with an unpaired surrogate cannot be encoded as UTF-8.
    try:
        return s.encode("utf-8")
    except UnicodeEncodeError:
        raise Json2dirError("%s: string contains an unpaired surrogate" % where)


def check_name(name, where):
    if name in ("", ".", "..") or "/" in name or "\0" in name:
        raise Json2dirError("%s: invalid name %s" % (where, json.dumps(name)))


# Sections 4 and 6: validate the whole document before touching the file system.
def validate(value, where):
    if isinstance(value, str):
        encode(value, where)
    elif isinstance(value, list):
        if len(value) != 2 or not isinstance(value[0], str) or not isinstance(value[1], str):
            raise Json2dirError('%s: an array must be ["link", target] or ["script", content]' % where)
        if value[0] not in ("link", "script"):
            raise Json2dirError("%s: unknown array kind %s" % (where, json.dumps(value[0])))
        if value[0] == "link" and "\0" in value[1]:
            raise Json2dirError("%s: a link target cannot contain NUL" % where)
        encode(value[1], where)
    elif isinstance(value, dict):
        for name, child in value.items():
            path = name if where == "." else where + "/" + name
            encode(name, path)
            check_name(name, path)
            validate(child, path)
    else:
        kind = "null" if value is None else type(value).__name__
        raise Json2dirError("%s: %s values are not allowed" % (where, kind))


def lstat_or_none(p):
    try:
        return os.lstat(p)
    except FileNotFoundError:
        return None


# Sections 5.2, 5.3: an existing non-directory is removed (a symlink itself, never its target);
# section 5.4: a directory in the way of a non-object is an error.
def clear(p, existing):
    if existing is None:
        return
    if stat.S_ISDIR(existing.st_mode):
        raise Json2dirError("%s: a directory is in the way" % p.decode("utf-8"))
    os.unlink(p)


def write_file(p, content, executable):
    # O_EXCL: never writes through an entry that appeared after clear().
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o666)
    try:
        view = memoryview(content)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        if executable:
            os.fchmod(fd, stat.S_IMODE(os.fstat(fd).st_mode) | 0o111)
    finally:
        os.close(fd)


def apply(directory, tree):
    # Members in ascending order of the UTF-8 bytes of their names.
    for bname, value in sorted((k.encode("utf-8"), v) for k, v in tree.items()):
        p = os.path.join(directory, bname)
        existing = lstat_or_none(p)
        if isinstance(value, str):
            clear(p, existing)
            write_file(p, value.encode("utf-8"), False)
        elif isinstance(value, list):
            clear(p, existing)
            if value[0] == "link":
                os.symlink(value[1].encode("utf-8"), p)
            else:
                write_file(p, value[1].encode("utf-8"), True)
        else:
            if existing is None or not stat.S_ISDIR(existing.st_mode):
                if existing is not None:
                    os.unlink(p)
                os.mkdir(p)
            apply(p, value)


def main(args):
    if args:
        sys.stderr.write("usage: json2dir < document.json\n")
        return 2
    try:
        document = parse(sys.stdin.buffer.read())
        if not isinstance(document, dict):
            raise Json2dirError("the root of the document must be an object")
        validate(document, ".")
        apply(b".", document)
        return 0
    except Json2dirError as e:
        sys.stderr.write("json2dir: %s\n" % e)
    except OSError as e:
        sys.stderr.write("json2dir: %s\n" % e)
    except RecursionError:
        sys.stderr.write("json2dir: document is nested too deeply\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
