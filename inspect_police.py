import re, sys
sys.stdout.reconfigure(encoding="utf-8")
h = open("police_art.html", encoding="utf-8").read()
print("len", len(h))
for kw in ['articleBody','itemprop','article-area','dokument','newsContent','field--name-body',
           'class="text','id="tresc','og:image','class="foto','class="news','<article','data-page-type',
           'class="article','enclosure','class="content','plain-text','wydruk','print']:
    m = re.search(re.escape(kw), h)
    if m:
        i = m.start()
        print(f"\n[{kw}] @ {i}")
        print("   ", repr(h[i-60:i+110]))
