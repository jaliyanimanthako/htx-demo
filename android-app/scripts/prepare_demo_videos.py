#!/usr/bin/env python3
"""Convert repository demo clips to offline Android assets. Requires ffmpeg/ffprobe."""
import json
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parents[2]
out = root / 'android-app/app/src/main/assets/demo_videos'
out.mkdir(parents=True, exist_ok=True)
for source in sorted((root / 'demo_videos').glob('*.MOV')):
    target = out / (source.stem.lower() + '.mp4')
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=color_transfer', '-of', 'json', str(source)]))
    filters = ['scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2']
    if info['streams'][0].get('color_transfer') in ('arib-std-b67', 'smpte2084'):
        filters += ['zscale=t=linear:npl=100', 'format=gbrpf32le', 'zscale=p=bt709',
                    'tonemap=tonemap=hable:desat=0', 'zscale=t=bt709:m=bt709:r=tv']
    filters += ['format=yuv420p', 'setsar=1']
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
        '-map', '0:v:0', '-an', '-vf', ','.join(filters), '-c:v', 'libx264', '-preset', 'fast',
        '-crf', '24', '-movflags', '+faststart', '-map_metadata', '-1', str(target)], check=True)
    print(f'{target.name}: {target.stat().st_size // 1024} KB', flush=True)
