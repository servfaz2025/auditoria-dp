import streamlit as st
import pdfplumber
import re
from datetime import datetime, timedelta
from fpdf import FPDF
import io
from collections import defaultdict

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Auditor Enterprise Web 6.8", layout="wide")

# --- ESTILOS E EFEITO PISCANTE ---
if 'zoom' not in st.session_state: st.session_state.zoom = 1.0

st.markdown(f"""
    <style>
    @keyframes blinker {{ 50% {{ opacity: 0.1; }} }}
    .blink {{ animation: blinker 1s linear infinite; color: #EF4444; font-weight: bold; }}
    .card {{ 
        border: 2px solid #ddd; 
        padding: {int(15 * st.session_state.zoom)}px; 
        border-radius: 8px; 
        background-color: white; 
        margin-bottom: 10px;
        min-height: {int(120 * st.session_state.zoom)}px;
    }}
    .sidebar-text {{ color: #FFFFFF !important; font-size: 14px; }}
    [data-testid="stSidebar"] {{ background-color: #0F172A; }}
    .stDownloadButton button {{ width: 100%; background-color: #EF4444 !important; color: white !important; border: none !important; }}
    </style>
""", unsafe_allow_html=True)

# --- MOTOR DE CÁLCULO DIAMOND 6.8 ---
def analisar_linha(data_raw, batidas_raw, motivo_raw, escala_str):
    d_str = str(data_raw).strip()
    if not re.match(r"^\d{2}", d_str): return None
    bats = re.findall(r"\d{2}:\d{2}", str(batidas_raw))
    motivo = str(motivo_raw).upper() if motivo_raw else ""
    alertas = []
    
    justificativas = ["ABONO", "FÉRIAS", "FERIAS", "RECESSO", "DISPENSA", "FOLGA", "FERIADO", "ATESTADO", "MÉDICO", "FACULTATIVO", "LICENÇA"]
    is_justificado = any(x in motivo for x in justificativas)
    is_12x36 = "12X36" in str(escala_str).upper()
    is_fds = any(x in d_str.upper() for x in ["SAB", "DOM", "SÁB"])

    if not bats:
        if is_12x36: motivo = "FOLGA DE ESCALA" if not is_justificado else motivo
        elif not is_fds and not is_justificado: alertas.append("FALTA NÃO JUSTIFICADA")
    else:
        if len(bats) == 2 and not is_justificado: alertas.append("CARGA HORÁRIA INCOMPLETA (2 BATIDAS)")
        if len(bats) % 2 != 0 and not is_justificado: alertas.append("MARCAÇÃO ÍMPAR")
        if len(bats) >= 3:
            try:
                s_int = datetime.strptime(bats[1], "%H:%M")
                v_int = datetime.strptime(bats[2], "%H:%M")
                if v_int <= s_int: v_int += timedelta(days=1)
                duracao = round((v_int - s_int).total_seconds() / 60)
                limite = 15 if "30H" in str(escala_str).upper() else 60
                if duracao < limite:
                    h, m = divmod(duracao, 60)
                    alertas.append(f"INTERVALO IRREGULAR ({h:02d}:{m:02d})")
            except: pass
    if is_justificado: alertas = []
    return {"data": d_str, "batidas": bats, "alertas": alertas, "motivo": motivo, "blink": len(alertas) > 0}

# --- FUNÇÃO GERADORA DE PDF (LAYOUT GOLD SYNC) ---
def gerar_pdf_bytes(dados_clientes):
    pdf = FPDF()
    pdf.set_margins(10, 10, 10)
    pdf.set_auto_page_break(auto=True, margin=15)
    
    for cli, colabs in dados_clientes.items():
        colabs_erro = {n: d for n, d in colabs.items() if any(j['alertas'] for j in d['jornadas'])}
        if not colabs_erro: continue
        
        pdf.add_page()
        pdf.set_fill_color(15, 23, 42); pdf.rect(0, 0, 210, 25, 'F')
        pdf.set_font("Arial", "B", 12); pdf.set_text_color(255, 255, 255)
        pdf.cell(190, 10, "RELATÓRIO DE INCONSISTÊNCIAS DE JORNADA", border=0, ln=1, align="C")
        pdf.set_text_color(0); pdf.ln(10)
        
        pdf.set_font("Arial", "B", 10)
        pdf.cell(0, 10, f"CLIENTE: {cli}", ln=True)
        
        for nome, d in colabs_erro.items():
            h = d['header']
            pdf.ln(2)
            pdf.set_font("Arial", "B", 7); pdf.set_fill_color(240, 240, 240)
            info = f"NOME: {h['nome']} | MAT: {h['matricula']} | CPF: {h['cpf']} | CARGO: {h['cargo']}\nESCALA: {h['escala']} | PERÍODO: {h['periodo']}"
            pdf.multi_cell(190, 5, info, border=1, fill=True)
            
            pdf.cell(30, 6, "Data", 1, 0, 'C', fill=True)
            pdf.cell(50, 6, "Batidas", 1, 0, 'C', fill=True)
            pdf.cell(110, 6, "Inconsistências Detectadas", 1, 1, 'C', fill=True)
            
            pdf.set_font("Arial", "", 8)
            for j in d['jornadas']:
                if j['alertas']:
                    txt_err = " / ".join(j['alertas'])
                    txt_bats = " - ".join(j['batidas']) if j['batidas'] else "---"
                    
                    # Cálculo de altura sincronizada
                    num_linhas = len(pdf.multi_cell(110, 5, txt_err, split_only=True))
                    h_row = max(6, num_linhas * 5)
                    
                    cx, cy = pdf.get_x(), pdf.get_y()
                    pdf.cell(30, h_row, j['data'], border=1, align='C')
                    pdf.cell(50, h_row, txt_bats, border=1, align='C')
                    pdf.set_text_color(200, 0, 0)
                    pdf.multi_cell(110, 5, txt_err, border=1)
                    pdf.set_text_color(0)
                    pdf.set_xy(cx, cy + h_row)
            pdf.ln(4)
    return pdf.output(dest='S').encode('latin-1')

