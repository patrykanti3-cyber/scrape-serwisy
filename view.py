import json, sys
sys.stdout.reconfigure(encoding="utf-8")
path = sys.argv[1]
for l in open(path, encoding="utf-8"):
    r = json.loads(l)
    print("="*80)
    print(f"[{r['source_type']}] {r['source_name']}")
    print("TITLE:", r["title"])
    print("URL  :", r["source_url"])
    print("IMG  :", r["image_url"][:100])
    print("PUB  :", r["published"])
    print("LEAD :", r["lead"][:200])
    print("BODY :", len(r["body"]), "chars ::", r["body"][:300].replace("\n"," "))
