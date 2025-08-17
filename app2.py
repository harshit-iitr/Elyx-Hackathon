# Elyx Member Journey – Streamlit App (filtered internal metrics)
# Full Streamlit app with role inference and Internal Metrics UI that excludes Member and unnamed roles.

from __future__ import annotations
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import re
import os
from datetime import datetime, date, timedelta, time as dtime
from io import BytesIO

try:
    from docx import Document
except Exception:
    Document = None

st.set_page_config(page_title="Elyx Member Journey", layout="wide")

# Minimal CSS
CUSTOM_CSS = """
:root { --radius-xl: 12px; }
.block { background: #ffffff; border-radius: var(--radius-xl); padding: 1rem; box-shadow: 0 1px 6px rgba(0,0,0,0.06); }
.kpi { text-align:center; padding: 0.6rem 0.8rem; border-radius: 10px; background: #f8fafb; color: #111111; }
.kpi div:first-child { font-weight: 700; color: #0b1220; }
.kpi .small { color:#6b7280; font-size:0.86rem; }
.small { color:#6b7280; font-size: 0.86rem; }
.stButton>button { border-radius: 8px; }
"""
st.markdown(f"<style>{CUSTOM_CSS}</style>", unsafe_allow_html=True)

# Heuristics
ROLE_MAP = {
    'Ruby': 'Concierge',
    'Neel': 'Concierge Lead',
    'Dr. Warren': 'Physician',
    'Carla': 'Nutritionist',
    'Rachel': 'Physiotherapist',
    'Advik': 'Performance Scientist',
    'Lab Tech': 'Lab',
}

LAB_PATTERNS = [
    (r"LDL[^\d]*(\d{2,3})", "LDL"),
    (r"HDL[^\d]*(\d{2,3})", "HDL"),
    (r"Triglycerides?[^\d]*(\d{2,3})", "Triglycerides"),
    (r"Total Cholesterol[^\d]*(\d{2,3})", "Total Cholesterol"),
    (r"ApoB[^\d]*(\d{1,3})", "ApoB"),
    (r"hs?-?CRP[^\d]*(\d+(?:\.\d+)?)", "hs-CRP"),
    (r"BP[^\d]*(\d{2,3})/(\d{2,3})", "Blood Pressure"),
    (r"VO[₂2]?max[^\d]*(\d+(?:\.\d+)?)", "VO2max"),
    (r"HRV[^\d]*(\d+(?:\.\d+)?)", "HRV"),
]

DECISION_KEYWORDS = [
    'start','add','begin','initiate','reduce','increase','switch','replace',
    'schedule','book','recheck','test','panel','scan','session','hiit',
    'supplement','vitamin','omega-3','d3','plan','target','goal','adjust','prescribe'
]

CITY_KEYWORDS = ["london","new york","nyc","jakarta","seoul","paris","dubai","tokyo","delhi","mumbai","bangkok","hong kong","sydney","los angeles","chicago","san francisco","toronto","berlin","rome","madrid","zurich","amsterdam","bali"]

EXERCISE_WORDS = ['workout','exercise','training','session','gym','run','jog','walk','steps','cycle','cycling','ride','swim','swimming','row','rowing','elliptical','treadmill','yoga','pilates','hiit','strength','weights','lifting','hike','tennis','badminton','football','cricket']

# Time parsing
TIME_RANGE_SEP = r"(?:\-|\u2013|\u2014|to)"
TIME_TOKEN = r"(?:\d{1,2}(?::|\.)\d{2}(?:\s*(?:am|pm))?|\d{1,2}\s*(?:am|pm)|\d{2}:\d{2}|\d{1,2})"
RANGE_RE = re.compile(fr"(?i)\b({TIME_TOKEN})\s*{TIME_RANGE_SEP}\s*({TIME_TOKEN})\b")
HMS_RE = re.compile(r"(?i)\b(\d{1,2})\s*h(?:ours?)?\s*(\d{1,2})\s*m(?:in(?:s|utes)?)?\b")
HOUR_ONLY_RE = re.compile(r"(?i)\b(\d+(?:\.\d+)?)\s*h(?:(?:ours?)|rs|r)?\b")
MIN_ONLY_RE = re.compile(r"(?i)\b(\d{1,3})\s*m(?:in(?:s|utes)?)?\b")


def _parse_time_token(tok: str) -> int | None:
    if not tok:
        return None
    s = tok.strip().lower().replace('.', ':')
    ampm = None
    m = re.search(r"\b(am|pm)\b", s)
    if m:
        ampm = m.group(1)
        s = re.sub(r"\s*(am|pm)\b", "", s)
    if ':' in s:
        parts = s.split(':')
        try:
            h = int(parts[0]); mnt = int(parts[1]) if len(parts) > 1 else 0
        except Exception:
            return None
    else:
        try:
            h = int(re.findall(r"\d+", s)[0]); mnt = 0
        except Exception:
            return None
    if ampm:
        h = h % 12
        if ampm == 'pm':
            h += 12
    if 0 <= h <= 23 and 0 <= mnt <= 59:
        return h*60 + mnt
    return None


def parse_time_range_minutes(text: str) -> tuple[int | None, int | None, int | None]:
    for m in RANGE_RE.finditer(text):
        a, b = m.group(1), m.group(2)
        sa = _parse_time_token(a); sb = _parse_time_token(b)
        if sa is None or sb is None:
            continue
        dur = sb - sa
        if dur <= 0:
            dur += 24*60
        return sa, sb, dur
    return None, None, None


