"""Create local development client credentials without printing tokens or replacing API keys."""
import json
import os
from pathlib import Path
import secrets


def main():
    root=Path(__file__).resolve().parents[1]
    env=root/'.env'
    lines=env.read_text().splitlines() if env.exists() else (root/'.env.example').read_text().splitlines()
    current=next((line.split('=',1)[1].strip().strip("'") for line in lines if line.startswith('CLIENT_TOKENS=')), '')
    tokens=json.loads(current or '{}')
    if not isinstance(tokens,dict): raise SystemExit('CLIENT_TOKENS must be a JSON object')
    if not tokens: tokens={'local':secrets.token_urlsafe(32)}
    value='CLIENT_TOKENS='+json.dumps(tokens,separators=(',',':'))
    if any(line.startswith('CLIENT_TOKENS=') for line in lines):
        lines=[value if line.startswith('CLIENT_TOKENS=') else line for line in lines]
    else: lines.append(value)
    env.write_text('\n'.join(lines)+'\n')
    env.chmod(0o600)
    local=root/'.local'
    local.mkdir(exist_ok=True,mode=0o700)
    path=local/'client.token'
    fd=os.open(path,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f: f.write(next(iter(tokens.values()))+'\n')
    path.chmod(0o600)
    print('Client token saved to .local/client.token; provider settings preserved. Run make up.')


if __name__=='__main__': main()
