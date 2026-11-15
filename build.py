"""Rebuild a pinned portable foobar2000 without reading the original installation."""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
QAAC_ENCODER = './encoders/qaac/qaac64.exe'
DEFAULT_QAAC_SOURCE = Path(r'C:\Tools\OKEGui\tools\qaac')
COMPONENTS = ['foo_cuesheet_creator', 'foo_dr_meter', 'foo_dsd_processor',
              'foo_input_dvda', 'foo_input_ffmpeg', 'foo_input_sacd',
              'foo_input_tak', 'foo_input_tta']
CORE_COMPONENTS = ['foo_converter.dll', 'foo_dsp_eq.dll', 'foo_dsp_std.dll',
                   'foo_fileops.dll', 'foo_freedb2.dll', 'foo_input_std.dll',
                   'foo_ui_std.dll', 'foo_unpack.dll']
PRESETS = ['aac_ent', 'alac_d.nn', 'flac_d.nn', 'flac_ent', 'flac_sep', 'wv_sep']
APP_FILES = ['CoreAudioToolbox.dll', 'CoreFoundation.dll', 'ASL.dll',
             'libdispatch.dll', 'objc.dll', 'libicuin.dll', 'libicuuc.dll']


def log(s):
    print(s, flush=True)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while b := f.read(1024 * 1024):
            h.update(b)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def sized_replace(data, old, new):
    """Replace a length-prefixed UTF-8 string, never a raw binary substring."""
    old, new = old.encode('utf-8'), new.encode('utf-8')
    needle = struct.pack('<I', len(old)) + old
    if data.count(needle) != 1:
        raise ValueError('Expected exactly one sized encoder path')
    return data.replace(needle, struct.pack('<I', len(new)) + new)


def transform_formats(blob, old, new):
    count, = struct.unpack_from('<I', blob)
    pos, records, changes = 4, [], 0
    for _ in range(count):
        guid = blob[pos:pos+16]
        size, = struct.unpack_from('<I', blob, pos+16)
        payload = blob[pos+20:pos+20+size]
        if len(payload) != size:
            raise ValueError('Truncated converter format')
        if old.encode('utf-8') in payload:
            payload = sized_replace(payload, old, new)
            changes += 1
        records.append(guid + struct.pack('<I', len(payload)) + payload)
        pos += 20 + size
    if pos != len(blob) or changes != 1:
        raise ValueError('Unexpected converter format structure')
    return struct.pack('<I', count) + b''.join(records)


def seven_zip():
    for p in [shutil.which('7z'), shutil.which('7zz'),
              os.environ.get('ProgramFiles', r'C:\Program Files') + r'\7-Zip\7z.exe']:
        if p and Path(p).is_file():
            return str(p)
    raise RuntimeError('7-Zip is required. Install 7-Zip or add 7z.exe to PATH.')


def extract(archive, destination, exe):
    destination.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run([exe, 'x', str(archive), '-o'+str(destination), '-y', '-bso0', '-bsp0'],
                          capture_output=True, text=True, errors='replace')
    if proc.returncode:
        raise RuntimeError(f'Cannot extract {archive.name}: {proc.stderr or proc.stdout}')


def copy_tree_files(source, target):
    for p in sorted(source.rglob('*')):
        if p.is_file():
            out = target / p.relative_to(source)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, out)


def qaac_bundle_inventory(directory):
    if not directory.is_dir():
        raise ValueError('OKE qaac source directory not found: '+str(directory))
    files = {}
    for p in sorted(directory.rglob('*')):
        if p.is_symlink():
            raise ValueError('Linked files are not supported in the qaac bundle: '+str(p))
        if p.is_file():
            files[p.relative_to(directory).as_posix()] = sha(p)
    for name in ['qaac64.exe', 'CoreAudioToolbox.dll', 'libsoxr64.dll']:
        if name not in files:
            raise ValueError('OKE qaac bundle is missing required file: '+name)
    if pe_machine(directory/'qaac64.exe') != 0x8664:
        raise ValueError('OKE qaac64.exe is not x64')
    return files


