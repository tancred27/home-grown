import os, sys, json, re, fnmatch
try: import tomllib
except ImportError: tomllib=None
SKIP_DIRS={'node_modules','.git','vendor','dist','build','target','__pycache__','.venv','venv'}
NOISE=re.compile(r'(^|/)(test|tests|testdata|fixtures?|__fixtures__|__tests__|examples?|example|e2e|integration|docs?|samples?|benchmarks?|templates?|playground|demo|testing)(/|$)',re.I)
MANIFESTS={'package.json':'npm','Cargo.toml':'cargo','pyproject.toml':'py','setup.py':'py','go.mod':'go','pom.xml':'mvn','build.gradle':'gradle','Chart.yaml':'helm','Dockerfile':'docker'}
def scan(root):
    units={}
    for d,ds,fs in os.walk(root):
        ds[:]=[x for x in ds if x not in SKIP_DIRS]
        rel=os.path.relpath(d,root)
        for f in fs:
            if f in MANIFESTS:
                units.setdefault(rel,set()).add(MANIFESTS[f])
            elif f.endswith('.tf'): units.setdefault(rel,set()).add('tf')
    return units
def meta(root,rel):
    d=os.path.join(root,rel); name=desc=None; priv=False
    p=os.path.join(d,'package.json')
    if os.path.exists(p):
        try:
            j=json.load(open(p)); name=j.get('name'); desc=j.get('description'); priv=bool(j.get('private'))
        except Exception: pass
    p=os.path.join(d,'Cargo.toml')
    if os.path.exists(p) and tomllib:
        try:
            j=tomllib.load(open(p,'rb')); pk=j.get('package',{}); name=pk.get('name'); desc=pk.get('description')
        except Exception: pass
    p=os.path.join(d,'pyproject.toml')
    if os.path.exists(p) and tomllib:
        try:
            j=tomllib.load(open(p,'rb')); pk=j.get('project',{}) or j.get('tool',{}).get('poetry',{}); name=pk.get('name'); desc=pk.get('description')
        except Exception: pass
    rd=''
    for f in os.listdir(d):
        if f.lower().startswith('readme'):
            try: rd=open(os.path.join(d,f),errors='ignore').read()
            except Exception: pass
            break
    return name,desc,priv,rd
if __name__=='__main__':
    root=sys.argv[1]; units=scan(root)
    out={'repo':os.path.basename(root),'total':len(units),'noise':0,'real':0,'with_readme':0,'with_desc':0,'est_tokens':0,'private_flag':0,'names':[]}
    for rel,k in units.items():
        if NOISE.search(rel+'/') and rel!='.': out['noise']+=1; continue
        out['real']+=1
        n,desc,priv,rd=meta(root,rel)
        out['with_readme']+= bool(rd.strip()); out['with_desc']+=bool(desc); out['private_flag']+=priv
        out['est_tokens']+=(min(len(rd),8000)+len(desc or '')+300)//4
        out['names'].append((rel,sorted(k)))
    json.dump(out,open(sys.argv[2],'w'))
    print({k:v for k,v in out.items() if k!='names'})
