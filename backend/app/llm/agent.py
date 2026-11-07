"""Local-LLM question answering over the vetted tools, with a deterministic fallback router/narrator."""
import json
import os
import re

from . import tools as T

MODEL = os.getenv("OLLAMA_MODEL", "qwen3:1.7b")
HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
USE_LLM = os.getenv("USE_LLM", "1") != "0"

SYSTEM = ("You are an analytics assistant for a pharma real-world-evidence demo on rheumatoid arthritis (synthetic data). "
          "Always answer by calling exactly one tool that best matches the question. Therapy A is adalimumab. "
          "If the user mentions months or years, convert to days (12 months=365). Never invent numbers.")
NARRATE = ("Write a concise answer (3-5 sentences) to the user's question using ONLY the numbers in the JSON data. "
           "Do not add numbers that are not in the data. Mention that the data is synthetic. Plain text, no markdown headers.")

SUGGESTIONS = ["What happens to patients after they initiate Therapy A?", "What are the most common treatment sequences?",
               "How long until patients switch from Therapy A?", "Which patients are most likely to discontinue?",
               "How many patients get hospitalized after leaving Therapy A?", "How long does it take to get diagnosed and treated?",
               "What is the persistence on Therapy A at 12 months?", "Which treatments do patients switch to?",
               "How many patients escalate therapy?", "What are the biggest unmet needs?"]


def _window(q):
    mo = re.search(r"(\d+)\s*(?:-)?\s*(month|mo\b|year|yr|day)", q.lower())
    if not mo:
        return None
    n, u = int(mo.group(1)), mo.group(2)
    return n * 365 if u.startswith(("year", "yr")) else n * 30 if u.startswith("mo") else n if u == "day" else int(n * 30.4)


def rule_route(q: str):
    s, w = q.lower(), _window(q)
    grp = ("sex" if re.search(r"\b(sex|gender|female|male|women|men)\b", s) else "comorbid" if "comorbid" in s
           else "age" if re.search(r"\b(age|aged|ages|older|younger|young|elderly)\b", s) else None)
    if re.search(r"unmet|treatment gap|care gap|opportunit|biggest", s):
        return "get_unmet_need", {}
    if grp or re.search(r"subgroup|which patients|most likely|segment|compare", s):
        return "get_subgroups", {"by": {"comorbid": "comorbidity"}.get(grp, grp or "age"), "window_days": w}
    if re.search(r"escalat|add-on|add on|\badded\b|on top of|intensif", s):
        return "get_escalation", {"window_days": w}
    if re.search(r"persist|surviv|kaplan|\b(stay|remain) on\b|retention", s):
        return "get_survival", {"event": "persistence"}
    if re.search(r"diagnos|specialist|rheumatolog|time to (first )?treat|initiation rate|start(ing)? treat", s):
        return "get_diagnosis_and_treatment_timing", {}
    if re.search(r"adheren|pdc|days covered", s):
        return "get_funnel", {"window_days": w}
    if re.search(r"utili[sz]ation|cost|er visit|emergency|healthcare use", s):
        return "get_utilization", {}
    if re.search(r"sequence|pathway|sankey|line of therapy|lines of|switch(ed)? to (what|which)|which (treatment|therap|drug|biologic)s?|switch to\?|destination|second.line", s):
        return "get_pathways", {"max_lines": 3}
    if re.search(r"how long.*switch|time to switch", s):
        return "get_funnel", {"window_days": w}
    if re.search(r"discontinu|stop", s) and re.search(r"curve|over time|cumulative", s):
        return "get_survival", {"event": "discontinuation"}
    if re.search(r"after|initiat|hospital|switch|discontinu|remain|happen|funnel|journey", s):
        return "get_funnel", {"window_days": w}
    return None


