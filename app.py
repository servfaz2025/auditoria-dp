import streamlit as st
import pdfplumber
import re
from datetime import datetime, timedelta
import pandas as pd
import io
import gc  # Importante para limpar memória em arquivos grandes

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Auditor DP Online", layout="wide", page_icon="🕵️")

# --- CSS PARA O EFEITO PISCANTE E ESTILOS ---
st.markdown("""
    <style>
    @keyframes blinker { 50% { opacity: 0; } }
    .blink { animation: blinker 1s linear infinite; color: red; font-weight: bold; }
    .card { border: 1px solid #ddd; padding: 15px; border-radius: 10px; background: white; margin-bottom: 10px; box-shadow: 2px 2px 5px rgba(0,0,0,0.1); }
    .status-ok { color: green; font-weight: bold; }
    .status-warning { color: orange; font-weight: bold; }
    .stRadio > div { overflow-y: auto; max-height: 400px; }
    </style>
""", unsafe_allow_html=True)

# --- SISTEMA DE LOGIN SIMPLES ---
if 'logado' not in st.session_state:
    st.session_state.logado = False

def login():
    st.title("🔐 Acesso Restrito - Departamento Pessoal")
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
        if len(bats) % 2 != 0:
            if not is_justificado: alertas.append("MARCAÇÃO ÍMPAR")
        if len(bats) == 2 and not is_justificado and not is_fds:
            alertas.append("CARGA HORÁRIA INCOMPLETA (2 BATIDAS)")
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

# --- FUNÇÃO DE PROCESSAMENTO COM CACHE E LIMPEZA DE MEMÓRIA ---
@st.cache_data(show_spinner=False, ttl=3600)
def processar_pdfs(files_bytes, file_names):
    """
    Recebe os bytes dos arquivos para permitir o cache do Streamlit.
    """
    dados_processados = {}
    
    # Criamos um iterador para a barra de progresso fora daqui
    for idx, (f_bytes, f_name) in enumerate(zip(files_bytes, file_names)):
        try:
            # Abre o PDF a partir da memória (BytesIO)
            with pdfplumber.open(io.BytesIO(f_bytes)) as pdf:
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
                                    if h['nome'] not in dados_processados: 
                                        dados_processados[h['nome']] = {"h": h, "j": []}
                                    dados_processados[h['nome']]["j"].append(res)
        except Exception as e:
            # Retorna o erro para exibir sem quebrar tudo
            print(f"Erro ao processar {f_name}: {e}")
            continue
        
        # OTIMIZAÇÃO: Força limpeza de memória após cada arquivo grande
        gc.collect()
        
    return dados_processados

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
        worksheet = writer.sheets['Auditoria']
        for i, col in enumerate(df.columns):
            width = max(df[col].astype(str).map(len).max(), len(col)) + 2
            worksheet.set_column(i, i, width)
    return output.getvalue()

# --- INTERFACE ---
st.sidebar.title("📂 Importação")
uploaded_files = st.sidebar.file_uploader(
    "Arraste PDFs (Suporta arquivos grandes)", 
    accept_multiple_files=True, 
    type="pdf"
)

todos_dados = {}

if uploaded_files:
    # Preparação para o processamento (Extrai bytes para permitir cache)
    files_bytes = [f.getvalue() for f in uploaded_files]
    files_names = [f.name for f in uploaded_files]
    
    with st.spinner(f"Processando {len(uploaded_files)} arquivos... Isso pode levar alguns instantes."):
        # Chama a função otimizada com cache
        todos_dados = processar_pdfs(files_bytes, files_names)

    if not todos_dados:
        st.error("Não foi possível extrair dados. Verifique se o PDF contém texto selecionável.")
    else:
        st.sidebar.success(f"Processado com sucesso!")
        
        # --- NAVEGAÇÃO ---
        st.sidebar.markdown("---")
        st.sidebar.header("🔍 Colaboradores")
        termo_busca = st.sidebar.text_input("Buscar nome...", "").upper()
        
        lista_nomes = sorted(list(todos_dados.keys()))
        nomes_filtrados = [n for n in lista_nomes if termo_busca in n.upper()]
        
        if not nomes_filtrados:
            st.sidebar.warning("Nenhum encontrado.")
            selecionado = None
        else:
            st.sidebar.caption(f"{len(nomes_filtrados)} encontrados")
            selecionado = st.sidebar.radio("Selecione:", nomes_filtrados)

        # --- RELATÓRIOS ---
        st.sidebar.markdown("---")
        with st.sidebar.expander("📄 Relatórios (Excel)"):
            apenas_inc = st.checkbox("Apenas inconsistências", value=True)
            filtro_sel = st.checkbox("Apenas atual", value=False)
            if st.button("Baixar XLSX"):
                f_nomes = [selecionado] if filtro_sel and selecionado else None
                data_xls = gerar_excel(todos_dados, apenas_inc, f_nomes)
                if data_xls:
                    ts = datetime.now().strftime("%d%m_%H%M")
                    st.download_button("📥 Download", data_xls, f"Auditoria_{ts}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                else:
                    st.warning("Sem dados para gerar.")

        # --- VISUALIZAÇÃO ---
        if selecionado:
            user = todos_dados[selecionado]
            with st.container():
                c1, c2 = st.columns([0.5, 4])
                with c1: st.image("https://cdn-icons-png.flaticon.com/512/3135/3135715.png", width=80)
                with c2: 
                    st.title(f"{selecionado}")
                    st.markdown(f"**Matrícula:** {user['h']['mat']} | **Escala:** {user['h']['escala']}")

            st.divider()
            ver_erros = st.checkbox("Focar apenas nas ocorrências", value=False)
            cols = st.columns(3)
            count = 0
            
            for dia in user['j']:
                tem_alerta = len(dia['alertas']) > 0
                if ver_erros and not tem_alerta: continue

                with cols[count % 3]:
                    border = "red" if tem_alerta else "#ddd"
                    batidas = ' | '.join(dia['batidas']) if dia['batidas'] else '<span style="color:#ccc">---</span>'
                    
                    status = ""
                    if dia['alertas']: status = f"<div class='blink'>{'<br>'.join(dia['alertas'])}</div>"
                    elif dia['motivo']: status = f"<div class='status-ok'>✅ {dia['motivo']}</div>"
                    else: status = "<div class='status-ok'>Regular</div>"
                    
                    if dia['alertas'] and dia['motivo']: status += f"<br><small>{dia['motivo']}</small>"

                    st.markdown(f"""
                    <div class="card" style="border-left: 5px solid {border};">
                        <b>{dia['data']}</b><hr style="margin:5px 0">
                        <div style="font-family:monospace;">{batidas}</div>
                        <div style="margin-top:5px;font-size:0.9em">{status}</div>
                    </div>""", unsafe_allow_html=True)
                    count += 1
            if count == 0: st.info("Nada a exibir com este filtro.")
else:
    st.title("📊 Auditoria de Ponto Online")
    st.info("Faça upload dos PDFs na barra lateral.")
