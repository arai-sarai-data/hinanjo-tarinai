"""避難所に入りきれない人数：国土数値情報「避難施設」の収容人数を市区町村ごとに合計し、
住民基本台帳の人口と比べて「住民の何割が入れるか」「入りきれない人数」を並べる。"""
import io
import re
from collections import defaultdict

import shapefile  # pyshp

from lib import oc

KSJ = "https://nlftp.mlit.go.jp/ksj/gml/"
P20_PAGE = KSJ + "datalist/KsjTmplt-P20.html"
POP_PAGE = "https://www.soumu.go.jp/main_sosiki/jichi_gyousei/daityo/jinkou_jinkoudoutai-setaisuu.html"


def zip_urls(page, pattern):
    html = oc.decode(oc.get(page))
    out = []
    for m in re.finditer(r"""['"]([^'"]*?%s)['"]""" % pattern, html):
        u = m.group(1)
        if not u.startswith("http"):
            u = "https://nlftp.mlit.go.jp" + (u if u.startswith("/") else "/ksj/gml/" + u.lstrip("./"))
        if u not in out:
            out.append(u)
    return out


def read_shp(zb):
    files = oc.unzip(zb)
    shp = [k for k in files if k.lower().endswith(".shp")]
    oc.check(shp, "ZIP内にシェープファイルがありません")
    base = shp[0][:-4]
    f = lambda ext: io.BytesIO(next(v for k, v in files.items() if k.lower() == (base + ext).lower()))
    return shapefile.Reader(shp=f(".shp"), dbf=f(".dbf"), shx=f(".shx"), encoding="cp932")


def population():
    import pandas as pd
    cands = [a for a in oc.anchors(POP_PAGE, r"\.xlsx?$") if "市区町村" in (a[1] + a[2]) and "人口" in (a[1] + a[2])]
    oc.check(cands, "住民基本台帳の市区町村別人口の表が見つかりません")
    u = cands[0][0]
    df = pd.read_excel(io.BytesIO(oc.get(u, cache="pop_" + u.rsplit("/", 1)[-1])), header=None)
    pop = {}
    for r in df.itertuples(index=False):
        cells = list(r)
        code = next((re.sub(r"\D", "", str(c)) for c in cells[:2] if re.fullmatch(r"\d{5,6}", str(c).strip())), None)
        if not code:
            continue
        nums = [oc.num(c) for c in cells[2:] if isinstance(c, (int, float)) or re.fullmatch(r"[\d,]+", str(c).strip() or "x")]
        nums = [n for n in nums if n]
        if nums:
            pop[code[:5]] = (str(cells[2] if len(cells) > 2 else ""), max(nums[:3]))
    oc.check(len(pop) > 1500, "人口表の市区町村が少なすぎます(%d)" % len(pop))
    return u, pop


def run():
    urls = zip_urls(P20_PAGE, r"P20-12_\d\d(_GML)?\.zip")
    oc.check(len(urls) >= 47, "避難施設データのZIPが47都道府県分ありません(%d)" % len(urls))
    cap = defaultdict(int); nfac = defaultdict(int); flood = defaultdict(int)
    for u in urls:
        rd = read_shp(oc.get(u, cache="p20_" + u.rsplit("/", 1)[-1]))
        names = [f[0] for f in rd.fields[1:]]
        oc.check("P20_001" in names and "P20_005" in names, "避難施設の項目名が想定と違います: %s" % names[:8])
        for rec in rd.iterRecords():
            d = dict(zip(names, rec))
            code = re.sub(r"\D", "", str(d["P20_001"]))[:5]
            c = oc.num(d["P20_005"]) or 0
            if len(code) == 5 and 0 <= c < 200000:
                cap[code] += c; nfac[code] += 1
                if str(d.get("P20_009", "")).strip() in ("1", "1.0"):
                    flood[code] += c
        oc.log(u.rsplit("/", 1)[-1], len(cap))
    oc.check(sum(nfac.values()) > 50000, "避難施設の数が少なすぎます")
    pop_url, pop = population()
    rows = []
    for code, (name, p) in pop.items():
        if code not in cap or p < 1000:
            continue
        rows.append({"city": name or code, "pref": oc.pref_name(code), "pop": p, "cap": cap[code], "n": nfac[code],
                     "rate": cap[code] / p, "short": max(0, p - cap[code]), "flood": flood[code] / p})
    oc.check(len(rows) > 1000, "人口と突き合わせられた市区町村が少なすぎます(%d)" % len(rows))
    cols = [{"key": "city", "label": "市区町村"}, {"key": "pref", "label": "都道府県"},
            {"key": "rate", "label": "住民のうち入れる割合", "type": "num", "fmt": "pct", "bar": True},
            {"key": "short", "label": "入りきれない人数", "type": "num"}, {"key": "cap", "label": "収容人数の合計", "type": "num"},
            {"key": "n", "label": "避難施設の数", "type": "num"}, {"key": "pop", "label": "人口", "type": "num"}]
    low = sorted(rows, key=lambda r: r["rate"])
    many = sorted(rows, key=lambda r: -r["short"])
    meth = ("国土数値情報「避難施設」（国土交通省）に記録された施設ごとの収容人数を市区町村ごとに足し、住民基本台帳の人口で割りました。"
            "避難施設データは2012年度時点のもので、その後の指定の追加・解除は反映されていません。"
            "住民全員が同時に避難所に入る想定ではありません。在宅避難・親戚宅などへの避難もあります。比べるための目安です。")
    R = [{"id": "rate", "tab": "入れる割合", "title": "避難施設に入れる住民の割合が低い市区町村",
          "desc": "収容人数の合計÷人口。低いほど、いっせいに避難したときに入りきれない。", "rows": low, "columns": cols, "method": meth,
          "trend": oc.trend(low, "pref", 50, "市区町村", "市区町村"), "share_label": "city", "share_value": "rate"},
         {"id": "short", "tab": "入りきれない人数", "title": "避難施設に入りきれない人数が多い市区町村",
          "desc": "人口−収容人数の合計。", "rows": many, "columns": cols, "method": meth,
          "trend": oc.trend(many, "pref", 50, "市区町村", "市区町村"), "share_label": "city", "share_value": "short"}]
    tp = sum(r["pop"] for r in rows); tc = sum(r["cap"] for r in rows)
    meta = {"data_period": "避難施設：2012年度 / 人口：住民基本台帳（最新）",
            "stats": [{"label": "全国で入れる割合", "value": tc / tp, "fmt": "pct"},
                      {"label": "住民の1割も入れない市区町村", "value": sum(1 for r in rows if r["rate"] < 0.1), "unit": "市区町村"},
                      {"label": "数えた避難施設", "value": sum(nfac.values()), "unit": "か所"}],
            "sources": [{"name": "国土数値情報（避難施設データ）", "org": "国土交通省", "url": P20_PAGE, "license": "CC BY 4.0"},
                        {"name": "住民基本台帳に基づく人口", "org": "総務省", "url": pop_url, "license": "公共データ利用規約"}]}
    oc.write(meta, R, search=[{"n": r["city"], "s": r["pref"], "k": [["入れる割合", "%.1f%%" % (r["rate"] * 100)], ["入りきれない人数", "{:,}".format(int(r["short"]))]]} for r in rows])


if __name__ == "__main__":
    oc.main(run)
