"""Exercise bundled encoders with generated audio; never uses a music library."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target',type=Path,default=ROOT/'artifact'/'foobar2000')
    ap.add_argument('--work',type=Path,default=ROOT/'work/smoke')
    ap.add_argument('--qaac-reference',type=Path,help='Optional reference qaac64.exe for bitstream comparisons')
    a=ap.parse_args()
    target=a.target.resolve(); work=a.work.resolve()
    if work==target or target.is_relative_to(work):
        raise ValueError('Test work must not be an ancestor of the application')
    work.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy()
    # Prove that encoder discovery does not depend on global ffmpeg/qaac installations.
    env['PATH']=str(Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32')
    ff=target/'ffmpeg.exe'; probe=target/'ffprobe.exe'; enc=target/'encoders'
    qaac=enc/'qaac/qaac64.exe'
    report={'target':str(target),'global_path_removed':True,'checks':[]}
    def run(args,stdin=None):
        result=subprocess.run([str(x) for x in args],input=stdin,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,cwd=work,timeout=60)
        if result.returncode:
            raise RuntimeError('Command failed: '+str(args)+'\n'+result.stderr.decode('utf8','replace'))
        return result.stdout,result.stderr
    out,err=run([qaac,'--check'])
    report['qaac_check']=(out+err).decode('utf8','replace').strip()
    for bits,rate in [(16,44100),(24,96000)]:
        sample=work/f'source-{bits}.wav'
        codec=f'pcm_s{bits}le'
        run([ff,'-hide_banner','-loglevel','error','-y','-f','lavfi','-i',f'sine=frequency=997:sample_rate={rate}:duration=1','-ac','2','-c:a',codec,sample])
        raw,_=run([ff,'-v','error','-i',sample,'-f',f's{bits}le','-c:a',codec,'-'])
        flac=work/f'roundtrip-{bits}.flac';wv=work/f'roundtrip-{bits}.wv';alac=work/f'roundtrip-{bits}-alac.m4a'
        run([enc/'flac.exe','--silent','-f','-8','-o',flac,sample])
        run([enc/'wavpack.exe','-q','-y','-hh','-x1',sample,'-o',wv])
        run([enc/'refalac64.exe','--silent','-o',alac,sample])
        for name,p in [('FLAC',flac),('WavPack',wv),('ALAC',alac)]:
            decoded,_=run([ff,'-v','error','-i',p,'-f',f's{bits}le','-c:a',codec,'-'])
            if decoded!=raw:
                raise ValueError(f'{name} {bits}-bit lossless PCM mismatch')
            report['checks'].append({'name':f'{name} {bits}-bit {rate}Hz PCM roundtrip','passed':True,'pcm_sha256':hashlib.sha256(raw).hexdigest()})
        aac=work/f'no-delay-{bits}.m4a'
        run([qaac,'-i','-q','2','-v','192','--no-delay','-o',aac,'-'],sample.read_bytes())
        info,_=run([probe,'-v','error','-show_streams','-of','json',aac])
        stream=json.loads(info)['streams'][0]
        decoded,_=run([ff,'-v','error','-i',aac,'-f','s16le','-'])
        if stream['codec_name']!='aac' or not decoded:
            raise ValueError('AAC output cannot be decoded')
        report['checks'].append({'name':f'AAC no-delay {bits}-bit input','passed':True,'codec':stream['codec_name'],'sample_rate':stream['sample_rate'],'duration':stream.get('duration')})
        if a.qaac_reference:
            reference=work/f'no-delay-{bits}-reference.m4a'
            run([a.qaac_reference.resolve(),'-i','-q','2','-v','192','--no-delay','-o',reference,'-'],sample.read_bytes())
            stream_a,_=run([ff,'-v','error','-i',aac,'-c:a','copy','-f','adts','-'])
            stream_b,_=run([ff,'-v','error','-i',reference,'-c:a','copy','-f','adts','-'])
            if stream_a!=stream_b:
                raise ValueError(f'AAC {bits}-bit elementary stream differs from reference qaac')
            report['checks'].append({'name':f'AAC {bits}-bit bitstream matches reference qaac','passed':True,'aac_sha256':hashlib.sha256(stream_a).hexdigest()})
    # Exercise the remaining free encoders supplied by the reference package.
    sample=work/'source-16.wav'
    for name,args in [
        ('MP3',[enc/'lame.exe','--silent',sample,work/'extra.mp3']),
        ('Opus',[enc/'opusenc.exe','--quiet',sample,work/'extra.opus']),
        ('Vorbis',[enc/'oggenc2.exe','-Q','-o',work/'extra.ogg',sample]),
        ('Musepack',[enc/'mpcenc.exe','--silent','--overwrite',sample,work/'extra.mpc']),
    ]:
        run(args)
        outpath=work/{'MP3':'extra.mp3','Opus':'extra.opus','Vorbis':'extra.ogg','Musepack':'extra.mpc'}[name]
        decoded,_=run([ff,'-v','error','-i',outpath,'-f','s16le','-'])
        if not decoded:raise ValueError(name+' output is empty')
        report['checks'].append({'name':name+' encode/decode','passed':True})
    (work/'results.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    print(f'PASS: {len(report["checks"])} audio checks; {report["qaac_check"]}')
    print('Report: '+str(work/'results.json'))


if __name__=='__main__':
    main()
