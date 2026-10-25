#!/usr/bin/env python3
"""Prepare real App Store artwork during Amplify build.

Uses Apple's published iTunes Lookup endpoint, not handmade covers.
Fails the build when an icon cannot be verified/downloaded, avoiding an
accidentally published fake or missing image. Only Python stdlib needed.
"""
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
import json, re, sys, time

root=Path(sys.argv[1] if len(sys.argv)>1 else 'dist').resolve()
apps=root/'apps.html'
ids={'6761431119':'stoolytics','6762096127':'syntax-alchemist','6761403683':'angrywife'}
icons_dir=root/'assets'/'app-icons'
icons_dir.mkdir(parents=True,exist_ok=True)

def fetch(url):
    last=None
    for attempt in range(3):
        try:
            req=Request(url,headers={'User-Agent':'AndreyPortfolio/1.0 (+app-icon-build)'})
            with urlopen(req,timeout=25) as r:
                data=r.read(4*1024*1024 + 1)
                if len(data)>4*1024*1024: raise ValueError('Image too large')
                return data
        except Exception as exc:
            last=exc
            if attempt<2:time.sleep(attempt+1)
    raise RuntimeError(f'Unable to fetch Apple artwork from {url}: {last}')

html=apps.read_text(encoding='utf-8')
for app_id,slug in ids.items():
    payload=json.loads(fetch('https://itunes.apple.com/lookup?'+urlencode({'id':app_id,'country':'lu'})))
    record=next((r for r in payload.get('results',[]) if str(r.get('trackId'))==app_id),None)
    if not record:raise RuntimeError(f'Apple lookup did not return expected app ID {app_id}')
    url=record.get('artworkUrl512') or record.get('artworkUrl100')
    if not url or not url.startswith('https://'):raise RuntimeError(f'No artwork URL for {app_id}')
    data=fetch(url)
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        ext='png'
    elif data[:3]==b'\xff\xd8\xff':
        ext='jpg'
    elif data.startswith(b'RIFF') and data[8:12]==b'WEBP':
        ext='webp'
    else:
        raise RuntimeError(f'Unexpected image bytes for {app_id}')
    relative=f'assets/app-icons/{slug}.{ext}'
    (root/relative).write_bytes(data)
    pattern=re.compile(r'<img\b[^>]*\bdata-app-id="'+re.escape(app_id)+r'"[^>]*>')
    counter=[0]
    def modify(match):
        counter[0]+=1
        tag=match.group(0)
        if re.search(r'\bsrc="[^"]*"',tag):
            return re.sub(r'\bsrc="[^"]*"',f'src="{relative}"',tag,count=1)
        return tag[:-1]+f' src="{relative}">'
    html=pattern.sub(modify,html)
    if counter[0]<1: raise RuntimeError(f'No image nodes found for app {app_id}')
    print(f'{record.get("trackName")}: {relative} ({len(data)} bytes, {counter[0]} references)')
html=re.sub(r'(<a\s+class="[^"]*\bapple-artwork\b[^"]*)"',r'\1 is-loaded"',html)
apps.write_text(html,encoding='utf-8')
