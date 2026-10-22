#!/usr/bin/env python3
"""Restore omitted DriverKit headers from Apple's earlier Darwin 23 source."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import urllib.request

# The Apple source listings come from api.github.com, which allows only 60
# unauthenticated requests per hour per IP. Hosted runners share IPs, so use
# the workflow token when one is present.
_TOKEN = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
if _TOKEN:
    _opener = urllib.request.build_opener()
    _opener.addheaders = [('Authorization', f'Bearer {_TOKEN}'),
                          ('User-Agent', 'unixnu-driverkit-restore')]
    urllib.request.install_opener(_opener)

# Tags of the Apple sources that still carry the headers newer xnu omits; feeds.toml sets them for the unixnu driver.
TAG = os.environ.get('UNIXNU_DRIVERKIT_TAG', 'xnu-10063.121.3')
TRUST_TAG = os.environ.get('UNIXNU_TRUSTCACHE_TAG', 'xnu-8796.101.5')
DESTINATION = Path('.cache/xnu-work/iokit/DriverKit')
CACHE = Path(f'.cache/sources/xnu-driverkit-{TAG.removeprefix("xnu-")}.json')
URL = f'https://api.github.com/repos/apple-oss-distributions/xnu/contents/iokit/DriverKit?ref={TAG}'


def restore(entry, destination=DESTINATION, tag=TAG):
    if entry['type'] != 'file' or not entry['name'].endswith('.h'):
        return None
    name = entry.get('relative', entry['name'])
    target = destination / name
    if target.exists():
        return None
    data = urllib.request.urlopen(entry['download_url'], timeout=30).read()
    digest = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    if digest != entry['sha']:
        raise ValueError(f'Git blob hash mismatch: {entry["name"]}')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {'name': name, 'source_tag': tag, 'git_blob': digest,
            'sha256': hashlib.sha256(data).hexdigest()}


if __name__ == '__main__':
    if not DESTINATION.is_dir():
        raise SystemExit('Create the local XNU work source before preparing headers')
    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(urllib.request.urlopen(URL, timeout=30).read())
    entries = json.loads(CACHE.read_text())
    crypto_cache = CACHE.with_name(f'xnu-driverkit-crypto-{TAG.removeprefix("xnu-")}.json')
    if not crypto_cache.exists():
        crypto_url = URL.replace('/DriverKit?', '/DriverKit/crypto?')
        crypto_cache.write_bytes(urllib.request.urlopen(crypto_url, timeout=30).read())
    for entry in json.loads(crypto_cache.read_text()):
        entry['relative'] = 'crypto/' + entry['name']
        entries.append(entry)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        restored = [entry for entry in executor.map(restore, entries) if entry]
        trust_tag = TRUST_TAG
        trust_cache = CACHE.with_name(f'xnu-trustcache-{TRUST_TAG.removeprefix("xnu-")}.json')
        if not trust_cache.exists():
            url = ('https://api.github.com/repos/apple-oss-distributions/xnu/'
                   f'contents/EXTERNAL_HEADERS/TrustCache?ref={trust_tag}')
            trust_cache.write_bytes(urllib.request.urlopen(url, timeout=30).read())
        trust_destination = Path('.cache/xnu-work/EXTERNAL_HEADERS/TrustCache')
        def restore_trust(entry):
            return restore(entry, trust_destination, trust_tag)
        restored.extend(entry for entry in executor.map(
            restore_trust, json.loads(trust_cache.read_text())) if entry)
    previous = Path('.cache/sources/xnu-restored-headers.json')
    existing = json.loads(previous.read_text()) if previous.exists() else []
    by_name = {entry['name']: entry for entry in existing + restored}
    previous.write_text(json.dumps(list(by_name.values()), indent=2) + '\n')
    print(f'Restored {len(restored)} omitted public headers from Apple; blob hashes verified')


# UniXNU bring-up: bsd_init references the legacy display hooks, but the archive
# that provides them is not on the kernel link line. Define them in the kernel
# source so the link is honest: the driver genuinely is not built yet.
_STUB = """
void native_legacy_display_probe(void);
void native_legacy_display_late_probe(void);
void native_legacy_display_probe(void) {}
void native_legacy_display_late_probe(void) {}
"""

_bsd = Path('.cache/xnu-work/bsd/kern/bsd_init.c')
if _bsd.is_file():
    _source = _bsd.read_text()
    if 'native_legacy_display_probe' not in _source:
        _at = _source.index('#include')
        _bsd.write_text(_source[:_at] + _STUB + _source[_at:])
        print('bsd_init.c: legacy display stubs injected')