def parse_duration_minutes(text: str) -> int | None:
    m = HMS_RE.search(text)
    if m:
        h, mm = int(m.group(1)), int(m.group(2))
        return h*60 + mm
    m = HOUR_ONLY_RE.search(text)
    if m:
        h = float(m.group(1))
        return int(round(h*60))
    m = MIN_ONLY_RE.search(text)
    if m:
        return int(m.group(1))
    return None

# Parsers
@st.cache_data(show_spinner=False)
def parse_csv_messages(file_bytes_or_path) -> pd.DataFrame:
    if isinstance(file_bytes_or_path, (bytes, bytearray)):
        df = pd.read_csv(BytesIO(file_bytes_or_path), dtype=str, keep_default_na=False)
    else:
        df = pd.read_csv(file_bytes_or_path, dtype=str, keep_default_na=False)
    cols_lower = {c.lower(): c for c in df.columns}
    ts_col = None
    for candidate in ['timestamp','datetime','date_time','time_stamp','created_at']:
        if candidate in cols_lower:
            ts_col = cols_lower[candidate]; break
    if ts_col:
        df['timestamp'] = pd.to_datetime(df[ts_col], errors='coerce', dayfirst=True)
    else:
        date_col = None; time_col = None
        for d in ['date','day']:
            if d in cols_lower:
                date_col = cols_lower[d]; break
        for t in ['time','hour']:
            if t in cols_lower:
                time_col = cols_lower[t]; break
        if date_col and time_col:
            df['timestamp'] = pd.to_datetime(df[date_col].astype(str).str.strip() + ' ' + df[time_col].astype(str).str.strip(), errors='coerce', dayfirst=True)
        elif date_col:
            df['timestamp'] = pd.to_datetime(df[date_col], errors='coerce', dayfirst=True)
        else:
            df['timestamp'] = pd.NaT
    sender_col = None
    for s in ['sender','author','from','speaker','name']:
        if s in cols_lower:
            sender_col = cols_lower[s]; break
    if sender_col:
        df['sender'] = df[sender_col].astype(str).str.strip()
    else:
        df['sender'] = ''
    role_col = None
    for r in ['role','role_name','speaker_role']:
        if r in cols_lower:
            role_col = cols_lower[r]; break
    if role_col:
        df['role'] = df[role_col].astype(str).str.strip()
    else:
        df['role'] = ''
    text_col = None
    for t in ['text','message','body','content']:
        if t in cols_lower:
            text_col = cols_lower[t]; break
    if text_col:
        df['text'] = df[text_col].astype(str).str.strip()
    else:
        used = {ts_col, sender_col, role_col}
        fallback = next((c for c in df.columns if c not in used), None)
        if fallback:
            df['text'] = df[fallback].astype(str).str.strip()
        else:
            df['text'] = ''
    def split_sender_role(s: str):
        s = (s or '').strip()
        if '(' in s and ')' in s:
            try:
                name = s[:s.find('(')].strip()
                role_part = s[s.find('(')+1:s.rfind(')')].strip()
                if '/' in role_part:
                    role_part = role_part.split('/')[0].strip()
                return name, role_part
            except Exception:
                pass
        return s, ''
    parsed = df['sender'].apply(split_sender_role)
    df['sender_clean'] = parsed.apply(lambda x: x[0])
    df['role_from_sender'] = parsed.apply(lambda x: x[1])
    df['role'] = df.apply(lambda r: r['role'] if r['role'] else (r['role_from_sender'] if r['role_from_sender'] else ''), axis=1)
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df['date'] = df['timestamp'].dt.date
    df['time'] = df['timestamp'].dt.strftime('%H:%M').fillna('')
    df['sender'] = df['sender_clean'].fillna(df['sender']).replace('', 'Unknown')
    df['role'] = df['role'].fillna('').astype(str)
    df['text'] = df['text'].fillna('')
    df = df.sort_values('timestamp', na_position='last').reset_index(drop=True)
    df.drop(columns=['sender_clean','role_from_sender'], inplace=True, errors='ignore')
    return df

