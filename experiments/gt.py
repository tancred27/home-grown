import json,sys,os,glob,fnmatch
try: import tomllib
except ImportError: pass
out=sys.argv[1]; repos=sys.argv[2]
def det(r): return {n[0] for n in json.load(open(f'{out}/{r}.json'))['names']}
def workspaces(root):
    pats=[]
    p=os.path.join(root,'package.json')
    if os.path.exists(p):
        w=json.load(open(p)).get('workspaces',[]); pats+= w if isinstance(w,list) else w.get('packages',[])
    p=os.path.join(root,'Cargo.toml')
    if os.path.exists(p):
        pats+=tomllib.load(open(p,'rb')).get('workspace',{}).get('members',[])
    p=os.path.join(root,'pnpm-workspace.yaml')
    if os.path.exists(p):
        pats+=[l.strip()[2:].strip('\'"') for l in open(p) if l.strip().startswith('- ') and '/' in l]
    gt=set()
    for pt in pats:
        if pt.startswith('!'): continue
        for m in glob.glob(os.path.join(root,pt)):
            if os.path.isdir(m) and any(os.path.exists(os.path.join(m,f)) for f in ('package.json','Cargo.toml')): gt.add(os.path.relpath(m,root))
    return gt
for r in ['babel','backstage','tokio','turborepo']:
    root=f'{repos}/{r}'; g=workspaces(root); d=det(r)
    tp=len(g&d); print(r,'declared',len(g),'detected',len(d),'recall',round(tp/max(1,len(g)),2),'extra(not declared)',len(d-g))
    print('  sample extras:',sorted(d-g)[:12])