# --- INTERFACE WEB ---
st.sidebar.markdown("<h2 style='color:white;'>AUDITOR ENTERPRISE</h2>", unsafe_allow_html=True)

# Upload
uploaded_files = st.sidebar.file_uploader("📂 Importar PDFs de Ponto", accept_multiple_files=True, type="pdf")

if uploaded_files:
    todos_dados = defaultdict(lambda: defaultdict(dict))
    for f in uploaded_files:
        with pdfplumber.open(f) as pdf:
            last_h = None
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                
                def find(label):
                    pattern = rf"{label}:?\s*(.*?)(?=\s*\||\s*Matrícula:|\s*CPF:|\s*Escala:|\s*Cargo:|\s*Período:|\s*$|\n)"
                    m = re.search(pattern, text, re.I)
                    return m.group(1).strip() if m else "N/A"
                
                nome_raw = find("Colaborador")
                if nome_raw == "N/A" and last_h: h = last_h
                else:
                    per = re.search(r"(\d{2}/\d{2}/\d{4}\s*até\s*\d{2}/\d{2}/\d{4})", text)
                    h = {
                        "cliente": find("Cliente"), "nome": nome_raw.split("Matrícula")[0].strip(),
                        "matricula": find("Matrícula"), "cpf": find("CPF"), "escala": find("Escala"),
                        "cargo": find("Cargo"), "periodo": per.group(0) if per else find("Período")
                    }
                    last_h = h
                
                table = page.extract_table()
                if table:
                    jornadas = []
                    for r in table:
                        if len(r) >= 3:
                            res = analisar_linha(r[0], r[1], r[2], h['escala'])
                            if res: jornadas.append(res)
                    
                    if h['nome'] not in todos_dados[h['cliente']]:
                        todos_dados[h['cliente']][h['nome']] = {"header": h, "jornadas": []}
                    todos_dados[h['cliente']][h['nome']]["jornadas"].extend(jornadas)

    # Botão de PDF
    pdf_data = gerar_pdf_bytes(todos_dados)
    st.sidebar.download_button(label="📄 GERAR PDF (DIVERGÊNCIAS)", data=pdf_data, file_name="Auditoria_Web_Diamond.pdf", mime="application/pdf")

    # Zoom
    st.sidebar.markdown("<hr>", unsafe_allow_html=True)
    st.sidebar.write("🔍 Ajuste de Zoom")
    col_z1, col_z2 = st.sidebar.columns(2)
    if col_z1.button("-"): st.session_state.zoom = max(0.6, st.session_state.zoom - 0.1); st.rerun()
    if col_z2.button("+"): st.session_state.zoom = min(1.8, st.session_state.zoom + 0.1); st.rerun()

    # Lista de Colaboradores
    st.sidebar.markdown("<br><p class='sidebar-text'>COLABORADORES</p>", unsafe_allow_html=True)
    lista_final = []
    for cli, colabs in todos_dados.items():
        for nome, d in colabs.items():
            err_count = sum(1 for j in d['jornadas'] if j['alertas'])
            lista_final.append({"id": nome, "nome": f"{nome} ({err_count}!)"})
    
    escolha = st.sidebar.radio("Selecione para ver detalhes:", [x['nome'] for x in lista_final])
    nome_selecionado = escolha.split(" (")[0]

    # Renderização Principal
    for cli, colabs in todos_dados.items():
        if nome_selecionado in colabs:
            u = colabs[nome_selecionado]
            st.title(u['header']['nome'])
            st.info(f"MAT: {u['header']['matricula']} | CPF: {u['header']['cpf']} | CARGO: {u['header']['cargo']} | ESCALA: {u['header']['escala']} | PERÍODO: {u['header']['periodo']}")
            
            # Cards Dinâmicos
            cols_per_row = max(1, int(4 / st.session_state.zoom))
            rows = [u['jornadas'][i:i + cols_per_row] for i in range(0, len(u['jornadas']), cols_per_row)]
            
            for row_days in rows:
                cols = st.columns(cols_per_row)
                for i, dia in enumerate(row_days):
                    with cols[i]:
                        border_color = "#EF4444" if dia['alertas'] else "#E2E8F0"
                        st.markdown(f"""
                        <div class="card" style="border-color: {border_color}; font-size: {int(14 * st.session_state.zoom)}px;">
                            <small>{dia['data']}</small><br>
                            <b style="font-family: monospace;">{' | '.join(dia['batidas']) if dia['batidas'] else 'Sem registro'}</b><br>
                            {" ".join([f"<div class='blink'>{a}</div>" for a in dia['alertas']]) if dia['alertas'] else f"<div style='color:#6366F1'>{dia['motivo']}</div>"}
                        </div>
                        """, unsafe_allow_html=True)
else:
    st.title("📊 Auditoria Enterprise Web")
    st.warning("Arraste seus arquivos PDF na barra lateral para começar.")
