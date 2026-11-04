import psycopg, numpy as np, time, uuid, random, statistics as st, sys
from pgvector.psycopg import register_vector
rng=np.random.default_rng(0); random.seed(0)
N=50000; G=200; U=100; D=64
c=psycopg.connect("host=/tmp port=5433 user=postgres dbname=postgres",autocommit=True)
c.execute("drop database if exists expb"); c.execute("create database expb")
c=psycopg.connect("host=/tmp port=5433 user=postgres dbname=expb",autocommit=True)
c.execute("create extension vector"); register_vector(c)
c.execute("""create table membership(user_id int, group_id int);
create table asset(id int primary key, scope int, name text, descr text, tsv tsvector generated always as (to_tsvector('english',name||' '||descr)) stored, vec vector(%d));
create table edge(src int, dst int, type text);
drop role if exists app; create role app login;
grant select on all tables in schema public to app;
alter table asset enable row level security; alter table edge enable row level security;
create policy see on asset for select to app using (scope in (select group_id from membership where user_id=nullif(current_setting('app.user_id',true),'')::int));
create policy see_edge on edge for select to app using (
  src in (select id from asset) and dst in (select id from asset));"""%D)
words=[f"w{i}" for i in range(3000)]
# clustered vectors: 50 topic centers
centers=rng.normal(size=(50,D)); 
vecs=centers[rng.integers(0,50,N)]+0.6*rng.normal(size=(N,D)); vecs=vecs.astype('float32')
scopes=rng.integers(0,G,N)
with c.cursor() as cur:
    with cur.copy("copy asset(id,scope,name,descr,vec) from stdin") as cp:
        for i in range(N):
            cp.write_row((i,int(scopes[i]),f"asset {i} "+random.choice(words),' '.join(random.choices(words,k=12)),vecs[i]))
    with cur.copy("copy membership from stdin") as cp:
        mem={}
        for u in range(U):
            gs=random.sample(range(G),3); mem[u]=set(gs)
            for g in gs: cp.write_row((u,g))
    with cur.copy("copy edge from stdin") as cp:
        for _ in range(N): cp.write_row((random.randrange(N),random.randrange(N),'depends_on'))
c.execute("create index on asset using gin(tsv); create index on asset(scope); create index on asset using hnsw(vec vector_cosine_ops) with (m=16, ef_construction=64); create index on membership(user_id); analyze")
cu=psycopg.connect("host=/tmp port=5433 user=app dbname=expb",autocommit=False); register_vector(cu)
def q(user,sql,params=()):
    with cu.cursor() as cur:
        cur.execute("select set_config('app.user_id',%s,true)",(str(user),)); cur.execute(sql,params); r=cur.fetchall(); cu.commit(); return r
# 1 leak test: every returned row in user's groups, across FTS, vector, edge join, count
leaks=0; checks=0
for u in range(U):
    qv=vecs[random.randrange(N)]
    rows=q(u,"select id,scope from asset order by vec <=> %s limit 50",(qv,))
    rows+=q(u,"select id,scope from asset where tsv @@ plainto_tsquery('english',%s) limit 50",(random.choice(words),))
    rows+=q(u,"select a.id,a.scope from edge e join asset a on a.id=e.dst limit 100")
    for _,s in rows: checks+=1; leaks+= s not in mem[u]
    n=q(u,"select count(*) from asset")[0][0]; leaks+= n!=int((np.isin(scopes,list(mem[u]))).sum())
print("LEAK TEST rows checked",checks,"leaks",leaks)
# 2 recall of filtered HNSW vs exact, and latency
rec=[];lat=[];lat_ex=[];nret=[]
for u in range(U):
    qv=vecs[random.randrange(N)]
    t=time.time(); a=[r[0] for r in q(u,"select id from asset order by vec <=> %s limit 10",(qv,))]; lat.append(time.time()-t)
    with cu.cursor() as cur:
        cur.execute("select set_config('app.user_id',%s,true)",(str(u),)); cur.execute("set local enable_indexscan=off"); 
        t=time.time(); cur.execute("select id from asset order by vec <=> %s limit 10",(qv,)); b=[r[0] for r in cur.fetchall()]; lat_ex.append(time.time()-t); cu.commit()
    nret.append(len(a)); rec.append(len(set(a)&set(b))/max(1,len(b)))
print("visible rows/user ~",int(N*3/G),"| filtered HNSW: mean recall@10",round(st.mean(rec),3),"| mean returned",round(st.mean(nret),2),"/10 | zero-result users",sum(1 for n in nret if n==0))
print("latency ms hnsw p50/p95",round(1000*st.median(lat),1),round(1000*sorted(lat)[94],1),"| exact-scan p50",round(1000*st.median(lat_ex),1))
# 3 hybrid FTS latency
l=[]
for u in range(U):
    t=time.time(); q(u,"select id from asset where tsv @@ plainto_tsquery('english',%s) order by ts_rank(tsv,plainto_tsquery('english',%s)) desc limit 10",(random.choice(words),)*2); l.append(time.time()-t)
print("FTS under RLS ms p50/p95",round(1000*st.median(l),1),round(1000*sorted(l)[94],1))
