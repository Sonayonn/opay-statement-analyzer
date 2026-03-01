import streamlit as st
import pandas as pd
import re
import pdfplumber
from thefuzz import process, fuzz

# ==========================================
# PAGE CONFIGURATION & CUSTOM CSS
# ==========================================
st.set_page_config(page_title="OPay Statement Analyzer", layout="wide", page_icon="🟢")

# Injecting Custom CSS
st.markdown("""

<style>
    /* Style the top metric cards */
    div[data-testid="metric-container"] {
        background-color: #f7f9fc;
        border: 1px solid #e2e8f0;
        padding: 5% 10% 5% 10%;
        border-radius: 10px;
        box-shadow: 2px 2px 10px rgba(0,0,0,0.05);
    }
    /* Change the color of the positive/negative Net Flow */
    div[data-testid="stMetricDelta"] > div {
        font-size: 1.2rem !important;
    }
    /* Main Title Styling */
    h1 {
        color: #00b578; /* OPay Green */
        font-weight: 800;
    }
</style>
""", unsafe_allow_html=True)

st.title("🟢 OPay Statement Analyzer")
st.markdown("Upload your OPay Excel statement to instantly see your cash flow and transaction notes.")
st.caption("🔒 **PRIVACY FIRST:** Your file is processed securely in temporary server memory. It is never saved, stored, or viewed by anyone, and is permanently deleted the moment you close this page.")
st.divider() 

# ==========================================
# INTAKE MANIFOLD
# ==========================================
def extract_from_pdf(file_obj):
    records = []
    with pdfplumber.open(file_obj) as pdf:
        full_text = " ".join([str(page.extract_text()).replace('\n', ' ') for page in pdf.pages])
        
    chunks = re.split(r'(?=\b\d{2} [A-Z][a-z]{2} \d{4}\b)', full_text)
    
    for chunk in chunks:
        if not chunk.strip() or re.search(r'(?i)(date/time|balance after|owealth\b)', chunk): continue
        amounts = re.findall(r'\b\d+(?:[.,]\d{3})*[.,]\d{2}\b', chunk)
        if not amounts: continue
        
        is_outflow = True
        if re.search(r'(?i)(transfer from)', chunk): is_outflow = False
            
        raw_amount = amounts[0]
        actual_amount = float(re.sub(r'[^\d]', '', raw_amount)) / 100.0
        
        if is_outflow: records.append({'Description': chunk, 'Amount_Out': actual_amount, 'Amount_In': 0.0})
        else: records.append({'Description': chunk, 'Amount_Out': 0.0, 'Amount_In': actual_amount})
            
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
    
    m = re.search(r'(?:Transfer to|Transfer from|POS Transfer-)\s*(.*?)\s*\|\s*(.*?)\s*(?:\|(.*))?', text, re.IGNORECASE)
    
    if m:
        name = m.group(1).strip().title()
        if m.group(3) and m.group(3).strip():
            narration = m.group(3).strip().title()
    else:
        if 'Sporty' in text or 'Betting' in text: return pd.Series(["Betting (SportyBet)", "Gaming/Betting"])
        elif 'Airtime' in text: return pd.Series(["Airtime Purchase", "Airtime"])
        elif 'Data' in text: return pd.Series(["Mobile Data", "Internet Data"])
        elif 'Stamp Duty' in text: return pd.Series(["FGN Stamp Duty", "Bank Charges"])
        elif 'Google Play' in text: return pd.Series(["Google Play", "App Subscription"])

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
uploaded_file = st.file_uploader("Drop your OPay Statement here (XLSX, CSV)", type=['xlsx', 'csv'])

if uploaded_file is not None:
    with st.spinner('Parsing OPay Telemetry...'):
        
        if uploaded_file.name.lower().endswith('.pdf'):
            df = extract_from_pdf(uploaded_file)
        else:
            df = extract_from_excel(uploaded_file, uploaded_file.name)
            
        if not df.empty:
            df[['Raw_Name', 'Narration']] = df['Description'].apply(extract_opay_details)
            df['Clean_Name'] = resolve_identities(df['Raw_Name'])
            
            # Aggregate the Math
            summary_out = df[df['Amount_Out'] > 0].groupby('Clean_Name')['Amount_Out'].sum().reset_index().sort_values(by='Amount_Out', ascending=False)
            summary_in = df[df['Amount_In'] > 0].groupby('Clean_Name')['Amount_In'].sum().reset_index().sort_values(by='Amount_In', ascending=False)
            summary_narration = df.groupby('Narration')[['Amount_Out', 'Amount_In']].sum().reset_index()
            summary_narration = summary_narration[(summary_narration['Amount_Out'] > 0) | (summary_narration['Amount_In'] > 0)].sort_values(by='Amount_Out', ascending=False)

            
            # Calculate Top-Level Variables for the KPI Dashboard
            total_money_in = summary_in['Amount_In'].sum()
            total_money_out = summary_out['Amount_Out'].sum()
            net_flow = total_money_in - total_money_out
            
            if net_flow < 0:
                delta_str = f"-₦{abs(net_flow):,.2f}"
            else:
                delta_str = f"₦{net_flow:,.2f}"

            st.success("✅ OPay Analysis Complete!")
            
            # --- KPI DASHBOARD UI ---
            st.markdown("### 📊 Account Summary")
            kpi1, kpi2, kpi3 = st.columns(3)
            kpi1.metric("💰 Total Money IN", f"₦{total_money_in:,.2f}")
            kpi2.metric("💸 Total Money OUT", f"₦{total_money_out:,.2f}")
            kpi3.metric("⚖️ Net Cash Flow", f"₦{net_flow:,.2f}", delta=delta_str)
            st.divider() # Separates KPIs from the tables
            
            # THE TOTAL ROWS
            total_out_df = pd.DataFrame([{'Clean_Name': '🛑 TOTAL', 'Amount_Out': total_money_out}])
            summary_out = pd.concat([summary_out, total_out_df], ignore_index=True)

            total_in_df = pd.DataFrame([{'Clean_Name': '🛑 TOTAL', 'Amount_In': total_money_in}])
            summary_in = pd.concat([summary_in, total_in_df], ignore_index=True)

            total_narration_df = pd.DataFrame([{'Narration': '🛑 TOTAL', 'Amount_Out': summary_narration['Amount_Out'].sum(), 'Amount_In': summary_narration['Amount_In'].sum()}])
            summary_narration = pd.concat([summary_narration, total_narration_df], ignore_index=True)

            # --- RENDER FORMATTED TABLES ---
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.subheader("💸 Money OUT")
                # Format specific column as Naira
                st.dataframe(summary_out.style.format({'Amount_Out': '₦{:,.2f}'}), hide_index=True, use_container_width=True)
                
            with col2:
                st.subheader("💰 Money IN")
                st.dataframe(summary_in.style.format({'Amount_In': '₦{:,.2f}'}), hide_index=True, use_container_width=True)
                
            with col3:
                st.subheader("📝 Spending by Narration")
                st.dataframe(summary_narration.style.format({'Amount_Out': '₦{:,.2f}', 'Amount_In': '₦{:,.2f}'}), hide_index=True, use_container_width=True)
                
        else:
            st.error("Engine Stalled: Could not find valid OPay transactions.")
