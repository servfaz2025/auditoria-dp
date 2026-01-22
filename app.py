import streamlit as st
import pdfplumber
import re
from datetime import datetime, timedelta
import pandas as pd
import io
import gc

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Auditor DP Online", layout="wide", page_icon="🕵️")

# --- CSS ---
st.markdown("""
    <style>
    @keyframes blinker { 50% { opacity: 0; } }
    .blink { animation: blinker 1s linear infinite; color: red; font-weight: bold; }
    .card { border: 1px solid #ddd; padding: 15px; border-radius: 10px; background: white; margin-bottom: 10px; box-shadow: 2px 2px 5px rgba(0,0,0,0.1); }
    .status-ok { color: green; font-weight: bold; }
    .stRadio > div { overflow-y: auto; max-height: 400px; }
    </style>
""", unsafe_allow_html=True)

# --- LOGIN ---
if 'logado' not in st.session_state:
    st.session_state.logado = False

def login():
    st.title("🔐 Acesso Restrito - DP")
    col1, col2 = st.columns([1, 2])
    with col1:
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        if st.button("Entrar", type="primary"):
            if usuario == "admin_dp" and senha == "fms_ponto_2024":
                st.session_state.logado = True
                st.rerun()
            else:
                st.error("Credenciais inválidas")

if not st.session_state.logado:
    login()
    st.stop()

# --- MOTOR DE CÁLCULO ---
def analisar_linha(data_raw, batidas_raw, motivo_raw, escala_str):
    d_str = str(data_raw).strip()
    if not re.match(r"^\d{2}", d_str): return None
    
    bats = re.findall(r"\d{2}:\d{2}", str(batidas_raw))
    motivo = str(motivo_raw).replace('\n', ' ').strip().upper() if motivo_raw else ""
    alertas = []
    
    termos_afastamento_total = [
        "FÉRIAS", "FERIAS", "RECESSO", "DISPENSA", "FOLGA", "FERIADO", 
        "ATESTADO", "MÉDICO", "FACULTATIVO", "LICENÇA", "LICENCA",
        "AFASTAMENTO", "SUP. 15D", "INSS", "DSR"
    ]
    termos_abono_parcial = ["ABONO", "ABONADO", "ABONADAS", "ESQUECIMENTO", "DECLARAÇÃO"]
    
    is_afastado_total = any(x in motivo for x in termos_afastamento_total)
    is_abonado_parcial = any(x in motivo for x in termos_abono_parcial)
    is_justificado = is_afastado_total or is_abonado_parcial
    is_12x36 = "12X36" in str(escala_str).upper()
    is_fds = any(x in d_str.upper() for x in ["SAB", "SÁB", "DOM"])
    is_escala_30h = "30" in str(escala_str)

    if not bats:
        if is_afastado_total: pass
        elif is_12x36: motivo = "FOLGA DE ESCALA" if not motivo else motivo
        elif not is_fds and not is_justificado: alertas.append("FALTA NÃO JUSTIFICADA")
    else:
        if len(bats) % 2 != 0 and not is_justificado: alertas.append("MARCAÇÃO ÍMPAR")
        if len(bats) == 2 and not is_justificado and not is_fds: alertas.append("CARGA HORÁRIA INCOMPLETA (2 BATIDAS)")
        if len(bats) >= 3:
            try:
                s_int = datetime.strptime(bats[1], "%H:%M")
                v_int = datetime.strptime(bats[2], "%H:%M")
                if v_int < s_int: v_int += timedelta(days=1)
                duracao_minutos = round((v_int - s_int).total_seconds() / 60)
                limite_minimo = 15 if is_escala_30h else 60
                if duracao_minutos < limite_minimo:
                    h, m = divmod(duracao_minutos, 60)
                    alertas.append(f"INTERVALO IRREGULAR ({h:02d}:{m:02d}) - Min: {limite_minimo}m")
            except: pass

    return {"data": d_str, "batidas": bats, "alertas": alertas, "motivo": motivo}

# --- PROCESSAMENTO OTIMIZADO (SEM CACHE DE UPLOAD) ---
def processar_arquivo(file):
    dados_locais = {}
    try:
        # Abre diretamente do buffer sem criar cópia em bytes
        with pdfplumber.open(file) as pdf:
            last_h = None
            # Processa página por página e libera memória
            for page in pdf.pages:
                text = page.extract_text()
                if not text: 
                    del text # Libera memória
                    continue
                
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
                                if h['nome'] not in dados_locais: dados_locais[h['nome']] = {"h": h, "j": []}
                                dados_locais[h['nome']]["j"].append(res)
                
                # LIMPEZA CRÍTICA DE MEMÓRIA
                del text
                del table
                gc.collect()
                
    except Exception as e:
        st.error(f"Erro ao processar arquivo: {e}")
    
    return dados_locais

