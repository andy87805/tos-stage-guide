"""神魔之塔關卡敵技攻略：資料更新腳本

    python tools/update.py          # 增量更新（平常與排程都跑這個）
    python tools/update.py --full   # 丟掉卡片索引、整個重抓

做三件事：
 1. 增量更新卡片與技能索引（tools/index.json）
 2. 從維基的 Template:CurrentEvents 找出本期開放的關卡並解析敵技
 3. 比對能處理敵技的卡，輸出 data.js 並下載缺少的卡圖到 img/
任何一步抓不到資料就中止、不覆寫舊的 data.js。
"""
import json,re,sys,os,time,html,urllib.request,urllib.parse
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
sys.stdout.reconfigure(encoding='utf-8')
HERE=os.path.dirname(os.path.abspath(__file__));OUT=os.path.dirname(HERE)
IDX=os.path.join(HERE,'index.json');IMG=os.path.join(OUT,'img');os.makedirs(IMG,exist_ok=True)
U='https://tos.fandom.com/zh/api.php'
UA={'User-Agent':'Mozilla/5.0 (compatible; tos-stage-guide; personal fan tool)'}
TW=timezone(timedelta(hours=8));NOW=datetime.now(TW).replace(tzinfo=None)

def api(**p):
    p['format']='json'
    for i in range(4):
        try:
            r=urllib.request.Request(U,data=urllib.parse.urlencode(p).encode(),headers=UA)
            return json.load(urllib.request.urlopen(r,timeout=40))
        except Exception as e:
            if i==3: raise
            time.sleep(2+i*4)
def batch(titles):
    out={}
    for i in range(0,len(titles),50):
        d=api(action='query',prop='revisions',rvprop='content',rvslots='main',titles='|'.join(titles[i:i+50]))
        for p in d.get('query',{}).get('pages',{}).values():
            if 'revisions' in p: out[p['title']]=p['revisions'][0]['slots']['main']['*']
    return out
def fields(t):
    d={}
    for m in re.finditer(r'^\|[ \t]*(\w+)[ \t]*=[ \t]*(.*)$',t,re.M): d[m.group(1)]=m.group(2).strip()
    return d
def clean(s):
    s=html.unescape(html.unescape(s));s=re.sub(r'<br\s*/?>','\n',s);s=re.sub(r'\[/?\w+\]|<[^>]+>','',s)
    return s.replace('\xa0',' ').strip()
norm=lambda s:re.sub(r'\s+','',s)

# ───────── 1. 卡片與技能索引 ─────────
CF=('name','attr','race','rare','skill','skill1','skill2','lskill','cskill')
SLOTS=CF[4:]
def load_index():
    if '--full' in sys.argv or not os.path.exists(IDX): return {'v':2,'cards':{},'skills':{}}
    d=json.load(open(IDX,encoding='utf-8'))
    return d if d.get('v')==2 else {'v':2,'cards':{},'skills':{}}
def fetch_cards(idx,ids):
    got=0
    for k,v in batch([f'Template:{i}' for i in ids]).items():
        f=fields(v)
        if f.get('name') and '召喚獸' in v:
            idx['cards'][k.split(':')[1]]={c:f[c] for c in CF if f.get(c)};got+=1
    return got
def update_index(idx):
    cards=idx['cards'];mx=max(map(int,cards),default=0)
    # 最近的卡維基常晚幾天才補技能，所以每次都重抓最後 300 個編號
    recent=list(range(max(1,mx-300),mx+1))
    if recent: fetch_cards(idx,recent)
    i=mx+1;empty=0
    # 卡片編號中間有大段空號（例如 3019 之後），所以首次建置要容忍 3000 個空號才視為到底；
    # 平常增量更新只往後多看 500 個
    gap=60 if mx<10000 else 10
    while empty<gap:
        n=fetch_cards(idx,range(i,i+50));i+=50
        empty=0 if n else empty+1
        if i%1000<50: print('  cards …',i,len(cards),flush=True)
    names={c[s] for c in cards.values() for s in SLOTS if c.get(s)}
    names={n for n in names if not re.search(r'[|{}\[\]<>#]',n)}
    rn={cards[str(j)][s] for j in recent if str(j) in cards for s in SLOTS if cards[str(j)].get(s)}
    todo=sorted(n for n in names if n not in idx['skills'] or n in rn)
    got=batch(['Template:'+n for n in todo])
    for n in todo:
        t=got.get('Template:'+n)
        if not t: idx['skills'].setdefault(n,None);continue
        f=fields(t);m=re.search(r'\|\s*effect\s*=\s*(.*?)\n\s*\n?\|\s*\w+\s*=',t,re.S)
        idx['skills'][n]={'eff':m.group(1) if m else f.get('effect',''),'cd':f.get('minTurn') or f.get('turn') or ''}
    json.dump(idx,open(IDX,'w',encoding='utf-8'),ensure_ascii=False,separators=(',',':'))
    print(f'index: {len(cards)} cards (max {max(map(int,cards),default=0)}), {sum(1 for s in idx["skills"].values() if s)} skills, fetched {len(todo)} skill pages',flush=True)