# Feature extraction
@st.cache_data(show_spinner=False)
def extract_events(messages: pd.DataFrame) -> pd.DataFrame:
    if messages.empty:
        return pd.DataFrame(columns=["timestamp","date","type","title","detail","sender","role"]) 
    events = []
    def is_summary_text(text: str) -> bool:
        if not text:
            return False
        low = text.lower()
        if 'weekly summary' in low or 'week summary' in low or 'weekly progress summary' in low:
            return True
        if re.search(r"\bweekly\b", low) and re.search(r"\b(update|report|progress|summary|check)\b", low):
            return True
        if re.search(r"summary(.{0,50})week", low) or re.search(r"week(.{0,50})summary", low):
            return True
        if re.search(r"\bweek(?:'s)?\b", low) and re.search(r"\b(summary|update|report)\b", low):
            return True
        if 'weekly' in low and any(keyword in low for keyword in ['summary','update','report','progress','check','notes']):
            return True
        return False
    ROLE_KEYWORDS_LOCAL = {
        'concierge':'Concierge','orchestrator':'Concierge','ruby':'Concierge',
        'concierge lead':'Concierge Lead','neel':'Concierge Lead',
        'physician':'Physician','doctor':'Physician','dr.':'Physician','warren':'Physician',
        'performance':'Performance Scientist','advik':'Performance Scientist',
        'nutrition':'Nutritionist','carla':'Nutritionist',
        'physio':'Physiotherapist','physiotherapist':'Physiotherapist','pt':'Physiotherapist','rachel':'Physiotherapist',
        'lab':'Lab','lab tech':'Lab'
    }
    def infer_sender_and_role(sender_field: str, existing_role: str | None) -> tuple[str,str]:
        """Return (cleaned_sender_name, canonical_role).
        Logic:
         - If sender string contains parentheses, prefer the name inside/outside as before.
         - If role text (from CSV role column or parentheses) contains keywords, use that.
         - If role is missing, try exact-name mapping (Ruby, Dr Warren, Advik, Carla, Rachel, Neel).
         - Otherwise fall back to keyword matching in name or role_text and finally 'Member'.
        """
        s = (sender_field or '').strip()
        # normalize common "Dr." variants and remove trailing slashes
        s_norm = re.sub(r"\bdr\.?\s+", "dr ", s, flags=re.IGNORECASE)
        name = s_norm
        role_text = (existing_role or '').strip()
        # pull out parentheses if present
        m = re.match(r"^([^\(]+)\(([\^)]+)\)", s_norm)
        if m:
            name = m.group(1).strip()
            role_text = m.group(2).strip()
        # clean name: remove honorifics like Dr., Mr., Ms.
        clean_name = re.sub(r"\b(dr|mr|ms|mrs|prof)\.?\s*", "", name, flags=re.IGNORECASE).strip()
        low_name = (clean_name or '').lower()
        low_role_txt = (role_text or '').lower()

        # explicit exact-name map (covers cases where brackets are empty)
        NAME_ROLE_MAP = {
            'ruby': 'Concierge',
            'dr warren': 'Physician',
            'dr. warren': 'Physician',
            'warren': 'Physician',
            'advik': 'Performance Scientist',
            'carla': 'Nutritionist',
            'rachel': 'Physiotherapist',
            'neel': 'Concierge Lead',
            'lab tech': 'Lab',
            'lab': 'Lab'
        }
        # check exact name matches first
        for k, v in NAME_ROLE_MAP.items():
            if low_name == k:
                return (clean_name or k.title(), v)
        # check if name contains one of the known names (partial match)
        for k, v in NAME_ROLE_MAP.items():
            if k in low_name:
                return (clean_name or name, v)

        # keyword mapping fallback
        ROLE_KEYWORDS_LOCAL = {
            'concierge':'Concierge','orchestrator':'Concierge','ruby':'Concierge',
            'concierge lead':'Concierge Lead','neel':'Concierge Lead',
            'physician':'Physician','doctor':'Physician','dr.':'Physician','warren':'Physician',
            'performance':'Performance Scientist','advik':'Performance Scientist',
            'nutrition':'Nutritionist','carla':'Nutritionist',
            'physio':'Physiotherapist','physiotherapist':'Physiotherapist','pt':'Physiotherapist','rachel':'Physiotherapist',
            'lab':'Lab','lab tech':'Lab'
        }
        for k,v in ROLE_KEYWORDS_LOCAL.items():
            if k in low_role_txt or k in low_name:
                return (clean_name or name, v)

        # if role_text looks meaningful, use it
        if role_text:
            if '/' in role_text:
                role_text = role_text.split('/')[0].strip()
            return (clean_name or name, role_text.title())

        # fallback to Member
        return (clean_name or name or 'Unknown', 'Member')

    for _, r in messages.iterrows():
        txt = str(r.get('text', ''))
        low = txt.lower()
        sender_field = str(r.get('sender', '')).strip()
        existing_role = str(r.get('role', '')).strip()
        sender_name, norm_role = infer_sender_and_role(sender_field, existing_role)
        if norm_role == 'Lab' and (not sender_name or sender_name.lower() in ['unknown','member']):
            sender_name = 'Lab'
        if ("travel" in low or any(city in low for city in CITY_KEYWORDS)) and "singapore" not in low:
            events.append({'timestamp': r['timestamp'], 'date': r['date'], 'type': 'Travel', 'title': 'Travel / Trip', 'detail': txt, 'sender': sender_name, 'role': norm_role})
        if any(k in low for k in ['book','schedule','panel','test','scan','ecg','cimt','blood draw','mri','ct','ultrasound']):
            events.append({'timestamp': r['timestamp'], 'date': r['date'], 'type': 'Test/Diagnostics', 'title': 'Diagnostics / Scheduling', 'detail': txt, 'sender': sender_name, 'role': norm_role})
        if any(k in low for k in ['diet','meal plan','hiit','supplement','omega-3','vitamin','workout','strength','cardio','mobility','plan','routine','session']):
            events.append({'timestamp': r['timestamp'], 'date': r['date'], 'type': 'Intervention', 'title': 'Plan / Coaching Update', 'detail': txt, 'sender': sender_name, 'role': norm_role})
        if is_summary_text(txt):
            events.append({'timestamp': r['timestamp'], 'date': r['date'], 'type': 'Summary', 'title': 'Weekly Summary', 'detail': txt, 'sender': sender_name, 'role': norm_role})
        # Sleep detection (Garmin etc.) – only keep duration, skip timing tables (Bed → Awake)
        if any(k in low for k in ['sleep', 'tst', 'garmin', 'advik']):
            minutes = parse_duration_minutes(low)
            if minutes is None:
                smin, emin, dur = parse_time_range_minutes(low)
                minutes = dur
            if minutes is not None:
                if minutes < 180:
                    minutes = 180
                events.append({
                    'timestamp': r['timestamp'], 'date': r['date'], 'type': 'Biomarker',
                    'title': 'Sleep Tracking', 'detail': f"Sleep log (normalized): {minutes} min | raw: {txt}", 'sender': sender_name, 'role': norm_role
                })

        if any(k in low for k in EXERCISE_WORDS):
            minutes = None
            dmin = parse_duration_minutes(low)
            if dmin:
                minutes = dmin
            else:
                smin, emin, dur = parse_time_range_minutes(low)
                if dur:
                    minutes = dur
            if minutes is not None:
                if minutes > 180:
                    minutes = 45
                events.append({'timestamp': r['timestamp'], 'date': r['date'], 'type': 'Biomarker', 'title': 'Exercise Tracking', 'detail': f"Exercise log (normalized): {minutes} min | raw: {txt}", 'sender': sender_name, 'role': norm_role})
    ev = pd.DataFrame(events)
    if not ev.empty:
        ev.sort_values('timestamp', inplace=True)
        ev.reset_index(drop=True, inplace=True)
    return ev