def gerar_excel(dados_completos, apenas_erros=False, filtro_nomes=None):
    linhas_relatorio = []
    for nome, info in dados_completos.items():
        if filtro_nomes and nome not in filtro_nomes: continue
        for dia in info['j']:
            if apenas_erros and not dia['alertas']: continue
            linhas_relatorio.append({
                "Colaborador": nome,
                "Matrícula": info['h']['mat'],
                "Escala": info['h']['escala'],
                "Data": dia['data'],
                "Batidas": " | ".join(dia['batidas']),
                "Motivo Original": dia['motivo'],
                "Status": " ; ".join(dia['alertas']) if dia['alertas'] else "OK"
            })
            
    if not linhas_relatorio: return None
    df = pd.DataFrame(linhas_relatorio)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Auditoria')
    return output.getvalue()

# --- UI PRINCIPAL ---
st.sidebar.title("📂 Importação")
uploaded_files = st.sidebar.file_uploader(
    "Arraste PDFs (Máx 200MB/arquivo)", 
    accept_multiple_files=True, 
    type="pdf"
)

# Container de dados na sessão para persistir após interações
if 'dados_auditados' not in st.session_state:
    st.session_state.dados_auditados = {}

if uploaded_files:
    # Botão para iniciar o processamento (evita travamento automático)
    if st.sidebar.button("🚀 Processar Arquivos", type="primary"):
        dados_temp = {}
        bar = st.sidebar.progress(0)
        
        for idx, file in enumerate(uploaded_files):
            d = processar_arquivo(file)
            # Mescla dicionários
            for k, v in d.items():
                if k not in dados_temp: dados_temp[k] = v
                else: dados_temp[k]['j'].extend(v['j'])
            
            bar.progress((idx + 1) / len(uploaded_files))
            gc.collect() # Limpeza extra entre arquivos
            
        st.session_state.dados_auditados = dados_temp
        st.rerun()

# Recupera dados da sessão
todos_dados = st.session_state.dados_auditados

if todos_dados:
    st.sidebar.success(f"Dados carregados!")
    
    # --- FILTROS ---
    st.sidebar.markdown("---")
    termo_busca = st.sidebar.text_input("Buscar Colaborador", "").upper()
    lista_nomes = sorted(list(todos_dados.keys()))
    nomes_filtrados = [n for n in lista_nomes if termo_busca in n.upper()]
    
    if nomes_filtrados:
        selecionado = st.sidebar.radio(f"Encontrados ({len(nomes_filtrados)}):", nomes_filtrados)
    else:
        st.sidebar.warning("Ninguém encontrado.")
        selecionado = None

    # --- RELATÓRIOS ---
    st.sidebar.markdown("---")
    with st.sidebar.expander("📄 Relatório Excel"):
        apenas_inc = st.checkbox("Apenas inconsistências", value=True)
        filtro_sel = st.checkbox("Apenas atual selecionado", value=False)
        if st.button("Baixar XLSX"):
            f_nomes = [selecionado] if filtro_sel and selecionado else None
            data_xls = gerar_excel(todos_dados, apenas_inc, f_nomes)
            if data_xls:
                ts = datetime.now().strftime("%H%M")
                st.download_button("📥 Download", data_xls, f"Auditoria_{ts}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            else:
                st.warning("Sem dados para gerar.")

    # --- VISUALIZAÇÃO ---
    if selecionado:
        user = todos_dados[selecionado]
        st.title(f"👤 {selecionado}")
        st.caption(f"Matrícula: {user['h']['mat']} | Escala: {user['h']['escala']}")
        st.divider()
        
        ver_erros = st.checkbox("Ver apenas ocorrências", value=False)
        cols = st.columns(3)
        count = 0
        
        for dia in user['j']:
            tem_alerta = len(dia['alertas']) > 0
            if ver_erros and not tem_alerta: continue

            with cols[count % 3]:
                border = "red" if tem_alerta else "#ddd"
                batidas = ' | '.join(dia['batidas']) if dia['batidas'] else '<span style="color:#ccc">---</span>'
                status = ""
                
                if dia['alertas']: 
                    status = f"<div class='blink'>{'<br>'.join(dia['alertas'])}</div>"
                elif dia['motivo']: 
                    status = f"<div class='status-ok'>✅ {dia['motivo']}</div>"
                else: 
                    status = "<div class='status-ok'>Regular</div>"
                
                if dia['alertas'] and dia['motivo']: status += f"<br><small>{dia['motivo']}</small>"

                st.markdown(f"""
                <div class="card" style="border-left: 5px solid {border};">
                    <b>{dia['data']}</b><hr style="margin:5px 0">
                    <div style="font-family:monospace;">{batidas}</div>
                    <div style="margin-top:5px;font-size:0.9em">{status}</div>
                </div>""", unsafe_allow_html=True)
                count += 1
        if count == 0: st.info("Nada a exibir.")

else:
    st.title("📊 Auditoria de Ponto")
    st.info("Por favor, faça upload dos arquivos PDF no menu lateral e clique em 'Processar'.")