# ───────── 2. 本期關卡 ─────────
def pdate(s,end=False):
    m=re.match(r'\s*(\d{4})/(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?',s or '')
    if not m: return None
    y,mo,d=int(m[1]),int(m[2]),int(m[3])
    if m[4]: return datetime(y,mo,d,int(m[4]),int(m[5]))
    return datetime(y,mo,d,23,59) if end else datetime(y,mo,d)
def discover():
    t=batch(['Template:CurrentEvents']).get('Template:CurrentEvents','')
    t=re.sub(r'<!--.*?-->','',t,flags=re.S)
    ev=[];sec=''
    for m in re.finditer(r'\{\{EventSection\|(.*?)\}\}\s*$|\{\{Event\|\{\{DIcon\|(\d+)\|([^}]+)\}\}[ \t]*(?:※[ \t]*([^\n|]*))?\s*\|([^|}]*)\|([^|}]*)\}\}',t,re.M):
        if m[1] is not None: sec=re.sub(r'[\[\]⬇\s]|全新|系列|活動|更新','',m[1]);continue
        st,en=pdate(m[5]),pdate(m[6],True)
        if not st or not en or en<NOW or st>NOW+timedelta(days=3): continue
        kind=(m[4] or '').strip() or (sec if sec!='所有關卡' else '')
        fmt=lambda d:f'{d.month}/{d.day}'
        ev.append(dict(page=m[3].strip(),icon=int(m[2]),kind=kind,open='常駐' if en.year>NOW.year+5 else f'{st.year}/{fmt(st)} – {fmt(en)}',soon=st>NOW))
    return ev
def parse_page(e,wt):
    out=[]
    ach=re.search(r'\{\{關卡成就\|(.*?)\}\}',wt);lim=re.search(r'limit=\{\{RL\|(\d+)',wt)
    parts=re.split(r'^(==+)[ \t]*(.*?)[ \t]*==+[ \t]*$',wt,flags=re.M)
    secs=[('',parts[0])]+[(parts[i+1],parts[i+2]) for i in range(1,len(parts)-2,3)]
    for head,body in secs:
        rows=[];cur=0
        for m in re.finditer(r'\{\{關卡數據\|(\d+)\|(.*?)\}\}\s*$',body,re.M):
            mid,rest=m.group(1),m.group(2)
            st=re.search(r'stage=(\d*)',rest)
            if st and st.group(1): cur=int(st.group(1))
            def g(k,pat=r'([\d,]+)'):
                x=re.search(r'(?:^|\|)'+k+'='+pat,rest);return x.group(1) if x else ''
            if int(mid)==0: continue
            rows.append(dict(floor=cur,mid=mid,hp=int(g('hp').replace(',','') or 0),df=int(g('def').replace(',','') or 0),cd=int((g('turn') or '1').split(',')[0]),es=[x for x in g('es').split(',') if x],attr=g('attr',r'([水火木光暗])')))
        if not rows or not any(r['es'] for r in rows): continue
        ti=re.search(r'\{\{TitleIcon\|(\d+)\}\}',head)
        name=re.sub(r'\s+',' ',re.sub(r'\{\{.*?\}\}|\[\[|\]\]|\'{2,}','',head)).strip() or e['page']
        info=re.search(r'\{\{地下城信息\|sta=(\d*)',body)
        out.append(dict(page=e['page'],name=name,kind=e['kind'],boss=int(ti.group(1)) if ti else e['icon'],sta=info.group(1) if info else '',open=e['open'],soon=e['soon'],
                        limit=f'限時 {lim.group(1)} 分鐘' if lim else '',ach=[a for a in ach.group(1).split('|') if a and '=' not in a] if ach else [],rows=rows))
    return out

