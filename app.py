import streamlit as st
import pandas as pd
import re
import pdfplumber
from thefuzz import process, fuzz
import plotly.express as px
import io

# ==========================================
# 1. PAGE CONFIGURATION & DYNAMIC CSS
# ==========================================
st.set_page_config(page_title="OPay Analyzer", layout="wide", page_icon="🟢")

st.markdown("""
<style>
    /* Use Streamlit's dynamic theme variables instead of hardcoded hex colors */
    .stApp { background-color: var(--secondary-background-color); }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    
    .main-title { 
        color: var(--text-color); 
        font-weight: 900; 
        font-size: 2.5rem; 
        letter-spacing: -1px; 
        margin-bottom: 0px; 
        padding-top: 1rem; 
    }
    .opay-green { color: #00b578; }
    
    .fintech-card { 
        background-color: var(--background-color); 
        padding: 24px; 
        border-radius: 16px; 
        box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); 
        border: 1px solid var(--secondary-background-color); 
        transition: transform 0.2s, box-shadow 0.2s; 
        margin-bottom: 1rem; 
    }
    .fintech-card:hover { 
        transform: translateY(-4px); 
        box-shadow: 0 12px 20px -3px rgba(0,0,0,0.15); 
    }
    
    .card-title { 
        color: #64748b; 
        font-size: 0.95rem; 
        font-weight: 700; 
        text-transform: uppercase; 
        letter-spacing: 0.5px; 
        margin-bottom: 8px; 
    }
    .card-value { 
        color: var(--text-color); 
        font-size: 2.2rem; 
        font-weight: 800; 
    }
    
    /* Using rgba backgrounds so the delta chips look good in both Dark and Light mode */
    .delta-positive { color: #10b981; font-weight: 600; font-size: 0.95rem; margin-top: 8px; background: rgba(16, 185, 129, 0.1); padding: 4px 8px; border-radius: 6px; display: inline-block;}
    .delta-negative { color: #ef4444; font-weight: 600; font-size: 0.95rem; margin-top: 8px; background: rgba(239, 68, 68, 0.1); padding: 4px 8px; border-radius: 6px; display: inline-block;}
    
    .table-header { 
        color: var(--text-color); 
        font-weight: 800; 
        font-size: 1.2rem; 
        padding-top: 1.5rem; 
        padding-bottom: 0.5rem; 
    }
</style>
""", unsafe_allow_html=True)

st.markdown("<div class='main-title'><span class='opay-green'>OPay</span> Statement Analyzer</div>", unsafe_allow_html=True)
st.markdown("<p style='color: #64748b; font-size: 1.1rem;'>Instantly visualize your cash flow, transaction frequencies, and spending habits.</p>", unsafe_allow_html=True)
st.write("")
# ==========================================
# OPAY INTAKE MANIFOLD
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
        
        # Extract Date
        date_match = re.search(r'\b\d{2} [A-Z][a-z]{2} \d{4}\b', chunk)
        trans_date = pd.to_datetime(date_match.group(0)).date() if date_match else None
        
        is_outflow = not bool(re.search(r'(?i)(transfer from)', chunk))
        actual_amount = float(re.sub(r'[^\d]', '', amounts[0])) / 100.0
        
        if is_outflow: records.append({'Date': trans_date, 'Description': chunk, 'Amount_Out': actual_amount, 'Amount_In': 0.0})
        else: records.append({'Date': trans_date, 'Description': chunk, 'Amount_Out': 0.0, 'Amount_In': actual_amount})
    return pd.DataFrame(records)

def extract_from_excel(file_obj, filename):
    df_raw = pd.read_csv(file_obj, header=None) if filename.endswith('.csv') else pd.read_excel(file_obj, header=None)
    header_idx = next((i for i, row in df_raw.iterrows() if re.search(r'(?i)(desc|narration|remark|particular)', " ".join([str(c).lower() for c in row.values]))), 0)
    file_obj.seek(0)
    df = pd.read_csv(file_obj, header=header_idx) if filename.endswith('.csv') else pd.read_excel(file_obj, header=header_idx)
    
    col_desc = next((c for c in df.columns if 'desc' in str(c).lower()), None)
    col_out = next((c for c in df.columns if 'debit' in str(c).lower()), None)
    col_in = next((c for c in df.columns if 'credit' in str(c).lower()), None)
    col_date = next((c for c in df.columns if 'date' in str(c).lower() or 'time' in str(c).lower()), None)
    
    if not col_desc: return pd.DataFrame()
    df = df.dropna(subset=[col_desc])
    df = df[~df[col_desc].str.contains('OWealth|Balance After', na=False, case=False)]
    
    df['Date'] = pd.to_datetime(df[col_date], errors='coerce').dt.date if col_date else None
    
    def clean_money(x):
        clean_str = re.sub(r'[^\d.]', '', str(x))
        return float(clean_str) if clean_str else 0.0
        
    df['Amount_Out'] = df[col_out].apply(clean_money) if col_out else 0.0
    df['Amount_In'] = df[col_in].apply(clean_money) if col_in else 0.0
    df['Description'] = df[col_desc]
    return df

