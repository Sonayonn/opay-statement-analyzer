import streamlit as st
import pandas as pd
import re
import pdfplumber
from thefuzz import process, fuzz

# 1. Page Configuration & UI
st.set_page_config(page_title="OPay Statement Analyzer", layout="wide")
st.title("🟢 OPay Statement Analyzer")
st.write("Upload your OPay PDF or Excel statement to instantly see your cash flow and transaction notes.")

# ==========================================
# THE OPAY-ONLY INTAKE MANIFOLD
# ==========================================
def extract_from_pdf(file_obj):
    records = []
    with pdfplumber.open(file_obj) as pdf:
        full_text = " ".join([str(page.extract_text()).replace('\n', ' ') for page in pdf.pages])
        
    # OPay Date Trigger (e.g., 28 Jan 2026)
    chunks = re.split(r'(?=\b\d{2} [A-Z][a-z]{2} \d{4}\b)', full_text)
    
    for chunk in chunks:
        if not chunk.strip() or re.search(r'(?i)(date/time|balance after|owealth\b)', chunk):
            continue
            
        amounts = re.findall(r'\b\d+(?:[.,]\d{3})*[.,]\d{2}\b', chunk)
        if not amounts: continue
        
        # OPay Direction Heuristics
        is_outflow = True
        if re.search(r'(?i)(transfer from)', chunk):
            is_outflow = False
            
        raw_amount = amounts[0]
        actual_amount = float(re.sub(r'[^\d]', '', raw_amount)) / 100.0
        
        if is_outflow:
            records.append({'Description': chunk, 'Amount_Out': actual_amount, 'Amount_In': 0.0})
        else:
            records.append({'Description': chunk, 'Amount_Out': 0.0, 'Amount_In': actual_amount})
            
    return pd.DataFrame(records)

def extract_from_excel(file_obj, filename):
    df_raw = pd.read_csv(file_obj, header=None) if filename.endswith('.csv') else pd.read_excel(file_obj, header=None)
    
    header_idx = 0
    for i, row in df_raw.iterrows():
        if re.search(r'(?i)(desc|narration|remark|particular)', " ".join([str(cell).lower() for cell in row.values])):
            header_idx = i
            break

    file_obj.seek(0)
    df = pd.read_csv(file_obj, header=header_idx) if filename.endswith('.csv') else pd.read_excel(file_obj, header=header_idx)

    # Fuzzy match standard OPay columns
    col_desc = next((c for c in df.columns if 'desc' in str(c).lower()), None)
    col_out = next((c for c in df.columns if 'debit' in str(c).lower()), None)
    col_in = next((c for c in df.columns if 'credit' in str(c).lower()), None)

    if not col_desc: return pd.DataFrame()

    df = df.dropna(subset=[col_desc])
    df = df[~df[col_desc].str.contains('OWealth|Balance After', na=False, case=False)]

    def clean_money(x):
        clean_str = re.sub(r'[^\d.]', '', str(x))
        try: return float(clean_str) if clean_str else 0.0
        except: return 0.0

    df['Amount_Out'] = df[col_out].apply(clean_money) if col_out else 0.0
    df['Amount_In'] = df[col_in].apply(clean_money) if col_in else 0.0
    df['Description'] = df[col_desc]
    return df

# ==========================================
# THE OPAY Y-PIPE (Name & Narration Splitter)
# ==========================================
def extract_opay_details(text):
    text = str(text).replace('\n', ' ')
    name = "Other"
    narration = "General"
    
    # OPay standard format: Action Name | Bank | User Narration
    # We use regex to split the string based on those pipe "|" characters
    m = re.search(r'(?:Transfer to|Transfer from|POS Transfer-)\s*(.*?)\s*\|\s*(.*?)\s*(?:\|(.*))?', text, re.IGNORECASE)
    
    if m:
        name = m.group(1).strip().title()
        # If there is a 3rd section after the pipes, it's the user's custom note
        if m.group(3) and m.group(3).strip():
            narration = m.group(3).strip().title()
    else:
        # Catch OPay internal transactions
        if 'Sporty' in text or 'Betting' in text:
            name = "Betting (SportyBet)"
            narration = "Gaming/Betting"
        elif 'Airtime' in text:
            name = "Airtime Purchase"
            narration = "Airtime"
        elif 'Data' in text:
            name = "Mobile Data"
            narration = "Internet Data"
        elif 'Stamp Duty' in text:
            name = "FGN Stamp Duty"
            narration = "Bank Charges"
        elif 'Google Play' in text:
            name = "Google Play"
            narration = "App Subscription"

    return pd.Series([name, narration])

def resolve_identities(names, threshold=85):
    name_counts = names.value_counts()
    master_names, mapping = [], {}
    for name in name_counts.index:
        if name == "Other":
            mapping[name] = "Other"
            continue
        if master_names:
            best_match, score = process.extractOne(name, master_names, scorer=fuzz.token_set_ratio)
            if score >= threshold:
                mapping[name] = best_match
                continue
        master_names.append(name)
        mapping[name] = name
    return names.map(mapping)

# ==========================================
# THE WEB DASHBOARD
# ==========================================
uploaded_file = st.file_uploader("Drop your OPay Statement here (PDF, XLSX, CSV)", type=['pdf', 'xlsx', 'csv'])

if uploaded_file is not None:
    with st.spinner('Parsing OPay Telemetry...'):
        
        if uploaded_file.name.lower().endswith('.pdf'):
            df = extract_from_pdf(uploaded_file)
        else:
            df = extract_from_excel(uploaded_file, uploaded_file.name)
            
        if not df.empty:
            # Run the Y-Pipe
            df[['Raw_Name', 'Narration']] = df['Description'].apply(extract_opay_details)
            df['Clean_Name'] = resolve_identities(df['Raw_Name'])
            
            # Aggregate the Math
            summary_out = df[df['Amount_Out'] > 0].groupby('Clean_Name')['Amount_Out'].sum().reset_index().sort_values(by='Amount_Out', ascending=False)
            summary_in = df[df['Amount_In'] > 0].groupby('Clean_Name')['Amount_In'].sum().reset_index().sort_values(by='Amount_In', ascending=False)
            
            # 3rd Column: Spending by Narration
            summary_narration = df.groupby('Narration')[['Amount_Out', 'Amount_In']].sum().reset_index()
            summary_narration = summary_narration[(summary_narration['Amount_Out'] > 0) | (summary_narration['Amount_In'] > 0)].sort_values(by='Amount_Out', ascending=False)

            st.success("✅ OPay Analysis Complete!")
            
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.subheader("💸 Top Recipients")
                st.dataframe(summary_out, hide_index=True, use_container_width=True)
                
            with col2:
                st.subheader("💰 Top Senders")
                st.dataframe(summary_in, hide_index=True, use_container_width=True)
                
            with col3:
                st.subheader("📝 Spending by Narration")
                st.dataframe(summary_narration, hide_index=True, use_container_width=True)
        else:
            st.error("Engine Stalled: Could not find valid OPay transactions.")