# ───────── 3. 比對 ─────────
# 卡片技能寫的是「遊戲內敵技名稱」，維基的 content 分類則常是另一套名字，
# 所以比對以敵技標題為主：標題去掉數字與註記後得到核心名稱，再經別名表轉成卡片用語。
ALIAS=[  # (核心名稱的 regex, 卡片技能中的用語)
 (r'^消.+禁咒$','符石禁咒'),(r'形狀盾$|^拼圖盾$','指定形狀盾'),(r'^防禦盾$','%防禦盾'),
 (r'^反首消.*符石盾$','反首消符石盾'),(r'連擊零化$','符石連擊零化'),
 (r'^首消.*粒數盾$|^首消.*顆數盾$','首消粒數盾'),(r'^首消.*組屬性符石盾$','首消組數屬性符石盾'),
 (r'^全版消除盾$','全消盾'),(r'^全消盾$','全版消除盾'),(r'^減傷抗性','指定減傷抗性敵技'),
 (r'^固定連擊盾$','固定連擊盾'),(r'^反擊$','敵人反擊傷害'),(r'步限制$','步數限制'),
 (r'^殞命輪迴$','殞命輪迴倒數'),(r'^痲痺$','麻痺'),(r'符石護盾粒?$','累積符石盾'),
 (r'^回復自損$','回復顛倒'),(r'^起結有序|^始終盾$','起結有序'),(r'^灼熱地型','灼熱地型'),
 (r'^符石禁消$|^消除禁咒$','符石禁咒'),(r'^百分比盾$','%防禦盾'),
 (r'十字限盾$','十字限盾'),(r'T字限盾$','T字限盾'),(r'L字限盾$','L字限盾'),(r'符石加護$','加護符石'),
]
TIPS=[  # (核心名稱的 regex, 應對方式)
 (r'^不朽聖果','場上需保留指定數量的「不朽聖果」才打得出傷害，轉珠時別把聖果消掉。'),
 (r'^T字限盾','首批以 T 形消除 5 粒符石。'),(r'^L字限盾','首批以 L 形消除 5 粒符石。'),(r'^十字限盾','首批以十字形消除 5 粒符石。'),
 (r'^喋血屠刀','敵人攻擊時直接扣除大量生命力，減傷與意志無效；要在它出手前擊殺或用吸收類技能。'),
 (r'^無法控制','敵人不吃狀態，延遲、凍結類技能無效。'),
 (r'^首消不同種類盾','首消指定連擊數，每組種類不可重複。'),(r'^首消相同種類盾','首消指定連擊數，全部要同一種類。'),
 (r'^回復顛倒','消心符石會反過來扣血，避開心符石或帶無視。'),(r'^終結倒數','倒數結束前沒擊殺就直接死亡。'),
 (r'^首消.*組屬性符石盾','首批消除指定組數的屬性符石。'),(r'^反首消.*符石盾','首批不能消到指定屬性的符石。'),
 (r'連擊零化$','首批消到指定屬性符石，全隊攻擊力歸零。'),(r'^灼熱地型','在地型內消除符石會扣生命力，護盾可擋。'),
 (r'^角色符石地型盾','結束轉珠時角色符石要停在鎖定格內。'),(r'^首消.*粒數盾','首批消除指定粒數的符石。'),
 (r'^愈攻愈強|^越攻越強','愈打愈痛，速戰速決。'),(r'^淨世','進場清除所有附加效果並重置技能 CD，別在上一層先開技。'),
 (r'^起結有序','從綠色漩渦起手、在紅色漩渦收尾。'),(r'^炎之氣','炎之氣歸零前擊殺會直接死亡，護盾與減傷失效。'),
 (r'^消炎印記','消除帶印記的符石來削減炎之氣。'),(r'^全版消除盾|^全消盾','首批消除場上所有符石。'),
 (r'^消.+禁咒$','無法消除指定符石。'),(r'加護$|^加護符石','場上要有加護符石才打得出傷害，可用技能自行添加。'),
 (r'^固定連擊盾','首批要剛好消除指定的連擊數。'),(r'^連擊盾$','需達成指定以上的連擊數，沒有卡能無視，靠增加連擊的技能湊數。'),
 (r'之佑律$','被移除、引爆的符石一律變成指定屬性符石。'),(r'^深淵滅襲|^聖劍裁決','擊殺前把深淵滅襲壓到 0%。'),
 (r'^首攻CD','首次攻擊 CD 被改短，進場就要處理。'),(r'形狀盾$|^拼圖盾','在指定位置、以指定形狀消除符石。'),
 (r'^五屬盾','同一回合消除五種屬性符石。'),(r'^攻前盾','敵人攻擊前打不出傷害，要先撐過一次攻擊或帶無視。'),
]
VERB='無視|解除|減少|減至|添加|吸收|不會|免疫|不受|抵銷|破除|傷害轉化'
LIST=re.compile(r'^[\s▍▶▷◆⇒•⓵-⓿①-⑳IVX.\d()（）、及和與]*(?:包括)?「')   # 清單的續行：開頭就是「…」
RENAME={'步限制':'步數限制','次受擊護盾':'受擊護盾','首攻CD':'首攻 CD 變更'}
def core(part):
    p=re.sub(r'[\(（].*?[\)）]','',part);p=re.split(r'\s[-–]\s',p)[0]
    p=re.sub(r'\d[\d,.]*\s*(?:粒|層|組|個)(?=\s|$)','',p)
    p=re.sub(r'[\d\s%％≥≤＋+.,，〖〗【】「」]|萬|點$','',p)
    return RENAME.get(p,p)