# ==========================================
# OPAY Y-PIPE 
# ==========================================
def extract_opay_details(text):
    text = str(text).replace('\n', ' ').strip()
    name, narration = "Other", "General"

    if re.search(r'(Sporty|Betting)', text, re.IGNORECASE): return pd.Series(["Betting (SportyBet)", "Gaming/Betting"])
    elif re.search(r'(Airtime)', text, re.IGNORECASE): return pd.Series(["Airtime Purchase", "Airtime"])
    elif re.search(r'(Mobile Data|DataMin)', text, re.IGNORECASE): return pd.Series(["Mobile Data", "Internet Data"])
    elif re.search(r'(Stamp Duty|Stamp_Duty)', text, re.IGNORECASE): return pd.Series(["FGN Stamp Duty", "Bank Charges"])
    elif re.search(r'(Google Play)', text, re.IGNORECASE): return pd.Series(["Google Play", "App Subscription"])
    
    m_pipe = re.search(r'(?:Transfer to|Transfer from|POS Transfer-)\s*(.*?)\s*\|\s*(.*?)(?:\s*\|\s*(.*))?$', text, re.IGNORECASE)
    bank_regex = r'\s+(OPay|PalmPay|MONIE|Moniepoint|United|Wema|Access|Zenith|FBN|First Bank|UBA|Kuda|GTB|Guaranty|Sterling|Stanbic|Polaris|Union|Fidelity|Ecobank|Paystack)'
    m_no_pipe = re.search(r'(?:Transfer to|Transfer from|POS Transfer-)\s*(.*?)' + bank_regex + r'(.*)$', text, re.IGNORECASE)
    
    if m_pipe:
        name = m_pipe.group(1).strip().title()
        bank_name = m_pipe.group(2).strip().title()
        note = m_pipe.group(3).strip().title() if m_pipe.group(3) else ""
        narration = note if note else ("General" if re.search(r'(?i)(opay|palmpay|monie|uba|fbn|zenith|access|gtb|kuda)', bank_name) else bank_name)
    elif m_no_pipe:
        name = m_no_pipe.group(1).strip().title()
        raw_note = m_no_pipe.group(3)
        narration = raw_note.strip().title() if raw_note and raw_note.strip() else "General"
    else:
        m_bare = re.search(r'(?:Transfer to|Transfer from|POS Transfer-)\s*(.*)$', text, re.IGNORECASE)
        raw_name = m_bare.group(1) if m_bare else text
        clean_name = re.sub(r'(?i)\b\d{10,30}\b|\b\d{2} [A-Za-z]{3} \d{4}\b|\b\d{2}:\d{2}:\d{2}\b|\b\d{2}/\d{2}/\d{2,4}\b|\b\d+(?:[.,]\d{3})*[.,]\d{2}\b|--|\b(transfer|pos|successful|failed|mobile)\b', ' ', raw_name)
        clean_name = re.sub(r'[^A-Za-z\s]+', ' ', clean_name)
        clean_name = " ".join(clean_name.split()).title()
        if len(clean_name) > 40: clean_name = clean_name[:40].strip()
        if clean_name: name = clean_name
        narration = "General"

    name = re.sub(r'(?i)^POS Transfer-', '', name).strip().rstrip('|').strip()
    if not name: name = "Other"
    
    narration = str(narration)
    narration = re.sub(r'\b\d{5,30}\b', '', narration) 
    if re.fullmatch(r'[^A-Za-z]*', narration): 
        narration = "General"
    narration = narration.strip(" |,-:") 
    if not narration: narration = "General"
    # --------------------------------
    
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
# EXPORT COMPILER
# ==========================================
@st.cache_data
def convert_to_excel(df_all, df_out, df_in, df_narration):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_all[['Date', 'Clean_Name', 'Narration', 'Amount_In', 'Amount_Out']].to_excel(writer, index=False, sheet_name='All Transactions')
        df_out.to_excel(writer, index=False, sheet_name='Money Out Summary')
        df_in.to_excel(writer, index=False, sheet_name='Money In Summary')
        df_narration.to_excel(writer, index=False, sheet_name='Narration Summary')
    return output.getvalue()

