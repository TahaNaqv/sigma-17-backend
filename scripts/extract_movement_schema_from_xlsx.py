"""One-off: extract SAMA IFRS17 movement template structure from template.xlsx into a
faithful normalized schema_source.json. Re-run only when SAMA revises the template."""
import json, re, zipfile

import sys
SRC = sys.argv[1] if len(sys.argv) > 1 else "template.xlsx"
OUT = sys.argv[2] if len(sys.argv) > 2 else str(__import__("pathlib").Path(__file__).resolve().parent.parent / "module2_engine" / "movement" / "schema_source.json")
SCHEMA_VERSION = "2026.06"

z = zipfile.ZipFile(SRC)
def unescape(s):
    return (s.replace("&amp;","&").replace("&lt;","<").replace("&gt;",">").replace("&#10;"," ").replace("\n"," "))
strings=[unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))) for si in re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode(), re.S)]

def parse_sheet(path):
    cells={}
    for m in re.finditer(r'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', z.read(path).decode(), re.S):
        col,row,attrs,inner=m.group(1),int(m.group(2)),m.group(3),(m.group(4) or "")
        t=re.search(r't="([^"]+)"',attrs); t=t.group(1) if t else None
        fm=re.search(r'<f[^>]*>(.*?)</f>',inner,re.S); formula=unescape(fm.group(1)) if fm else None
        vm=re.search(r'<v>(.*?)</v>',inner,re.S); val=vm.group(1) if vm else None
        cells[(col,row)]={"t":t,"f":formula,"v":val}
    return cells
def disp(cell):
    if not cell: return None
    if cell["t"]=="s" and cell["v"] is not None: return strings[int(cell["v"])]
    if cell["f"]: return "="+cell["f"]
    return cell["v"]
SIGNS={"+","-","+/-","-/+"}
SHEETS={
 "Gross":("xl/worksheets/sheet2.xml","A",{"C":"LRC_excl_LC","E":"Loss_Component","G":"LIC_excl_RA","I":"Risk_Adjustment","J":"Total"},{"B":"C","D":"E","F":"G","H":"I"}),
 "RI":("xl/worksheets/sheet1.xml","B",{"D":"Assets_Remaining_Coverage","F":"Loss_Recovery_Component","H":"Amounts_Recoverable_IC","J":"Risk_Adjustment","K":"Total"},{"C":"D","E":"F","G":"H","I":"J"}),
}
def slug(label):
    return re.sub(r"[^a-z0-9]+","_",label.strip().lower()).strip("_") or "line"

def raw_lines(name, spec):
    sheet_xml,label_col,valcols,signcols=spec
    cells=parse_sheet(sheet_xml)
    col2bucket=dict(valcols)
    rows=sorted({r for (c,r) in cells})
    used={}; rowid={}; lines=[]
    for r in rows:
        lc=cells.get((label_col,r))
        if not (lc and lc.get("t")=="s" and lc.get("v") is not None): continue
        raw=strings[int(lc["v"])]; label=unescape(raw).strip()
        if not label: continue
        lead=len(raw)-len(raw.lstrip(" ")); level=lead//3
        signs={}; formulas={}
        for vc,bucket in valcols.items():
            cell=cells.get((vc,r))
            for sc,target in signcols.items():
                if target==vc:
                    sv=disp(cells.get((sc,r)))
                    if sv in SIGNS: signs[bucket]=sv
            if cell and cell["f"]: formulas[bucket]="="+cell["f"]
        base=slug(label); sid=base; i=2
        while sid in used: sid=f"{base}_{i}"; i+=1
        used[sid]=True; rowid[r]=sid
        lines.append({"id":sid,"row":r,"label":label,"level":level,"signs":signs,"_rawformulas":formulas})
    # translate formulas -> internal refs
    def translate(expr):
        body=expr[1:] if expr.startswith("=") else expr
        refs=[]; internal=False
        for cl1,r1,cl2,r2 in re.findall(r"\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)", body):
            if cl1 in col2bucket:
                ids=[rowid[rr] for rr in range(int(r1),int(r2)+1) if rr in rowid]
                if ids: internal=True
                refs.append({"op":"sum_range","bucket":col2bucket.get(cl1),"lines":ids})
        tmp=re.sub(r"\$?[A-Z]+\$?\d+:\$?[A-Z]+\$?\d+","",body)
        for cl,rr in re.findall(r"\$?([A-Z]+)\$?(\d+)", tmp):
            if cl in col2bucket and int(rr) in rowid:
                internal=True; refs.append({"op":"cell","bucket":col2bucket.get(cl),"line":rowid.get(int(rr))})
        return {"excel":expr,"refs":refs,"internal":internal}
    for ln in lines:
        ln["formulas"]={b:translate(f) for b,f in ln["_rawformulas"].items()}
        # a line is structurally aggregating if any NON-Total bucket formula refs other lines
        ln["_agg"]=any(ln["formulas"][b]["internal"] for b in ln["formulas"] if b!="Total")
        del ln["_rawformulas"]
    return {"buckets":[valcols[c] for c in valcols],
            "value_buckets":[valcols[c] for c in valcols if valcols[c]!="Total"],
            "lines":lines}

sheets={name:raw_lines(name,spec) for name,spec in SHEETS.items()}
# subtotal labels = any line that aggregates internally in EITHER sheet (skeletons align by label)
subtotal_labels={ln["label"] for sh in sheets.values() for ln in sh["lines"] if ln["_agg"]}
for name,sh in sheets.items():
    for ln in sh["lines"]:
        low=ln["label"].lower()
        if "as at 01/01" in low: ln["kind"]="opening"
        elif "as at 30/06" in low: ln["kind"]="closing"
        elif ln["_agg"] or ln["label"] in subtotal_labels: ln["kind"]="subtotal"
        elif not ln["signs"] and not ln["formulas"]: ln["kind"]="section"
        else: ln["kind"]="input"
        del ln["_agg"]
        # drop empty formulas dict / non-internal (lookup) formulas on input lines for cleanliness
        if ln["kind"]=="input":
            ln.pop("formulas",None)
        elif "formulas" in ln:
            ln["formulas"]={b:v for b,v in ln["formulas"].items() if v["internal"]} or None
            if ln["formulas"] is None: ln.pop("formulas")

schema={"schema_version":SCHEMA_VERSION,"source":"SAMA IFRS17 movement template.xlsx",
        "instance_key":["reserving_class","uwy"],"sheets":sheets}
json.dump(schema,open(OUT,"w"),indent=2,ensure_ascii=False)
for name,sh in schema["sheets"].items():
    k={}
    for ln in sh["lines"]: k[ln["kind"]]=k.get(ln["kind"],0)+1
    print(f"{name}: {len(sh['lines'])} lines  {k}")
print("\nGross 'section' lines:", [ln["label"] for ln in schema["sheets"]["Gross"]["lines"] if ln["kind"]=="section"])
print("\nGross subtotals:", [ln["label"] for ln in schema["sheets"]["Gross"]["lines"] if ln["kind"]=="subtotal"])
print("wrote", OUT)
