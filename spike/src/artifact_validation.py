import subprocess, yaml
from pathlib import Path
def parse(uri):
    if not uri.startswith('repo://'): raise ValueError('only repo:// is supported in spike')
    repo,path=uri[7:].split('/',1); return repo,path
def validate(uri, repos_file):
    repo,path=parse(uri); repos=yaml.safe_load(Path(repos_file).read_text())['repos']; root=Path(repos.get(repo,''))
    if not root.is_dir() or not (root/'.git').exists(): return {'valid':False,'reason':'repo_unavailable'}
    tracked=subprocess.run(['git','-C',str(root),'ls-files','--error-unmatch',path],capture_output=True,text=True)
    return {'valid':tracked.returncode==0,'reason':'tracked' if tracked.returncode==0 else 'missing_file'}
