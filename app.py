import streamlit as st
import pdfplumber
import re
from datetime import datetime, timedelta
import pandas as pd

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Auditor DP Online", layout="wide")

# --- CSS PARA O EFEITO PISCANTE ---
st.markdown("""
    <style>
    @keyframes blinker { 50% { opacity: 0; } }
    .blink { animation: blinker 1s linear infinite; color: red; font-weight: bold; }
    .card { border: 1px solid #ddd; padding: 15px; border-radius: 10px; background: white; margin-bottom: 10px; }
    </style>
""", unsafe_allow_html=True)

# --- SISTEMA DE LOGIN SIMPLES ---
if 'logado' not in st.session_state:
    st.session_state.logado = False

def login():
    st.title("Acesso Restrito - Departamento Pessoal")
    usuario = st.text_input("Usuário")
    senha = st.text_input("Senha", type="password")
    if st.button("Entrar"):
        if usuario == "admin_dp" and senha == "fms_ponto_2024":
            st.session_state.logado = True
            st.rerun()
        else:
            st.error("Credenciais inválidas")

if not st.session_state.logado:
    login()
    st.stop()

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
    return {"data": d_str, "batidas": bats, "alertas": alertas, "motivo": motivo}

# --- INTERFACE DO DASHBOARD ---
st.sidebar.title("📂 Importação")
uploaded_files = st.sidebar.file_uploader("Arraste os PDFs aqui", accept_multiple_files=True, type="pdf")

if uploaded_files:
    todos_dados = {}
    for file in uploaded_files:
        with pdfplumber.open(file) as pdf:
            last_h = None
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                
                def find(label):
                    m = re.search(rf"{label}:?\s*(.*?)(?=\s*\||\s*Matrícula:|\s*CPF:|\s*Escala:|\s*Cargo:|\s*Período:|\s*$|\n)", text, re.I)
                    return m.group(1).strip() if m else "N/A"
                
                nome = find("Colaborador").split("Matrícula")[0].strip()
                if nome == "N/A" and last_h: h = last_h
                else:
                    h = {"nome": nome, "mat": find("Matrícula"), "cpf": find("CPF"), "escala": find("Escala"), "per": find("Período")}
                    last_h = h
                
                table = page.extract_table()
                if table:
                    for r in table:
                        if len(r) >= 3:
                            res = analisar_linha(r[0], r[1], r[2], h['escala'])
                            if res:
                                if h['nome'] not in todos_dados: todos_dados[h['nome']] = {"h": h, "j": []}
                                todos_dados[h['nome']]["j"].append(res)

    # BARRA LATERAL COM NOMES
    selecionado = st.sidebar.selectbox("Selecione o Colaborador", list(todos_dados.keys()))
    
    if selecionado:
        user = todos_dados[selecionado]
        st.header(f"👤 {selecionado}")
        st.info(f"MAT: {user['h']['mat']} | CPF: {user['h']['cpf']} | Escala: {user['h']['escala']}")
        
        # EXIBIÇÃO EM CARDS
        cols = st.columns(3)
        for i, dia in enumerate(user['j']):
            with cols[i % 3]:
                st.markdown(f"""
                <div class="card">
                    <b>{dia['data']}</b><br>
                    <code>{' | '.join(dia['batidas']) if dia['batidas'] else 'Sem registro'}</code><br>
                    {"<span class='blink'>" + " / ".join(dia['alertas']) + "</span>" if dia['alertas'] else f"<small>{dia['motivo']}</small>"}
                </div>
                """, unsafe_allow_html=True)
else:
    st.title("📊 Auditoria de Ponto Online")
    st.write("Aguardando upload de arquivos pelo menu lateral...")