# ==========================================
# WEB DASHBOARD
# ==========================================
st.info("🔒 **Bank-Grade Privacy:** Processed dynamically in temporary memory. No data is ever saved or stored. It vanishes the moment you close the tab.", icon="🛡️")
uploaded_file = st.file_uploader("Upload Statement (XLSX, CSV)", type=['xlsx', 'csv'])

if uploaded_file is not None:
    with st.spinner('Spinning up Analytics Engine...'):
        
        if uploaded_file.name.lower().endswith('.pdf'): df = extract_from_pdf(uploaded_file)
        else: df = extract_from_excel(uploaded_file, uploaded_file.name)
            
        if not df.empty:
            df[['Raw_Name', 'Narration']] = df['Description'].apply(extract_opay_details)
            df['Clean_Name'] = resolve_identities(df['Raw_Name'])
            
            # Mathematical Aggregations
            summary_out = df[df['Amount_Out'] > 0].groupby('Clean_Name').agg(Amount_Out=('Amount_Out', 'sum'), Transactions=('Amount_Out', 'count')).reset_index().sort_values(by='Amount_Out', ascending=False)
            summary_in = df[df['Amount_In'] > 0].groupby('Clean_Name').agg(Amount_In=('Amount_In', 'sum'), Transactions=('Amount_In', 'count')).reset_index().sort_values(by='Amount_In', ascending=False)
            summary_narration = df.groupby('Narration').agg(Amount_Out=('Amount_Out', 'sum'), Amount_In=('Amount_In', 'sum'), Transactions=('Narration', 'count')).reset_index()
            summary_narration = summary_narration[(summary_narration['Amount_Out'] > 0) | (summary_narration['Amount_In'] > 0)].sort_values(by='Amount_Out', ascending=False)

            total_money_in = summary_in['Amount_In'].sum()
            total_money_out = summary_out['Amount_Out'].sum()
            net_flow = total_money_in - total_money_out
            
            delta_html = f"<div class='delta-positive'>↑ ₦{net_flow:,.2f} surplus</div>" if net_flow >= 0 else f"<div class='delta-negative'>↓ -₦{abs(net_flow):,.2f} deficit</div>"

            # --- KPI DASHBOARD ---
            col1, col2, col3 = st.columns(3)
            with col1:
                st.markdown(f'<div class="fintech-card"><div class="card-title">💰 Money IN</div><div class="card-value">₦{total_money_in:,.2f}</div></div>', unsafe_allow_html=True)
            with col2:
                st.markdown(f'<div class="fintech-card"><div class="card-title">💸 Money OUT</div><div class="card-value">₦{total_money_out:,.2f}</div></div>', unsafe_allow_html=True)
            with col3:
                st.markdown(f'<div class="fintech-card"><div class="card-title">⚖️ Net Flow</div><div class="card-value">₦{net_flow:,.2f}</div>{delta_html}</div>', unsafe_allow_html=True)
            
           # --- VISUAL ANALYTICS (CHARTS) ---
            st.write("")
            st.markdown("<div class='table-header'>📈 Cash Flow Over Time</div>", unsafe_allow_html=True)
            
            #Line Chart
            if 'Date' in df.columns and not df['Date'].isna().all():
                df_time = df.groupby('Date')[['Amount_In', 'Amount_Out']].sum().reset_index()
                fig_line = px.line(df_time, x='Date', y=['Amount_In', 'Amount_Out'], 
                                   color_discrete_map={'Amount_In': '#00b578', 'Amount_Out': '#ef4444'},
                                   labels={'value': 'Naira (₦)', 'variable': 'Flow Type'})
                fig_line.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', legend_title_text='')
                st.plotly_chart(fig_line, use_container_width=True)
            else:
                st.info("Time-Series inactive: Could not extract valid dates from this specific file format.")

            st.write("")
            
            #Vibrant Donut Charts
            donut_col1, donut_col2 = st.columns(2)
            
            with donut_col1:
                st.markdown("<div class='table-header'>🍩 Top Money OUT Recipients</div>", unsafe_allow_html=True)
                top_out = summary_out.head(7)
                fig_out = px.pie(top_out, values='Amount_Out', names='Clean_Name', hole=0.5,
                                 color_discrete_sequence=px.colors.qualitative.Vivid)
               
                fig_out.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', 
                                      legend=dict(orientation="h", yanchor="top", y=-0.1, xanchor="center", x=0.5))
                fig_out.update_traces(textposition='inside', textinfo='percent', hovertemplate="<b>%{label}</b><br>₦%{value:,.2f}<extra></extra>")
                st.plotly_chart(fig_out, use_container_width=True)

            with donut_col2:
                st.markdown("<div class='table-header'>🍩 Top Money IN Sources</div>", unsafe_allow_html=True)
                top_in = summary_in.head(7) 
                fig_in = px.pie(top_in, values='Amount_In', names='Clean_Name', hole=0.5,
                                color_discrete_sequence=px.colors.qualitative.Bold)
                fig_in.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)', 
                                     legend=dict(orientation="h", yanchor="top", y=-0.1, xanchor="center", x=0.5))
                fig_in.update_traces(textposition='inside', textinfo='percent', hovertemplate="<b>%{label}</b><br>₦%{value:,.2f}<extra></extra>")
                st.plotly_chart(fig_in, use_container_width=True)

            # --- TOTAL ROWS ---
            total_out_df = pd.DataFrame([{'Clean_Name': '🛑 TOTAL', 'Amount_Out': total_money_out, 'Transactions': summary_out['Transactions'].sum()}])
            summary_out = pd.concat([summary_out, total_out_df], ignore_index=True)
            total_in_df = pd.DataFrame([{'Clean_Name': '🛑 TOTAL', 'Amount_In': total_money_in, 'Transactions': summary_in['Transactions'].sum()}])
            summary_in = pd.concat([summary_in, total_in_df], ignore_index=True)
            total_narration_df = pd.DataFrame([{'Narration': '🛑 TOTAL', 'Amount_Out': summary_narration['Amount_Out'].sum(), 'Amount_In': summary_narration['Amount_In'].sum(), 'Transactions': summary_narration['Transactions'].sum()}])
            summary_narration = pd.concat([summary_narration, total_narration_df], ignore_index=True)

            # --- RENDER FORMATTED TABLES ---
            t_col1, t_col2, t_col3 = st.columns(3)
            with t_col1:
                st.markdown("<div class='table-header'>💸 Top Recipients</div>", unsafe_allow_html=True)
                st.dataframe(summary_out.style.format({'Amount_Out': '₦{:,.2f}', 'Transactions': '{:,.0f}'}), hide_index=True, use_container_width=True)
            with t_col2:
                st.markdown("<div class='table-header'>💰 Top Senders</div>", unsafe_allow_html=True)
                st.dataframe(summary_in.style.format({'Amount_In': '₦{:,.2f}', 'Transactions': '{:,.0f}'}), hide_index=True, use_container_width=True)
            with t_col3:
                st.markdown("<div class='table-header'>📝 Spending by Narration</div>", unsafe_allow_html=True)
                st.dataframe(summary_narration.style.format({'Amount_Out': '₦{:,.2f}', 'Amount_In': '₦{:,.2f}', 'Transactions': '{:,.0f}'}), hide_index=True, use_container_width=True)
            
            # --- EXPORT REPORT BUTTON ---
            st.divider()
            st.markdown("### 📥 Export Your Data")
            st.write("Download a compiled spreadsheet containing all your transactions summaries.")
            
            # Remove the "🛑 TOTAL" rows before exporting so the Excel math stays clean
            clean_out = summary_out[summary_out['Clean_Name'] != '🛑 TOTAL']
            clean_in = summary_in[summary_in['Clean_Name'] != '🛑 TOTAL']
            clean_narration = summary_narration[summary_narration['Narration'] != '🛑 TOTAL']
            
            excel_data = convert_to_excel(df, clean_out, clean_in, clean_narration)
            
            st.download_button(
                label="📊 Download Full Excel Report",
                data=excel_data,
                file_name="OPay_Financial_Report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary"
            )
                
        else:
            st.error("Engine Stalled: Could not find valid OPay transactions.")