def es_keys(t):
    """敵技模板 → (分類名稱, 標題, 比對用語集合)"""
    title=clean(t.get('title','')).strip('【】〖〗')
    content=[norm(c) for c in (t.get('content') or '').split(',') if c.strip()]
    if not title:   # 沒有標題時，效果文字開頭的【…】通常就是遊戲內名稱
        m=re.search(r'【(.+?)】',clean(t.get('effect','')))
        title=m.group(1) if m and '凝護' not in m.group(1) else ''
    cores=[c for c in (core(p) for p in re.split(r'\s*[‧．]\s*',title)) if len(c)>=2]
    if not cores: cores=content[:1]
    keys=set(cores)|{c for c in content if c not in('連擊盾','Combo盾','指定Combo盾','弱攻','傷害零化','減傷')}
    for c in list(keys):
        if c.endswith('狀態') and len(c)>3: keys.add(c[:-2])
        for pat,term in ALIAS:
            if re.search(pat,c): keys.add(term)
    return ' ‧ '.join(cores),title,keys
def main():
    idx=load_index();update_index(idx)
    cards=idx['cards'];skills={n:s for n,s in idx['skills'].items() if s}
    if len(cards)<1000: sys.exit('卡片索引異常，中止')
    byskill={}
    for cid,c in cards.items():
        for k in SLOTS:
            if c.get(k) in skills: byskill.setdefault(c[k],[]).append((int(cid),k))
    rare=lambda i:int(cards[str(i)].get('rare') or 0)
    # 反向索引：卡片用語 → [(技能名, 動詞, 原文片段)]
    # 「A」、「B」及「C」這種清單常跨好幾行，動詞只出現在第一行，所以往上找最多 5 行
    term_idx={}
    for n,s in skills.items():
        if n not in byskill: continue
        L=[l for l in (re.sub(r'\s+',' ',clean(x)) for x in re.split(r'<br\s*/?>',s['eff'])) if l]
        for i,l in enumerate(L):
            toks=re.findall(r'「([^」]{2,16})」',l)
            if not toks or re.match(r'^[（(]?(註|※)',l): continue
            j=i
            # 這一行自己沒有動詞時，只有「清單續行」才往上找；中間每一行也都必須是續行
            while not re.search(VERB,L[j]):
                if not LIST.match(L[j]) or j==0 or i-j>=5: j=-1;break
                j-=1
            if j<0 or re.match(r'^[（(]?(註|※)',L[j]): continue
            sn=' '.join(L[j:i+1])
            verb='無視' if '無視' in sn else '解除' if re.search('解除|破除',sn) else '添加' if '添加' in sn else '減免'
            for tk in toks:
                for p in re.split(r'[、，及/]',tk):
                    p=norm(p).replace('痲','麻')
                    if len(p)>=2: term_idx.setdefault(p,{}).setdefault(n,(verb,sn[:110]))
    cache={}
    def solve(cat,keys):
        if cat in cache: return
        hits={}
        for k in keys:
            for n,(verb,sn) in term_idx.get(k,{}).items():
                # 每張擁有這個技能的卡都列出（進化前後、不同型態），背包比對才不會漏
                for cid,slot in byskill[n]:
                    kind='隊長技' if slot=='lskill' else '組合技' if slot=='cskill' else '主動技'
                    prev=hits.get(cid)
                    if not prev or (prev['kind']!='主動技' and kind=='主動技'):
                        hits[cid]=dict(id=cid,skill=n,kind=kind,verb=verb,cd=skills[n]['cd'] if kind=='主動技' else '',text=sn)
        lst=sorted(hits.values(),key=lambda h:(h['verb'] not in('無視','解除'),-rare(h['id']),-h['id']))
        tip=next((t for pat,t in TIPS if any(re.search(pat,c) for c in cat.split(' ‧ '))),'')
        cache[cat]=dict(tip=tip,total=len({h['skill'] for h in lst}),cards=lst)

    events=discover();print('events:',[e['page'] for e in events],flush=True)
    pages=batch([e['page'] for e in events]);raw=[]
    for e in events:
        if e['page'] in pages: raw+=parse_page(e,pages[e['page']])
    if not raw: sys.exit('找不到任何本期關卡，中止（不覆寫舊資料）')
    est={k.split('ES')[1]:fields(v) for k,v in batch(['Template:ES'+x for x in sorted({x for s in raw for r in s['rows'] for x in r['es']})]).items()}
    mons={k.split(':')[1]:fields(v) for k,v in batch(['Template:'+m for m in sorted({r['mid'] for s in raw for r in s['rows']})]).items()}
    stages=[];ids=set();files={};allcats={}
    for s in raw:
        used=[];floors=[]
        for r in s['rows']:
            es=[]
            for x in r['es']:
                t=est.get(x)
                if not t: continue
                cat,title,keys=es_keys(t)
                if not cat: continue
                if cat not in used: used.append(cat)
                solve(cat,keys);icon=t.get('icon','')
                es.append(dict(id=x,cat=cat,title=title or cat,effect=clean(t.get('effect','')),icon=icon))
                if icon and ',' not in icon: files[icon]='es_'+icon
            m=mons.get(r['mid'],{})
            en=dict(id=int(r['mid']),name=m.get('name','？'),attr=r['attr'] or m.get('attr',''),hp=r['hp'],df=r['df'],cd=r['cd'],es=es)
            files[f"{en['id']}i.png"]=f"c{en['id']}.png"
            if floors and floors[-1]['floor']==r['floor']: floors[-1]['enemies'].append(en)
            else: floors.append(dict(floor=r['floor'],enemies=[en]))
        if not used: continue
        for f in floors: f['phases']=len(f['enemies'])>1 and len({e['id'] for e in f['enemies']})==1
        for c in used: allcats[c]=cache[c]
        ids|={h['id'] for c in used for h in cache[c]['cards']}
        files[f"{s['boss']}i.png"]=f"c{s['boss']}.png"
        stages.append({k:s[k] for k in('page','name','kind','boss','sta','open','soon','limit','ach')}|dict(battle=len(floors),cats=used,floors=floors))
    out_cards={i:{k:cards[str(i)].get(k,'') for k in('name','attr','race','rare')} for i in ids}
    files.update({f'{i}i.png':f'c{i}.png' for i in ids})
    # 卡圖：只抓還沒有的
    files={k:v for k,v in files.items() if not os.path.exists(os.path.join(IMG,v))}
    lower={k.lower().replace(' ','_'):k for k in files};tl=['File:'+f for f in files];jobs=[]
    for i in range(0,len(tl),50):
        d=api(action='query',titles='|'.join(tl[i:i+50]),prop='imageinfo',iiprop='url',iiurlwidth=160)
        for p in d.get('query',{}).get('pages',{}).values():
            ii=p.get('imageinfo');key=lower.get(p['title'].split(':',1)[1].replace(' ','_').lower())
            if ii and key: jobs.append((ii[0].get('thumburl') or ii[0]['url'],os.path.join(IMG,files[key])))
    def dl(j):
        try:
            rq=urllib.request.Request(j[0],headers={**UA,'Accept':'image/webp,image/png,image/*'})
            open(j[1],'wb').write(urllib.request.urlopen(rq,timeout=40).read())
        except Exception as e: print('  圖片失敗',os.path.basename(j[1]),e)
    with ThreadPoolExecutor(8) as ex: list(ex.map(dl,jobs))
    data=dict(updated=NOW.strftime('%Y/%m/%d %H:%M'),stages=stages,cats=allcats,cards=out_cards,indexed=len(cards),nskills=len(skills))
    tmp=os.path.join(OUT,'data.js.tmp')
    open(tmp,'w',encoding='utf-8').write('window.DATA='+json.dumps(data,ensure_ascii=False,separators=(',',':'))+';')
    os.replace(tmp,os.path.join(OUT,'data.js'))
    for s in stages: print(f"  {s['kind'] or '-':8} {s['name']} | {s['battle']} 層 {len(s['cats'])} 敵技 {sum(allcats[c]['total']>0 for c in s['cats'])} 可解 {s['open']}")
    print(f"done: {len(stages)} stages, {len(allcats)} cats, {len(out_cards)} cards, +{len(jobs)} images, data.js {os.path.getsize(os.path.join(OUT,'data.js'))//1024} KB")
if __name__=='__main__': main()