def cached_source(source, cache, offline):
    name = source['file']
    if Path(name).name != name:
        raise ValueError('Unsafe cache filename')
    dest = cache/name
    if dest.is_file():
        if sha(dest) == source['sha256']:
            log('Cache OK: '+source['id'])
            return dest
        raise RuntimeError(f'Cache hash mismatch: {dest}. Move this file aside and retry.')
    if offline:
        raise RuntimeError('Missing offline source: '+str(dest))
    partial = cache/(name+'.partial')
    log('Downloading: '+source['id'])
    # curl follows HTTPS redirects; its connect/transfer deadlines avoid hung builds.
    urls = [source['url']] + source.get('fallback_urls', [])
    for url in urls:
        if not url.startswith('https://'):
            raise ValueError('Only HTTPS downloads are allowed')
        result = subprocess.run(['curl.exe', '--fail', '--location', '--retry', '2',
                                 '--connect-timeout', '30', '--max-time', '900',
                                 '--silent', '--show-error', '--output', str(partial), url])
        if result.returncode == 0 and partial.is_file() and sha(partial) == source['sha256']:
            partial.replace(dest)
            log('Download verified: '+source['id'])
            return dest
    raise RuntimeError(f'Download failed or SHA256 mismatch: {name}; no files installed')


def install_component(archive, ident, destination):
    data = archive.read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if archive.suffix == '.zip':
            inner = [n for n in z.namelist() if Path(n).name == ident+'.fb2k-component']
            if len(inner) != 1:
                raise ValueError('Missing nested component: '+ident)
            data = z.read(inner[0])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        selected = [n for n in z.namelist() if n.replace('\\', '/').startswith('x64/') and not n.endswith('/')]
        if not selected:
            raise ValueError('No x64 component payload: '+ident)
        for n in selected:
            rel = Path(n.replace('\\', '/')).relative_to('x64')
            if '..' in rel.parts or rel.is_absolute():
                raise ValueError('Unsafe component path')
            out = destination/rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(n))
    if not (destination/(ident+'.dll')).is_file():
        raise ValueError('Component DLL not installed: '+ident)


def apple_dependencies(archive, temp, target, exe):
    # Apple's bootstrapper embeds a CAB; no iTunes installation or registry change.
    with archive.open('rb') as f:
        header = f.read(16*1024*1024)
        start = 0
        while True:
            offset = header.find(b'MSCF', start)
            if offset < 0:
                raise RuntimeError('Apple installer CAB not found')
            size = struct.unpack_from('<I', header, offset+8)[0]
            if size > 1024*1024 and offset+size <= archive.stat().st_size:
                break
            start = offset+4
        f.seek(offset)
        cab = temp/'apple.cab'
        with cab.open('wb') as out:
            remaining = size
            while remaining:
                b = f.read(min(1024*1024, remaining))
                if not b:
                    raise ValueError('Truncated Apple CAB')
                out.write(b)
                remaining -= len(b)
    cabdir = temp/'apple-cab'
    extract(cab, cabdir, exe)
    msis = list(cabdir.rglob('iTunes64.msi'))
    if len(msis) != 1:
        raise RuntimeError('iTunes64.msi not found')
    msidir = temp/'apple-msi'
    extract(msis[0], msidir, exe)
    target.mkdir(parents=True, exist_ok=True)
    mapping = msi_file_names(msis[0])
    selected = []
    for p in msidir.rglob('*'):
        if not p.is_file():
            continue
        # Read MSI File-table keys to restore the original long filenames.
        name = mapping.get(p.name, p.name)
        if name in APP_FILES or name.startswith(('icudt', 'msvcp', 'msvcr', 'vcruntime')) and name.lower().endswith('.dll'):
            selected.append((p, name))
    for p, name in selected:
        if Path(name).name != name:
            raise ValueError('Unsafe MSI filename')
        shutil.copy2(p, target/name)
    for name in APP_FILES:
        if not (target/name).is_file():
            raise RuntimeError('Apple DLL not extracted: '+name)
    if not list(target.glob('icudt*.dll')):
        raise RuntimeError('Apple ICU data DLL missing')


