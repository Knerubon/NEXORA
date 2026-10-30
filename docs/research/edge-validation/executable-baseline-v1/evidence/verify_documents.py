from pathlib import Path
import ast, hashlib, json, re, subprocess
root=Path.cwd(); out=root/'docs/research/edge-validation/executable-baseline-v1'; errors=[]; refs=0
pin=json.loads((out/'SOURCE-AND-CONFIG-INVENTORY.json').read_text())
sha=pin['source_sha']
for p,h in {**pin['source_files_sha256'],**pin['dependency_files_sha256']}.items():
    assert hashlib.sha256((root/p).read_bytes()).hexdigest()==h,p
assert hashlib.sha256(json.dumps(pin['dependency_files_sha256'],sort_keys=True,separators=(',',':')).encode()).hexdigest()==pin['dependency_fingerprint_sha256']
fields=0
for cls,rows in pin['configuration_inventory'].items():
    for r in rows:
        lines=(root/r['source']).read_text().splitlines()
        assert r['field']+':' in lines[r['line']-1].strip(),r
        assert r['approved_baseline_value'] is None and r['status']=='UNRESOLVED'
        fields+=1
for f in out.glob('*.md'):
    text=f.read_text()
    for url in re.findall(r'\]\(([^)]+)\)',text):
        if url.startswith('https://github.com/Knerubon/NEXORA/blob/'):
            rev,path=url.split('/blob/',1)[1].split('/',1); line=None
            if '#L' in path: path,line=path.rsplit('#L',1)
            obj=subprocess.run(['git','show',f'{rev}:{path}'],capture_output=True,text=True)
            if obj.returncode: errors.append((f.name,url,'missing pinned path'))
            elif line and not 1<=int(line)<=len(obj.stdout.splitlines()): errors.append((f.name,url,'line out of bounds'))
            refs+=1
        elif not url.startswith(('https:','#')) and not (f.parent/url.split('#')[0]).exists(): errors.append((f.name,url,'missing local link'))
# Compare only filenames, never external branch payload files.
for branch in subprocess.check_output(['git','for-each-ref','--format=%(refname:short)','refs/remotes/origin'],text=True).splitlines():
    if branch in ('origin/HEAD', 'origin/codex/pnf-executable-spec-v1'): continue
    changed=subprocess.check_output(['git','diff','--name-only',sha,branch],text=True).splitlines()
    collision=[p for p in changed if p.startswith(str(out.relative_to(root))+'/')]
    if collision: errors.append((branch,collision,'existing branch path ownership'))
assert not errors, errors
print(f'PASS: {refs} pinned links, {len(pin["source_files_sha256"])} source hashes, {len(pin["dependency_files_sha256"])} dependency hashes, {fields} constructor fields; local links and remote-branch owned-path collision inventory clean')