def template_answer(tool: str, d: dict) -> str:
    syn = " (Synthetic data.)"
    if tool == "get_funnel":
        p = d["pct"]
        sw = d["median_days_to_switch_among_switchers"]
        dest = ", ".join(f"{k} ({v})" for k, v in list(d["switch_destinations"].items())[:3])
        return (f"Of {d['evaluable_patients']:,} adalimumab (Therapy A) initiators evaluable at {d['window_days']} days, "
                f"{d['remain_on_therapy']:,} ({p['remain']}%) remain on therapy, {d['discontinued']:,} ({p['discontinued']}%) discontinue and "
                f"{d['switched']:,} ({p['switched']}%) switch to another advanced therapy"
                + (f" (mainly {dest})" if dest else "") + f". {d['hospitalized']:,} ({p['hospitalized']}%) are hospitalized"
                + (f"; the median time to switch among switchers is {sw:.0f} days." if sw else ".")
                + f" {d['hospitalized_after_leaving_therapy']:,} were admitted after leaving Therapy A, and "
                f"{d['adherence_pct_pdc_80']}% had good adherence (PDC >= 80%)." + syn)
    if tool == "get_pathways":
        tp = d["top_pathways"][:4]
        return "Most common treatment sequences: " + "; ".join(f"{x['path']} ({x['n']:,}, {x['pct']}%)" for x in tp) + "." + syn
    if tool == "get_survival":
        o = d.get("overall")
        if o:
            return (f"{d['definition']} At 6, 12 and 24 months: {o['at_180']:.1%}, {o['at_365']:.1%}, {o['at_730']:.1%} "
                    f"(n={o['n']:,}, events={o['events']:,})." + syn)
        return d["definition"] + " Grouped curves are shown in the chart." + syn
    if tool == "get_utilization":
        return (f"Across {d['patient_years']:,.0f} patient-years: {d['hospitalizations_per_100py']} hospitalizations per 100 patient-years "
                f"({d['ra_hospitalizations_per_100py']} RA-related), {d['er_per_100py']} ER visits per 100 patient-years, "
                f"{d['outpatient_per_py']} outpatient visits per patient-year, mean cost ${d['mean_cost_per_patient_year']:,.0f} per patient-year." + syn)
    if tool == "get_diagnosis_and_treatment_timing":
        a, b, c, i = (d["time_to_diagnosis_days"], d["time_to_specialist_days"], d["time_to_treatment_days"], d["treatment_initiation_rate"])
        return (f"Median time from symptom onset to RA diagnosis is {a['median']:.0f} days, from first visit to a rheumatologist {b['median']:.0f} days, "
                f"and from diagnosis to first DMARD {c['median']:.0f} days. {i['pct_within_window']}% start a DMARD within {i['window_days']} days." + syn)
    if tool == "get_escalation":
        return (f"{d['escalated']:,} of {d['n']:,} patients ({d['pct']}%) escalate within {d['window_days']} days"
                + (f", at a median of {d['median_days_to_escalation']:.0f} days" if d["median_days_to_escalation"] else "")
                + f". Agents added: {d['agents_added']}." + syn)
    if tool == "get_subgroups":
        rows = "; ".join(f"{g['group']}: {g['pct_remain']}% remain, {g['pct_discontinued']}% discontinue, {g['pct_switched']}% switch, {g['pct_hospitalized']}% hospitalized (n={g['n']:,})"
                         for g in d["groups"])
        return f"By {d['by']} at {d['window_days']} days - {rows}." + syn
    if tool == "get_unmet_need":
        return "Unmet-need signals: " + "; ".join(f"{f['label']}: {f['n']:,} ({f['pct']}%)" for f in d["flags"]) + "." + syn
    return (f"{d['index_patients']:,} patients initiated Therapy A; at {d['window_days']} days {d['pct_persistent']}% remain, "
            f"{d['pct_discontinued']}% discontinue, {d['pct_switched']}% switch and {d['pct_hospitalized']}% are hospitalized." + syn)


def _nums(text):
    return {round(float(x.replace(",", "")), 1) for x in re.findall(r"\d[\d,]*\.?\d*", text) if x.replace(",", "").replace(".", "", 1).isdigit()}


def numbers_grounded(answer: str, payload, question: str) -> bool:
    allowed = _nums(json.dumps(payload, default=str)) | _nums(question)
    allowed |= {round(a * 100, 1) for a in allowed if a < 1.5} | {round(a / 100, 1) for a in allowed} | {a / 12 for a in allowed}
    for n in _nums(answer):
        if n in allowed or n in (6, 12, 24, 3, 2, 1, 100, 365, 180, 730) or round(n) in {round(a) for a in allowed}:
            continue
        return False
    return True


def _client():
    import ollama
    return ollama.Client(host=HOST, timeout=45)


def ask(question: str) -> dict:
    tool, args, engine, err = None, {}, "rules", None
    routed = rule_route(question)
    if routed:
        tool, args = routed[0], {k: v for k, v in routed[1].items() if v is not None}
        engine = "rules+llm" if USE_LLM else "rules"
    elif USE_LLM:
        try:
            c = _client()
            r = c.chat(model=MODEL, messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}],
                       tools=T.ollama_tools(), think=False, options={"temperature": 0, "num_ctx": 4096}, keep_alive="30m")
            calls = r.message.tool_calls or []
            if calls and calls[0].function.name in T.TOOLS:
                tool, args = calls[0].function.name, dict(calls[0].function.arguments or {})
                T.TOOLS[tool][0](**args)  # validate
                engine = "llm"
        except Exception as e:  # noqa: BLE001 - any LLM failure falls back to the deterministic router
            err, tool = str(e)[:200], None
    if tool is None:
        tool, args = "get_overview", {}
    data = T.run_tool(tool, args)
    answer, narration = template_answer(tool, data), "template"
    if USE_LLM and engine != "rules":
        try:
            slim = {k: v for k, v in data.items() if k not in ("sunburst", "nodes", "links")} if isinstance(data, dict) else data
            if tool == "get_survival":
                slim = json.loads(json.dumps(data, default=str))
                for g in ([slim.get("overall")] if slim.get("overall") else list(slim.get("groups", {}).values())):
                    for k in ("x", "y", "lo", "hi"):
                        g.pop(k, None)
            n = _client().chat(model=MODEL, messages=[{"role": "system", "content": NARRATE},
                                                       {"role": "user", "content": f"Question: {question}\nData: {json.dumps(slim, default=str)[:6000]}"}],
                               think=False, options={"temperature": 0.1, "num_ctx": 4096}, keep_alive="30m")
            txt = (n.message.content or "").strip()
            if txt and numbers_grounded(txt, slim, question):
                answer, narration = txt, "llm"
        except Exception as e:  # noqa: BLE001
            err = str(e)[:200]
    return {"question": question, "tool": tool, "args": args, "answer": answer, "data": data, "engine": engine,
            "narration": narration, "model": MODEL if engine != "rules" else None, "error": err}


def warm_up():
    try:
        _client().chat(model=MODEL, messages=[{"role": "user", "content": "hi"}], think=False, keep_alive="30m", options={"num_ctx": 4096})
    except Exception:  # noqa: BLE001
        pass
