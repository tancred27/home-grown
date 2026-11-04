import psycopg, numpy as np, time, random, statistics as st
from pgvector.psycopg import register_vector
rng=np.random.default_rng(1); D=64
c=psycopg.connect("host=/tmp port=5433 user=postgres dbname=expb",autocommit=True); register_vector(c)
c.execute("insert into asset(id,scope,name,descr,vec) select 100000+g, (random()*199)::int,'x','y', (select vec from asset where id=(g%50000)) from generate_series(1,450000) g")
c.execute("analyze asset")
cu=psycopg.connect("host=/tmp port=5433 user=app dbname=expb"); register_vector(cu)
lat=[];lat2=[]
for u in range(60):
    qv=rng.normal(size=D).astype('float32')
    with cu.cursor() as cur:
        cur.execute("select set_config('app.user_id',%s,true)",(str(u),)); cur.execute("set local enable_indexscan=off")
        t=time.time(); cur.execute("select id from asset order by vec <=> %s limit 10",(qv,)); cur.fetchall(); lat.append(time.time()-t); cu.commit()
print("500k rows, ~1.5% visible, exact filtered scan ms p50/p95",round(1000*st.median(lat),1),round(1000*sorted(lat)[56],1))
