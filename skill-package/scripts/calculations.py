"""Recompute explicit arithmetic certificates from authenticated source cells.

No guessed ranges, sample-specific values, exchange rates or expression eval.
"""
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import re
import factguard as f

def calculate(cert,sources,validate):
    cites,issues=validate(cert.get("evidence",[]),sources)
    if issues or not cites:raise ValueError("invalid_calculation_evidence")
    op=cert.get("operation")
    if op=="growth_percent":
        vals=cert.get("amounts",[])
        if len(vals)!=2:raise ValueError("growth_needs_target_and_base")
        normalized=[]
        for item in vals:
            literal=str(item["number"]);unit=item["unit"]
            if not any(re.search(r"(?<![\d.])"+re.escape(literal)+r"\s*"+re.escape(unit),e["quote"]) for e in cites):
                raise ValueError("growth_amount_not_in_quote")
            norm=f.normalized_measure(literal,unit)
            if norm is None:raise ValueError("growth_unit_unknown")
            normalized.append(norm)
        if normalized[0][0]!=normalized[1][0] or normalized[1][1]==0:raise ValueError("growth_dimensions_or_base_invalid")
        value=(normalized[0][1]/normalized[1][1]-1)*100
        value=value.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
        result={"value":str(value),"unit":"%","caption":"按所列目标金额和基期金额回算的增长率"}
    else:
        header_cites,errors=validate([cert.get("header",{})],sources)
        if errors or len(header_cites)!=1:raise ValueError("calculation_header_invalid")
        header=header_cites[0]
        h=re.fullmatch(r"工作表(.+)!([A-Z]+)1",header["locator"])
        if h is None:raise ValueError("calculation_requires_first_row_column_header")
        rows=[]
        for c in cites:
            m=re.fullmatch(r"工作表(.+)!([A-Z]+)(\d+)",c["locator"])
            if not m or c["source_id"]!=header["source_id"] or m.group(1)!=h.group(1) or m.group(2)!=h.group(2) or int(m.group(3))<2:
                raise ValueError("calculation_cell_header_binding_invalid")
            rows.append(int(m.group(3)))
        if len(set(rows))!=len(rows) or sorted(rows)!=list(range(min(rows),max(rows)+1)):
            raise ValueError("calculation_requires_unique_contiguous_rows")
        if op=="frequency":
            result={"value":dict(sorted(Counter(e["quote"] for e in cites).items())),"unit":"条记录","caption":header["quote"]}
        else:
            unit=cert.get("unit","")
            if not unit or unit not in header["quote"]:raise ValueError("calculation_unit_not_in_header")
            numbers=[Decimal(e["quote"].replace(",","")) for e in cites]
            if op=="sum":value=sum(numbers,Decimal(0))
            elif op=="mean":value=sum(numbers,Decimal(0))/len(numbers)
            elif op=="count":value=Decimal(len(numbers))
            else:raise ValueError("unknown_calculation_operation")
            result={"value":format(value,"f"),"unit":unit,"caption":header["quote"]}
        result["range"]="工作表%s!%s%d:%s%d"%(h.group(1),h.group(2),min(rows),h.group(2),max(rows))
        result["record_count"]=len(rows)
    if result["value"]!=cert.get("value"):raise ValueError("derived_value_mismatch")
    result["filename"]=cites[0]["filename"]
    result["locator"]=result.get("range",cites[0]["locator"])
    result["operation"]=op
    return result

def reference(result):
    return "【%s，%s，程序回算】"%(result["filename"],result["locator"])