def msi_file_names(path):
    """Read the Windows Installer database without executing an installation."""
    msi = ctypes.WinDLL('msi')
    handle = ctypes.c_uint32
    msi.MsiOpenDatabaseW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(handle)]
    msi.MsiDatabaseOpenViewW.argtypes = [handle, ctypes.c_wchar_p, ctypes.POINTER(handle)]
    msi.MsiViewExecute.argtypes = [handle, handle]
    msi.MsiViewFetch.argtypes = [handle, ctypes.POINTER(handle)]
    msi.MsiRecordGetStringW.argtypes = [handle, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
    msi.MsiCloseHandle.argtypes = [handle]
    db, view = handle(), handle()
    def check(status):
        if status != 0:
            raise RuntimeError('MSI database read failed: '+str(status))
    def field(record, index):
        buf = ctypes.create_unicode_buffer(4096)
        size = ctypes.c_uint32(4096)
        check(msi.MsiRecordGetStringW(record, index, buf, ctypes.byref(size)))
        return buf.value
    check(msi.MsiOpenDatabaseW(str(path), None, ctypes.byref(db)))
    try:
        check(msi.MsiDatabaseOpenViewW(db, 'SELECT `File`, `FileName` FROM `File`', ctypes.byref(view)))
        check(msi.MsiViewExecute(view, 0))
        mapping = {}
        while True:
            record = handle()
            status = msi.MsiViewFetch(view, ctypes.byref(record))
            if status == 259:  # ERROR_NO_MORE_ITEMS
                break
            check(status)
            try:
                mapping[field(record, 1)] = field(record, 2).split('|')[-1]
            finally:
                msi.MsiCloseHandle(record)
        return mapping
    finally:
        if view.value:
            msi.MsiCloseHandle(view)
        msi.MsiCloseHandle(db)


def install_profile(stage, target, output, output_mode='fixed'):
    template = ROOT/'config-template'
    for rel, digest in read_json(template/'manifest.json').items():
        if sha(template/rel) != digest:
            raise ValueError('Configuration template hash mismatch: '+rel)
    profile = stage/'profile'
    profile.mkdir(exist_ok=True)
    copy_tree_files(template/'files', profile)
    data = read_json(template/'config.json')
    c = sqlite3.connect(profile/'config.sqlite')
    for table in data:
        kind = {'configStrings':'TEXT','configReals':'REAL','configBlobs':'BLOB'}.get(table,'INTEGER')
        c.execute(f'CREATE TABLE {table} (name TEXT UNIQUE PRIMARY KEY NOT NULL, value {kind})')
        for key, value in data[table]:
            if table == 'configBlobs':
                value = base64.b64decode(value)
                if key == 'converter.formats':
                    value = transform_formats(value, '@QAAC_EXE@', QAAC_ENCODER)
            c.execute(f'INSERT INTO {table} VALUES (?,?)', (key,value))
    c.commit()
    c.execute('VACUUM')
    c.close()
    presets = profile/'converter presets'
    presets.mkdir(exist_ok=True)
    for p in (template/'presets').glob('*.ini'):
        lines = []
        for line in p.read_text(encoding='utf-8-sig').splitlines():
            if line.startswith('encodingParams='):
                blob = base64.b64decode(line.split('=',1)[1])
                if b'@QAAC_EXE@' in blob:
                    blob = sized_replace(blob, '@QAAC_EXE@', QAAC_ENCODER)
                line = 'encodingParams='+base64.b64encode(blob).decode()
            else:
                line = line.replace('@OUTPUT_DIR@', str(output))
                if p.stem in PRESETS:
                    if line.startswith('outputFolder='):
                        line = 'outputFolder='+('{ask}' if output_mode == 'ask' else str(output))
            lines.append(line)
        if p.stem in PRESETS and output_mode == 'ask' and not any(line.startswith('outputFolderLastUsed=') for line in lines):
            lines.append('outputFolderLastUsed='+str(output))
        (presets/p.name).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    # Keep the integrity-protected legacy component config byte-for-byte intact.
    # An empty FFmpeg folder uses the application directory before global PATH.
    (profile/'version.txt').write_text('foobar2000 v2.26.2\n',encoding='utf-8')
    (stage/'portable_mode_enabled').touch()
    (stage/'installer.ini').write_text('[foobar2000]\nversion=2.26.2\nrun_after_install=0\n[Header]\nPrevInstallInfoVersion=1\nEqMigrated=1\n[InstalledComponents]\nfoo_dsp_eq=1\nfoo_converter=1\nfoo_fileops=1\nfoo_unpack=1\nfoo_freedb2=1\n',encoding='utf-8')


def pe_machine(path):
    with path.open('rb') as f:
        if f.read(2) != b'MZ':
            raise ValueError('Not PE: '+str(path))
        f.seek(60)
        pos = struct.unpack('<I', f.read(4))[0]
        f.seek(pos)
        if f.read(4) != b'PE\0\0':
            raise ValueError('Invalid PE signature')
        return struct.unpack('<H', f.read(2))[0]


def validate_layout(stage, target, output, output_mode=None):
    if pe_machine(stage/'foobar2000.exe') != 0x8664:
        raise ValueError('Core is not x64')
    actual = sorted(p.name for p in (stage/'profile/user-components-x64').iterdir())
    if actual != sorted(COMPONENTS):
        raise ValueError('Unexpected component list: '+repr(actual))
    for name in COMPONENTS:
        if pe_machine(stage/f'profile/user-components-x64/{name}/{name}.dll') != 0x8664:
            raise ValueError('Wrong architecture: '+name)
    # Inspect known runtime-state locations, not arbitrary user directory names.
    excluded = {'metadb.sqlite', 'config.sqlite.bad', 'component-updates',
                'accuraterip', 'playlists-v2', 'playlists-v2.0'}
    for p in (stage/'profile').iterdir():
        if p.name.casefold() in excluded:
            raise ValueError('Excluded state in artifact: '+str(p))
    if (stage/'profile/converter presets/[last used].ini').exists():
        raise ValueError('Last used conversion preset must not be bundled')
    for name in PRESETS:
        text = (stage/f'profile/converter presets/{name}.ini').read_text(encoding='utf-8-sig')
        values = dict(line.split('=',1) for line in text.splitlines() if '=' in line)
        if values['outputFolder'] not in ['{ask}', str(output)]:
            raise ValueError('Wrong output folder')
        if 'outputFolderLastUsed' in values and values['outputFolderLastUsed'] != str(output):
            raise ValueError('Wrong last used output folder in '+name)
        if output_mode is not None:
            expected = '{ask}' if output_mode == 'ask' else str(output)
            if values['outputFolder'] != expected:
                raise ValueError('Wrong output mode in '+name)
            if output_mode == 'ask' and values.get('outputFolderLastUsed') != str(output):
                raise ValueError('Wrong last used output folder in '+name)
    aac = dict(line.split('=',1) for line in (stage/'profile/converter presets/aac_ent.ini').read_text(encoding='utf-8-sig').splitlines() if '=' in line)
    blob = base64.b64decode(aac['encodingParams'])
    if QAAC_ENCODER.encode('utf-8') not in blob:
        raise ValueError('AAC path not adapted')
    if pe_machine(stage/'encoders/qaac/qaac64.exe') != 0x8664:
        raise ValueError('Nested qaac encoder is not x64')
    if not (stage/'encoders/qaac/libsoxr64.dll').is_file():
        raise ValueError('Nested qaac resampler dependency missing')
    c = sqlite3.connect((stage/'profile/config.sqlite').resolve().as_uri()+'?mode=ro', uri=True)
    if c.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise ValueError('Configuration database integrity failed')
    formats = c.execute("SELECT value FROM configBlobs WHERE name='converter.formats'").fetchone()[0]
    if QAAC_ENCODER.encode('utf-8') not in formats:
        raise ValueError('Custom AAC format does not use the relative qaac path')
    c.close()
    for name in ['ffmpeg.exe','ffprobe.exe']:
        if not (stage/name).is_file():
            raise ValueError('Missing '+name)


def file_inventory(stage):
    return {p.relative_to(stage).as_posix():sha(p) for p in sorted(stage.rglob('*')) if p.is_file() and p.name!='build-manifest.json'}


def prepare_output_directory(directory):
    """Check creation and actual write access before publishing the application."""
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=directory) as probe:
        probe.write(b'foobar2000 output directory check')
        probe.flush()