@st.cache_data(show_spinner=False)
def extract_labs(messages: pd.DataFrame) -> pd.DataFrame:
    recs = []
    for _, r in messages.iterrows():
        txt = str(r['text'])
        for pat, name in LAB_PATTERNS:
            m = re.search(pat, txt, flags=re.IGNORECASE)
            if m:
                if name == 'Blood Pressure' and len(m.groups()) == 2:
                    syst, diast = m.groups()
                    try:
                        recs.append({'timestamp': r['timestamp'], 'marker': 'SBP', 'value': float(syst)})
                        recs.append({'timestamp': r['timestamp'], 'marker': 'DBP', 'value': float(diast)})
                    except Exception:
                        pass
                else:
                    try:
                        val = float(m.group(1))
                        recs.append({'timestamp': r['timestamp'], 'marker': name, 'value': val})
                    except Exception:
                        pass
    df = pd.DataFrame(recs)
    if not df.empty:
        df['date'] = df['timestamp'].dt.date
        df.sort_values('timestamp', inplace=True)
    return df

@st.cache_data(show_spinner=False)
def extract_sleep_metrics(messages: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in messages.iterrows():
        txt = str(r.get('text',''))
        low = txt.lower()
        if not any(k in low for k in ['sleep','slept','asleep','tst','garmin','advik','bedtime','woke up','wake up']):
            continue
        smin, emin, dur = parse_time_range_minutes(low)
        bedtime_str = waketime_str = ''
        minutes = None
        if smin is not None and emin is not None and dur is not None:
            minutes = dur
            bh, bm = divmod(smin, 60); wh, wm = divmod(emin, 60)
            bedtime_str = f"{bh:02d}:{bm:02d}"
            waketime_str = f"{wh:02d}:{wm:02d}"
        else:
            dmin = parse_duration_minutes(low)
            if dmin:
                minutes = dmin
            else:
                m = re.search(r"(\d+(?:\.\d+)?)\s*hours?", low)
                if m:
                    try:
                        minutes = int(float(m.group(1)) * 60)
                    except Exception:
                        minutes = None
        if minutes is not None:
            if minutes < 180:
                minutes = 180
            if minutes > 16*60:
                minutes = 16*60
            rows.append({'timestamp': r['timestamp'], 'date': r['timestamp'].date() if pd.notna(r['timestamp']) else None, 'bedtime': bedtime_str, 'waketime': waketime_str, 'sleep_minutes': int(minutes), 'sleep_hours': round(minutes/60.0, 2), 'source': r.get('sender','Unknown')})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values('timestamp').dropna(subset=['date'])
    df = df.groupby('date', as_index=False).tail(1).reset_index(drop=True)
    return df

@st.cache_data(show_spinner=False)
def extract_activity_minutes(messages: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in messages.iterrows():
        txt = str(r.get('text',''))
        low = txt.lower()
        if not any(k in low for k in EXERCISE_WORDS):
            continue
        minutes = None
        dmin = parse_duration_minutes(low)
        if dmin:
            minutes = dmin
        else:
            smin, emin, dur = parse_time_range_minutes(low)
            if dur:
                minutes = dur
        if minutes is not None:
            if minutes > 180:
                minutes = 45
            if minutes <= 0:
                continue
            a_type = 'exercise'
            for key in ['hiit','run','jog','walk','cycle','ride','swim','row','elliptical','treadmill','yoga','pilates','strength','weights','gym','hike','tennis','badminton','football','cricket']:
                if key in low:
                    a_type = key
                    break
            rows.append({'timestamp': r['timestamp'], 'date': r['timestamp'].date() if pd.notna(r['timestamp']) else None, 'activity_minutes': int(minutes), 'activity_type': a_type, 'source': r.get('sender','Unknown')})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values('timestamp').dropna(subset=['date'])
    agg = df.groupby('date', as_index=False).agg({'activity_minutes':'sum'})
    last_ts = df.groupby('date')['timestamp'].max().reset_index().rename(columns={'timestamp':'timestamp_last'})
    agg = agg.merge(last_ts, on='date', how='left')
    agg.rename(columns={'timestamp_last':'timestamp'}, inplace=True)
    return agg

@st.cache_data(show_spinner=False)
def merge_biomarkers(lab_df: pd.DataFrame, sleep_df: pd.DataFrame, act_df: pd.DataFrame) -> pd.DataFrame:
    parts = []
    if lab_df is not None and not lab_df.empty:
        parts.append(lab_df[['timestamp','marker','value']].copy())
    if sleep_df is not None and not sleep_df.empty:
        s = sleep_df.copy()
        s['marker'] = 'Sleep (hrs)'
        s.rename(columns={'sleep_hours':'value'}, inplace=True)
        parts.append(s[['timestamp','marker','value']])
    if act_df is not None and not act_df.empty:
        a = act_df.copy()
        a['marker'] = 'Exercise (min)'
        a.rename(columns={'activity_minutes':'value'}, inplace=True)
        parts.append(a[['timestamp','marker','value']])
    if not parts:
        return pd.DataFrame(columns=['timestamp','marker','value'])
    out = pd.concat(parts, ignore_index=True)
    out['date'] = pd.to_datetime(out['timestamp']).dt.date
    out.sort_values('timestamp', inplace=True)
    return out

@st.cache_data(show_spinner=False)
def extract_decisions(messages: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for i, r in messages.iterrows():
        txt = str(r['text'])
        low = txt.lower()
        if any(k in low for k in DECISION_KEYWORDS):
            t0 = r['timestamp'] - timedelta(days=3)
            context = messages[(messages['timestamp'] >= t0) & (messages['timestamp'] <= r['timestamp'])]
            reasons = []
            for _, c in context.iterrows():
                if any(x in c['text'].lower() for x in ['because','so that','to ','due to','shows','panel','result','scan','ldl','crp','hrv','bp','sleep','jet lag','travel']):
                    reasons.append(f"{c['time']} {c['sender']}: {c['text']}")
            rows.append({'timestamp': r['timestamp'], 'date': r['timestamp'].date(), 'decision_text': txt, 'by': r['sender'], 'role': r['role'], 'rationale_snippets': reasons[:6]})
    d = pd.DataFrame(rows)
    if not d.empty:
        d.sort_values('timestamp', inplace=True)
        d.reset_index(drop=True, inplace=True)
    return d

@st.cache_data(show_spinner=False)
def compute_internal_metrics(messages: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    if messages.empty:
        return pd.DataFrame(columns=['role','interactions','est_minutes','est_hours'])
    msgs = messages.copy()
    msgs['day'] = msgs['timestamp'].dt.date
    role_counts = msgs['role'].fillna('Member').value_counts()
    total_rows = len(msgs)
    num_member = int(role_counts.get('Member', 0))
    if total_rows > 0 and (num_member / float(total_rows) > 0.6 or len(role_counts) == 1):
        ROLE_KEYWORDS_LOCAL = {
            'concierge':'Concierge','orchestrator':'Concierge','ruby':'Concierge',
            'concierge lead':'Concierge Lead','neel':'Concierge Lead',
            'physician':'Physician','doctor':'Physician','dr.':'Physician','warren':'Physician',
            'performance':'Performance Scientist','advik':'Performance Scientist',
            'nutrition':'Nutritionist','carla':'Nutritionist',
            'physio':'Physiotherapist','physiotherapist':'Physiotherapist','pt':'Physiotherapist','rachel':'Physiotherapist',
            'lab':'Lab','lab tech':'Lab'
        }
        def infer_role_from_sender(sndr: str, current_role: str):
            if current_role and current_role.lower() not in ['member','unknown','']:
                return current_role
            low = (sndr or '').lower()
            if '(' in sndr and ')' in sndr:
                try:
                    name = sndr[:sndr.find('(')].strip()
                    role_text = sndr[sndr.find('(')+1:sndr.rfind(')')].strip()
                    if '/' in role_text:
                        role_text = role_text.split('/')[0].strip()
                    for k,v in ROLE_KEYWORDS_LOCAL.items():
                        if k in role_text.lower() or k in low:
                            return v
                    return role_text.title() if role_text else 'Member'
                except Exception:
                    pass
            for k, v in ROLE_KEYWORDS_LOCAL.items():
                if k in low:
                    return v
            return 'Member'
        msgs['role'] = msgs.apply(lambda r: infer_role_from_sender(r.get('sender',''), r.get('role','')), axis=1)
    grp = msgs.groupby(['day','role']).size().reset_index(name='count')
    grp['est_minutes'] = grp.apply(lambda r: int(r['count']) * float(weights.get(r['role'], 5)), axis=1)
    by_role = grp.groupby('role').agg(interactions=('count','sum'), est_minutes=('est_minutes','sum')).reset_index()
    by_role['est_hours'] = (by_role['est_minutes'] / 60.0).round(2)
    by_role = by_role.sort_values('est_hours', ascending=False).reset_index(drop=True)
    return by_role

# Mock data and UI (same as original, with internal metrics behaving as above)
@st.cache_data(show_spinner=False)
def load_mock_messages() -> pd.DataFrame:
    data = [
        {"timestamp": datetime(2025,8,15,9,2), "date": date(2025,8,15), "time":"09:02", "sender":"Rohan", "role":"Member", "text":"Hi Ruby, I’m Rohan Patel. Just signed up yesterday."},
        {"timestamp": datetime(2025,8,19,9,12), "date": date(2025,8,19), "time":"09:12", "sender":"Ruby", "role":"Concierge", "text":"Your results are in. LDL is borderline high, CRP mildly raised."},
        {"timestamp": datetime(2025,8,20,14,30), "date": date(2025,8,20), "time":"14:30", "sender":"Dr. Warren", "role":"Physician", "text":"Start Mediterranean-style diet and 30–40 mins brisk walking 5 days/week."},
        {"timestamp": datetime(2025,9,1,22,45), "date": date(2025,9,1), "time":"22:45", "sender":"Advik", "role":"Performance Scientist", "text":"Garmin sleep last night 23:45-06:30 (TST 6h 45m)."},
        {"timestamp": datetime(2025,9,2,6,10), "date": date(2025,9,2), "time":"06:10", "sender":"Rohan", "role":"Member", "text":"Heading to airport, boarding SQ322 to London."},
        {"timestamp": datetime(2025,9,2,13,45), "date": date(2025,9,2), "time":"13:45", "sender":"Rohan", "role":"Member", "text":"Landed LHR, long immigration lines."},
        {"timestamp": datetime(2025,9,3,8,10), "date": date(2025,9,3), "time":"08:10", "sender":"Rohan", "role":"Member", "text":"40 min treadmill + 20 min strength. Good sweat."},
        {"timestamp": datetime(2025,9,17,9,12), "date": date(2025,9,17), "time":"09:12", "sender":"Ruby", "role":"Concierge", "text":"LDL dropped from 140 to 132 mg/dL. hs-CRP down from 2.9 to 2.4 mg/L."},
        {"timestamp": datetime(2025,10,14,9,42), "date": date(2025,10,14), "time":"09:42", "sender":"Dr. Warren", "role":"Physician", "text":"Total Cholesterol 197, LDL 126, HDL 44, Triglycerides 148, hs-CRP 1.4, BP 126/82."},
        {"timestamp": datetime(2025,10,16,7,50), "date": date(2025,10,16), "time":"07:50", "sender":"Rachel", "role":"Physiotherapist", "text":"Adding 1 weekly HIIT session to improve HDL and heart efficiency."},
        {"timestamp": datetime(2025,10,28,22,15), "date": date(2025,10,28), "time":"22:15", "sender":"Rohan", "role":"Member", "text":"On the way to airport for Seoul – gate A12, boarding soon."},
        {"timestamp": datetime(2025,12,10,10,30), "date": date(2025,12,10), "time":"10:30", "sender":"Dr. Warren", "role":"Physician", "text":"LDL down to 118, CRP at 1.2, Thyroid panel normal, ECG clear, CIMT healthy."},
        {"timestamp": datetime(2026,1,11,11,2), "date": date(2026,1,11), "time":"11:02", "sender":"Dr. Warren", "role":"Physician", "text":"LDL 92, HDL 56, Triglycerides 102, ApoB 78, Lp(a) optimal. OGTT normal."},
        {"timestamp": datetime(2026,1,12,7,35), "date": date(2026,1,12), "time":"07:35", "sender":"Advik", "role":"Performance Scientist", "text":"Sleep 00:15-06:45 (~6h30m). Jet lag ok."},
        {"timestamp": datetime(2026,1,25,5,55), "date": date(2026,1,25), "time":"05:55", "sender":"Rohan", "role":"Member", "text":"Departed SIN, in-flight to Jakarta. See you on Monday."},
        {"timestamp": datetime(2026,2,2,19,5), "date": date(2026,2,2), "time":"19:05", "sender":"Rohan", "role":"Member", "text":"Arrived in New York; hotel check-in done."},
        {"timestamp": datetime(2026,2,3,9,15), "date": date(2026,2,3), "time":"09:15", "sender":"Rohan", "role":"Member", "text":"HIIT 22 min + walk 35 min."},
    ]
    df = pd.DataFrame(data)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df

# Sidebar & Data Loading

# ----------------
# Sidebar: require an uploaded file (no mock/local fallback)
# ----------------
st.sidebar.title("Elyx – Member Journey")
with st.sidebar.expander("Data source", expanded=True):
    uploaded = st.file_uploader(
        "Upload chat transcript (.csv or .docx)",
        type=["csv", "docx"],
        help="Upload CSV or .docx transcript (must include timestamp/date and text).",
        accept_multiple_files=False
    )

messages_df = None

# Require upload — show friendly message and stop if nothing is uploaded.
if uploaded is None:
    st.sidebar.info("Upload required — please upload a CSV or DOCX transcript to continue.")
    st.info("Upload a CSV with columns like timestamp,sender,role,text OR date+time+sender+text. The app will pause until a file is uploaded.")
    st.stop()
else:
    try:
        content = uploaded.read()
        if uploaded.name.lower().endswith('.csv'):
            messages_df = parse_csv_messages(content)
        else:
            messages_df = parse_docx_messages(content)
        st.sidebar.success(f"Loaded {uploaded.name}")
    except Exception as e:
        st.sidebar.error(f"Could not parse uploaded file: {e}")
        # stop so the rest of UI doesn't try to run on missing/invalid data
        st.stop()

messages_df = messages_df.copy()
if 'timestamp' in messages_df.columns:
    messages_df['timestamp'] = pd.to_datetime(messages_df['timestamp'], errors='coerce')
else:
    messages_df['timestamp'] = pd.NaT
messages_df['date'] = messages_df['timestamp'].dt.date
messages_df['time'] = messages_df['timestamp'].dt.strftime('%H:%M').fillna('')

events_df = extract_events(messages_df)
lab_df = extract_labs(messages_df)
sleep_df = extract_sleep_metrics(messages_df)
activity_df = extract_activity_minutes(messages_df)
biomarkers_df = merge_biomarkers(lab_df, sleep_df, activity_df)

# Header KPIs
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.markdown("**Messages**")
    st.markdown(f"<div class='kpi'><div style='font-size:1.4rem'>{len(messages_df)}</div><div class='small'>total</div></div>", unsafe_allow_html=True)
with col2:
    if messages_df['timestamp'].notna().any():
        days = (messages_df['timestamp'].max() - messages_df['timestamp'].min()).days + 1
    else:
        days = 0
    st.markdown("**Journey Length**")
    st.markdown(f"<div class='kpi'><div style='font-size:1.4rem'>{days} days</div><div class='small'>covered</div></div>", unsafe_allow_html=True)
with col3:
    st.markdown("**Decisions**")
    st.markdown(f"<div class='kpi'><div style='font-size:1.4rem'>{len(extract_decisions(messages_df))}</div><div class='small'>identified</div></div>", unsafe_allow_html=True)
with col4:
    st.markdown("**Lab Readings**")
    st.markdown(f"<div class='kpi'><div style='font-size:1.4rem'>{lab_df['marker'].nunique() if not lab_df.empty else 0}</div><div class='small'>unique markers</div></div>", unsafe_allow_html=True)

st.markdown("---")

# Pages
nav_options = [   # NEW — appears before Persona
    "Persona",
    "Journey Timeline",
    "Day Snapshot",
    "Advanced Decisions Explorer",
    "Decisions & Reasons",
    "Biomarkers",
    "Internal Metrics",
    "Conversation"
]
page = st.sidebar.radio("Go to", nav_options)


# Persona
if page == 'Persona':
    st.header('Member Persona')
    persona = {
        'name': st.text_input('Name', 'Rohan Patel'),
        'dob': st.date_input('Date of birth', date(1979,3,12)),
        'location': st.text_input('Location', 'Singapore'),
        'occupation': st.text_input('Occupation', 'Regional Head of Sales'),
        'goals': st.text_area('Goals (one per line)', 'Reduce heart disease risk by Dec 2026\nImprove cognitive focus by Jun 2026')
    }
    st.info('Persona stored in-memory for demo. Replace with real data later.')

# Journey Timeline
elif page == 'Journey Timeline':
    st.header('Journey Timeline')
    if events_df.empty:
        st.info('No events detected yet. Upload a CSV or docx transcript.')
    else:
        df = events_df.copy()
        df['when'] = pd.to_datetime(df['timestamp'])
        df['y'] = df['type']
        fig = px.scatter(df, x='when', y='y', color='type', hover_data=['title','detail','sender','role'])
        fig.update_yaxes(title='Event Type')
        fig.update_xaxes(title='Time')
        st.plotly_chart(fig, use_container_width=True)
        st.markdown('#### Events Table')
        st.dataframe(df[['date','type','title','detail','sender','role']])

# Day Snapshot
elif page == 'Day Snapshot':
    st.header('Day Snapshot')
    if messages_df.empty:
        st.info('No messages. Upload transcript.')
    else:
        dmin = messages_df['date'].min()
        dmax = messages_df['date'].max()
        sel = st.date_input('Select a date', value=dmax if pd.notna(dmax) else date.today(), min_value=dmin, max_value=dmax)
        day_msgs = messages_df[messages_df['date'] == sel]
        st.subheader('Messages on selected day')
        if day_msgs.empty:
            st.write('No messages on this date.')
        else:
            st.dataframe(day_msgs[['time','sender','role','text']])
        st.subheader('Biomarkers near this date (±7 days)')
        if biomarkers_df.empty:
            st.write('No biomarker readings parsed yet.')
        else:
            near = biomarkers_df[(biomarkers_df['date'] >= sel - timedelta(days=7)) & (biomarkers_df['date'] <= sel + timedelta(days=7))]
            if near.empty:
                st.write('No biomarkers within the window.')
            else:
                piv = near.pivot_table(index='date', columns='marker', values='value', aggfunc='last').sort_index()
                st.dataframe(piv)
        st.subheader('Sleep & Activity')
        srow = None
        if not sleep_df.empty:
            s_candidates = sleep_df[sleep_df['date'] <= sel]
            if not s_candidates.empty:
                srow = s_candidates.iloc[-1]
        if srow is not None:
            st.markdown(f"**Sleep**: {srow['sleep_hours']} h" + (f" (bed {srow['bedtime']} → wake {srow['waketime']})" if srow['bedtime'] or srow['waketime'] else ""))
        else:
            st.markdown("**Sleep**: no log")
        if not activity_df.empty and sel in set(activity_df['date']):
            mins = int(activity_df[activity_df['date'] == sel]['activity_minutes'].iloc[0])
            st.markdown(f"**Exercise**: {mins} min")
        else:
            st.markdown("**Exercise**: no log")
if page == 'Advanced Decisions Explorer':
    st.header('Advanced Decisions Explorer (external)')
    st.markdown("Click the link below to open the Decisions Explorer tool in a new tab:")
    st.markdown(
        '<a href="https://mqtnsn.csb.app/" target="_blank" rel="noopener noreferrer" style="font-size:1.05rem">🔗 Open Decisions Explorer (new tab)</a>',
        unsafe_allow_html=True
    )
    st.info("This opens an external site. Use your browser back button or the app's sidebar to return.")
    # Stop here so the rest of the page rendering does not run for this selection
    st.stop()

# Decisions & Reasons
elif page == 'Decisions & Reasons':
    st.header('Decisions & Reasons')
    decisions_df = extract_decisions(messages_df)
    if decisions_df.empty:
        st.info('No decisions detected (look for words like "start", "prescribe", "schedule").')
    else:
        sel_idx = st.selectbox('Select a decision', options=list(decisions_df.index), format_func=lambda i: f"{decisions_df.loc[i,'date']} – {decisions_df.loc[i,'by']} ({decisions_df.loc[i,'role']}): {decisions_df.loc[i,'decision_text'][:80]}…")
        row = decisions_df.loc[sel_idx]
        # Replace the old single-line display:
        # st.write(row['decision_text'])

        # With this (renders literal "\n" as new lines)
        decision_text = str(row.get('decision_text', ''))
        # convert literal backslash-n sequences into actual line breaks
        decision_text = decision_text.replace('\\n', '\n')
        st.subheader('Decision Text')
        st.text(decision_text)   # or st.write(decision_text) / st.markdown(decision_text)
        st.subheader('Rationale snippets (auto-extracted)')
        if row['rationale_snippets']:
            for s in row['rationale_snippets']:
                clean = str(s).replace('\\n', '\n')               # convert literal \n to newline
                # inside a markdown list item, indent subsequent lines so they stay in the same bullet
                bullet_safe = clean.replace('\n', '\n  ')
                st.markdown(f"- {bullet_safe}")

        else:
            st.write('No explicit rationale snippets; showing neighborhood messages:')
            t0, t1 = row['timestamp'] - timedelta(days=3), row['timestamp']
            neigh = messages_df[(messages_df['timestamp'] >= t0) & (messages_df['timestamp'] <= t1)]
            st.dataframe(neigh[['date','time','sender','role','text']])

# Biomarkers
elif page == 'Biomarkers':
    st.header('Biomarker Trends (incl. Sleep & Exercise)')
    if biomarkers_df.empty:
        st.info('No biomarker readings parsed yet. Ensure your transcript contains lab numbers, sleep logs (e.g. "23:45-06:30" or "TST 6h 30m"), and exercise durations (e.g. "run 45 min").')
    else:
        markers = sorted(biomarkers_df['marker'].unique())
        default_pick = [m for m in ['LDL','ApoB','hs-CRP','Sleep (hrs)','Exercise (min)'] if m in markers]
        pick = st.multiselect('Choose markers to plot', options=markers, default=default_pick or markers[:3])
        if pick:
            sub = biomarkers_df[biomarkers_df['marker'].isin(pick)]
            fig = px.line(sub, x='timestamp', y='value', color='marker', markers=True)
            st.plotly_chart(fig, use_container_width=True)
        st.markdown('#### Raw biomarker table')
        st.dataframe(biomarkers_df.sort_values('timestamp'))
        # Removed extra sleep timing table (bed → wake)


# Internal Metrics
elif page == 'Internal Metrics':
    st.header('Internal Metrics (heuristic)')
    st.caption('Estimated internal hours spent by role (members and unnamed roles are excluded automatically).')

    # Use sensible default per-interaction minutes (no sliders)
    weights = {
        'Physician': 12,
        'Nutritionist': 8,
        'Physiotherapist': 8,
        'Concierge': 6,
        'Concierge Lead': 10,
        'Performance Scientist': 8,
        'Lab': 5,
        'Member': 0
    }

    met = compute_internal_metrics(messages_df, weights)

    # Exclude Member and any blank/unknown role names
    if not met.empty:
        met = met[~met['role'].astype(str).str.strip().eq('')]
        met = met[~met['role'].str.lower().isin(['member','unknown'])]
        met = met[met['interactions'] > 0]

    if met.empty:
        st.info('No staff interactions to compute metrics from (members and unnamed roles have been excluded).')
    else:
        # Ensure consistent column types
        met = met.copy()
        met['est_hours'] = met['est_hours'].astype(float)
        total_hours = met['est_hours'].sum()
        met['pct_share'] = ((met['est_hours'] / total_hours) * 100).round(1) if total_hours > 0 else 0

        st.markdown('### Internal Hours by Role')
        display_df = met[['role','interactions','est_hours','pct_share']].rename(columns={'est_hours':'est_hours (h)', 'pct_share':'pct_share (%)'})
        st.dataframe(display_df.style.format({'est_hours (h)':'{:.2f}','pct_share (%)':'{:.1f}%'}))

        # Bar chart
        fig = px.bar(met, x='role', y='est_hours', title='Estimated hours by role', text='est_hours')
        fig.update_layout(yaxis_title='Hours', xaxis_title='Role')
        fig.update_traces(texttemplate='%{text:.2f}', textposition='outside')
        st.plotly_chart(fig, use_container_width=True)

        # Pie chart (visual share)
        fig2 = px.pie(met, names='role', values='est_hours', title='Share of estimated hours')
        st.plotly_chart(fig2, use_container_width=True)

        # CSV download
        st.download_button('Download internal metrics CSV', data=met.to_csv(index=False).encode('utf-8'), file_name='internal_metrics.csv', mime='text/csv')

# Conversation
elif page == 'Conversation':
    st.header('Conversation Viewer')
    if messages_df.empty:
        st.info('No messages.')
    else:
        roles = ['All'] + sorted(messages_df['role'].unique().tolist())
        role_sel = st.selectbox('Filter role', roles)
        q = st.text_input('Search text')
        view = messages_df.copy()
        if role_sel != 'All':
            view = view[view['role'] == role_sel]
        if q:
            view = view[view['text'].str.contains(q, case=False, na=False)]
        st.dataframe(view[['date','time','sender','role','text']])
        st.download_button('Download messages CSV', data=view.to_csv(index=False).encode('utf-8'), file_name='messages_filtered.csv', mime='text/csv')

# Footer
st.markdown('---')
st.markdown('**Notes:**\n- Upload a CSV with columns like timestamp,sender,role,text OR date+time+sender+text.\n- The app auto-extracts travel/test/intervention events, lab numbers, **sleep timing (Garmin/Advik logs)** and **exercise minutes** using heuristics.')
