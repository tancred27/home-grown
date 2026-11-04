import json,glob,os,sys,numpy as np,re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.decomposition import TruncatedSVD
d=sys.argv[1]
Q={ "retry_failed_requests":"when a network call errors out, automatically attempt it again with waiting between attempts",
"rate_limit":"stop a client from hitting an endpoint too many times per minute",
"csv_parser":"read comma separated spreadsheet rows into objects",
"pdf_generation":"produce printable documents with pages from code",
"date_formatting":"turn timestamps into human readable calendar strings",
"feature_flags":"turn functionality on for a subset of users without redeploying",
"queue_jobs":"run background work later with workers and persistence",
"schema_validation":"check that incoming data has the right shape and types",
"markdown_to_html":"convert lightweight text markup into web pages",
"logging_structured":"write machine readable diagnostic messages with levels",
"cache_memoize":"avoid recomputing expensive function results by remembering them",
"websocket_client":"bidirectional realtime connection to a server from the browser",
"image_resize":"make pictures smaller or change their dimensions",
"cron_scheduler":"run a task at recurring times of day",
"diff_compare_objects":"find what changed between two data structures",
"env_config_loader":"read settings from dotfiles and process variables",
"pagination_helper":"split long result lists into numbered chunks",
"jwt_auth":"verify signed bearer credentials on incoming API calls",
"email_sending":"deliver messages to inboxes via smtp",
"slugify_string":"make text safe for use inside a web address",
"deep_clone":"duplicate a nested structure so edits do not affect the original",
"file_upload":"accept multipart form submissions containing attachments",
"i18n_translation":"show the interface in the visitor's own language",
"cli_argument_parsing":"read flags and options typed after a command name",
"http_mock":"fake a remote server's responses in unit tests"}
docs=[];lab=[];seen=set()
for f in sorted(glob.glob(d+'/*.json')):
    k=os.path.basename(f)[:-5]
    for o in json.load(open(f))['objects']:
        p=o['package']
        if p['name'] in seen or not p.get('description'): continue
        seen.add(p['name']); docs.append(p['name'].replace('-',' ')+' . '+p['description']); lab.append(k)
lab=np.array(lab); print("docs",len(docs))
qk=list(Q); qt=[Q[k] for k in qk]
def evalm(S,name):
    r10=[];mrr=[]
    for i,k in enumerate(qk):
        order=np.argsort(-S[i]); rel=(lab[order]==k)
        r10.append(rel[:10].mean()); first=np.argmax(rel) if rel.any() else None
        mrr.append(1/(first+1) if first is not None else 0)
    print(f"{name:28s} precision@10 {np.mean(r10):.2f}  MRR {np.mean(mrr):.2f}  queries with 0 relevant in top10: {sum(1 for x in r10 if x==0)}/{len(qk)}")
tf=TfidfVectorizer(stop_words='english',sublinear_tf=True,ngram_range=(1,2),min_df=1); X=tf.fit_transform(docs); evalm((tf.transform(qt)@X.T).toarray(),"TF-IDF lexical")
for k in (50,150):
    svd=TruncatedSVD(k,random_state=0); Z=svd.fit_transform(X); Zq=svd.transform(tf.transform(qt))
    Zn=Z/(np.linalg.norm(Z,axis=1,keepdims=True)+1e-9); Zqn=Zq/(np.linalg.norm(Zq,axis=1,keepdims=True)+1e-9)
    evalm(Zqn@Zn.T,f"LSA k={k}")
# overlap: how many queries share any non-stopword token with their cluster docs
print("random baseline precision@10 ~",round(1/len(qk),2))
# hybrid
S1=(tf.transform(qt)@X.T).toarray(); svd=TruncatedSVD(150,random_state=0); Z=svd.fit_transform(X); Zq=svd.transform(tf.transform(qt))
S2=(Zq/(np.linalg.norm(Zq,axis=1,keepdims=True)+1e-9))@(Z/(np.linalg.norm(Z,axis=1,keepdims=True)+1e-9)).T
z=lambda S:(S-S.mean(1,keepdims=True))/(S.std(1,keepdims=True)+1e-9); evalm(z(S1)+z(S2),"hybrid TF-IDF+LSA150")
