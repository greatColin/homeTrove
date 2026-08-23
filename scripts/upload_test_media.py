"""Upload test media via the hometrove chunked upload API."""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8080"


def req(method, path, body=None, ctype=None, query=None):
    url = BASE + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None
    headers = {}
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        else:
            data = body
    if ctype:
        headers["Content-Type"] = ctype
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:500]


def upload(path, plugins=None):
    import urllib.parse
    p = Path(path)
    size = p.stat().st_size
    payload = p.read_bytes()
    sha = hashlib.sha256(payload).hexdigest()

    st, session = req("POST", "/api/uploads", {
        "filename": p.name, "size": size,
        "content_hash": sha, "encrypted": False,
    })
    if st != 200:
        print(f"  [create] FAILED {st}: {session}")
        return
    uid = session["upload_id"]
    chunk_size = session["chunk_size"]

    import urllib.parse as _u
    boundary = "----ht-test-boundary"
    for idx, start in enumerate(range(0, size, chunk_size)):
        chunk = payload[start:start + chunk_size]
        body = (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="chunk.bin"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n").encode("latin-1") + chunk + b"\r\n--" + boundary.encode() + b"--\r\n"
        st, _ = req("PUT", f"/api/uploads/{uid}/chunks/{idx}", body,
                    ctype=f"multipart/form-data; boundary={boundary}")
        if st != 200:
            print(f"  [chunk {idx}] FAILED {st}")
            return

    st, _ = req("POST", f"/api/uploads/{uid}/complete", {"chunk_indices": list(range((size + chunk_size - 1) // chunk_size or 1))})
    if st != 200:
        print(f"  [complete] FAILED {st}: {_}")
        return

    q = None
    if plugins:
        q = {"plugin_ids": plugins}
    st, result = req("POST", f"/api/uploads/{uid}/ingest", query=q)
    print(f"  [ingest] {st}: {result}")
    return result


if __name__ == "__main__":
    for f in sys.argv[1:]:
        print(f"upload {f}")
        upload(f)