def verify(target):
    report = read_json(target/'build-manifest.json')
    for rel, digest in report['files'].items():
        p = target/rel
        if not p.is_file() or sha(p) != digest:
            raise RuntimeError('Artifact changed or missing file: '+rel)
    validate_layout(target, target, Path(report['output_directory']), report.get('output_mode'))
    bundle = report['qaac_bundle']
    if bundle['encoder_path'] != QAAC_ENCODER:
        raise ValueError('Wrong qaac bundle encoder path')
    if qaac_bundle_inventory(target/'encoders/qaac') != bundle['files']:
        raise ValueError('OKE qaac bundle changed or contains unexpected files')
    log(f'Verified {len(report["files"])} files: {target}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target', type=Path, default=ROOT/'artifact'/'foobar2000')
    ap.add_argument('--output', type=Path)
    ap.add_argument('--output-mode', choices=['fixed', 'ask'], default='fixed',
                    help='Destination behavior for all six custom conversion presets (default: fixed)')
    ap.add_argument('--cache', type=Path, default=ROOT/'cache')
    ap.add_argument('--qaac-source', type=Path, default=DEFAULT_QAAC_SOURCE,
                    help='Copy this complete OKE qaac directory into encoders/qaac')
    ap.add_argument('--offline', action='store_true')
    ap.add_argument('--download-only', action='store_true')
    ap.add_argument('--verify-only', action='store_true')
    args = ap.parse_args()
    target = args.target.resolve()
    output = args.output.resolve() if args.output else target/'converted'
    if args.verify_only:
        verify(target)
        return
    lock = read_json(ROOT/'sources.lock.json')
    cache = args.cache.resolve()
    if target == ROOT or ROOT.is_relative_to(target) or cache == target or cache.is_relative_to(target):
        raise ValueError('Target overlaps the builder or cache directory')
    if target.exists() and (not target.is_dir() or any(target.iterdir())) and not args.download_only:
        raise ValueError('Target must be absent or empty; existing files are never overwritten')
    if target == Path(target.anchor):
        raise ValueError('A drive root cannot be a target')
    protected = Path(lock['reference_directory']).resolve()
    if target == protected or target.is_relative_to(protected) or protected.is_relative_to(target):
        raise ValueError('Target overlaps the original fb2k directory')
    if output == protected or output.is_relative_to(protected):
        raise ValueError('Output cannot be inside the original fb2k directory')
    if cache == protected or cache.is_relative_to(protected):
        raise ValueError('Cache cannot be inside the original fb2k directory')
    qaac_source = args.qaac_source.resolve()
    qaac_files = None
    if not args.download_only:
        if qaac_source == target or qaac_source.is_relative_to(target) or target.is_relative_to(qaac_source):
            raise ValueError('OKE qaac source overlaps the target directory')
        qaac_files = qaac_bundle_inventory(qaac_source)
        if not output.is_relative_to(target):
            prepare_output_directory(output)
    cache.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        files = list(pool.map(lambda s:cached_source(s,cache,args.offline),lock['sources']))
    sources = {s['id']:p for s,p in zip(lock['sources'],files)}
    if args.download_only:
        log('All pinned downloads verified.')
        return
    exe = seven_zip()
    target.parent.mkdir(parents=True,exist_ok=True)
    # A unique sibling directory makes the final rename stay on the same volume.
    with tempfile.TemporaryDirectory(prefix='.fb2k-build-',dir=target.parent) as tmp:
        tmp = Path(tmp)
        stage = tmp/'application'
        stage.mkdir()
        core = tmp/'core'
        log('Extracting official foobar2000 2.26.2...')
        extract(sources['core'],core,exe)
        for p in core.iterdir():
            if p.is_file() and p.suffix.lower() in ['.exe','.dll'] and p.name not in ['uninstall.exe','foobar2000 Shell Associations Updater.exe']:
                shutil.copy2(p,stage/p.name)
        for folder in ['doc','runtime','themes']:
            copy_tree_files(core/folder,stage/folder)
        (stage/'components').mkdir()
        for name in CORE_COMPONENTS:
            shutil.copy2(core/'components'/name,stage/'components'/name)
        for name in COMPONENTS:
            log('Installing x64 component: '+name)
            package = sources['foo_input_sacd'] if name == 'foo_dsd_processor' else sources[name]
            install_component(package,name,stage/'profile/user-components-x64'/name)
        enc = tmp/'encoders'
        extract(sources['encoders'],enc,exe)
        (stage/'encoders').mkdir()
        # NSIS duplicate paths appear x86 first, x64 last; verify the selected files.
        for p in enc.iterdir():
            if p.is_file() and p.suffix.lower() in ['.exe','.dll','.txt'] and p.name!='qaac.exe':
                shutil.copy2(p,stage/'encoders'/p.name)
        for name in ['flac','lame','opusenc','wavpack','mpcenc','oggenc2','qaac64','refalac64']:
            if pe_machine(stage/'encoders'/(name+'.exe')) != 0x8664:
                raise ValueError('Encoder extraction selected x86: '+name)
        log('Extracting Apple AAC dependencies (no installation)...')
        apple_dependencies(sources['apple'],tmp,stage/'encoders/QTfiles64',exe)
        log('Copying complete OKE qaac directory: '+str(qaac_source))
        copy_tree_files(qaac_source,stage/'encoders/qaac')
        if qaac_bundle_inventory(stage/'encoders/qaac') != qaac_files or qaac_bundle_inventory(qaac_source) != qaac_files:
            raise ValueError('OKE qaac source changed while copying')
        log('Extracting FFmpeg tools...')
        ff = tmp/'ffmpeg'
        extract(sources['ffmpeg'],ff,exe)
        ffout=stage
        for name in ['ffmpeg.exe','ffprobe.exe']:
            matches=list(ff.rglob(name))
            if len(matches)!=1:
                raise ValueError('Missing FFmpeg tool: '+name)
            shutil.copy2(matches[0],ffout/name)
        for p in ff.rglob('*'):
            if p.is_file() and p.name in ['LICENSE','LICENSE.txt','README.txt']:
                notices=stage/'doc/ffmpeg'
                notices.mkdir(exist_ok=True)
                shutil.copy2(p,notices/p.name)
        install_profile(stage,target,output,args.output_mode)
        if output.is_relative_to(target):
            prepare_output_directory(stage/output.relative_to(target))
        validate_layout(stage,target,output,args.output_mode)
        report={'schema':1,'core_version':'2.26.2','target_directory':str(target),
                'output_directory':str(output),'output_mode':args.output_mode,'sources':lock['sources'],
                'qaac_bundle':{'source_directory':str(qaac_source),'encoder_path':QAAC_ENCODER,'files':qaac_files},
                'template_files':read_json(ROOT/'config-template/manifest.json'),
                'files':file_inventory(stage)}
        write_json(stage/'build-manifest.json',report)
        # Verify every payload and configured destination before the final rename.
        verify(stage)
        # Recheck immediately before publication; never replace existing contents.
        if target.exists():
            if any(target.iterdir()):
                raise RuntimeError('Target changed during build; refusing publication')
            target.rmdir()
        stage.rename(target)
    log('Ready: '+str(target/'foobar2000.exe'))
    log('Conversion output: '+str(output))


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        log('ERROR: '+str(error))
        sys.exit(1)
