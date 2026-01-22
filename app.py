import streamlit as st
import pdfplumber
import re
from datetime import datetime, timedelta
import pandas as pd
import io

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
    /* Ajuste para a lista de seleção parecer um menu lateral limpo */
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

# --- MOTOR DE CÁLCULO (MANTIDO INTACTO) ---
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

# --- FUNÇÃO GERADORA DE RELATÓRIO EXCEL ---
def gerar_excel(dados_completos, apenas_erros=False, filtro_nomes=None):
    linhas_relatorio = []
    
    for nome, info in dados_completos.items():
        # Se houver filtro de nomes, pula quem não está na lista
        if filtro_nomes and nome not in filtro_nomes:
            continue
            
        for dia in info['j']:
            # Se for "Apenas Erros", pula dias sem alerta
            if apenas_erros and not dia['alertas']:
                continue
                
            linhas_relatorio.append({
                "Colaborador": nome,
                "Matrícula": info['h']['mat'],
                "Escala": info['h']['escala'],
                "Data": dia['data'],
                "Batidas": " | ".join(dia['batidas']),
                "Motivo Original": dia['motivo'],
                "Status/Inconsistência": " ; ".join(dia['alertas']) if dia['alertas'] else "OK"
            })
            
    if not linhas_relatorio:
        return None
        
    df = pd.DataFrame(linhas_relatorio)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Auditoria')
        # Ajuste automático de colunas (opcional, visual)
        worksheet = writer.sheets['Auditoria']
        for i, col in enumerate(df.columns):
            width = max(df[col].astype(str).map(len).max(), len(col)) + 2
            worksheet.set_column(i, i, width)
            
    return output.getvalue()

# --- INTERFACE DO DASHBOARD ---
st.sidebar.title("📂 Importação")
uploaded_files = st.sidebar.file_uploader("Arraste os PDFs aqui", accept_multiple_files=True, type="pdf")

if uploaded_files:
    todos_dados = {}
    
    # Processamento (Mantido)
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

    # --- NOVO SISTEMA DE NAVEGAÇÃO LATERAL ---
    st.sidebar.markdown("---")
    st.sidebar.header("🔍 Colaboradores")
    
    # 1. Busca
    termo_busca = st.sidebar.text_input("Buscar nome...", "").upper()
    
    # 2. Filtragem da lista
    lista_nomes = sorted(list(todos_dados.keys()))
    nomes_filtrados = [n for n in lista_nomes if termo_busca in n.upper()]
    
    if not nomes_filtrados:
        st.sidebar.warning("Nenhum colaborador encontrado.")
        selecionado = None
    else:
        # 3. Lista estilo Radio (substitui o selectbox)
        st.sidebar.caption(f"Mostrando {len(nomes_filtrados)} de {len(lista_nomes)}")
        selecionado = st.sidebar.radio("Selecione na lista:", nomes_filtrados)

    # --- ÁREA DE RELATÓRIOS ---
    st.sidebar.markdown("---")
    with st.sidebar.expander("📄 Gerar Relatórios (Excel)"):
        apenas_inconsistencias = st.checkbox("Apenas inconsistências", value=True)
        filtrar_selecionado = st.checkbox("Apenas colaborador atual", value=False)
        
        if st.button("Baixar Relatório"):
            filtro_nomes = [selecionado] if filtrar_selecionado and selecionado else None
            
            excel_data = gerar_excel(todos_dados, apenas_inconsistencias, filtro_nomes)
            
            if excel_data:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M")
                st.download_button(
                    label="📥 Clique para Salvar (XLSX)",
                    data=excel_data,
                    file_name=f"Auditoria_Ponto_{timestamp}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                st.warning("Nenhum dado encontrado com os filtros atuais.")

    # --- VISUALIZAÇÃO PRINCIPAL (MANTIDA) ---
    if selecionado:
        user = todos_dados[selecionado]
        
        # Cabeçalho
        with st.container():
            c1, c2 = st.columns([0.5, 4])
            with c1: st.image("https://cdn-icons-png.flaticon.com/512/3135/3135715.png", width=80)
            with c2: 
                st.title(f"{selecionado}")
                st.markdown(f"**Matrícula:** {user['h']['mat']} | **Escala:** {user['h']['escala']} | **Período:** {user['h']['per']}")

        st.divider()
        ver_apenas_erros = st.checkbox("Ver apenas dias com ocorrências na tela", value=False)

        cols = st.columns(3)
        count = 0
        for dia in user['j']:
            tem_alerta = len(dia['alertas']) > 0
            if ver_apenas_erros and not tem_alerta: continue

            with cols[count % 3]:
                border = "red" if tem_alerta else "#ddd"
                batidas_html = ' | '.join(dia['batidas']) if dia['batidas'] else '<span style="color:#ccc">Sem registro</span>'
                
                if dia['alertas']:
                    status_html = f"<div class='blink'>{'<br>'.join(dia['alertas'])}</div>"
                elif dia['motivo']:
                    status_html = f"<div class='status-ok'>✅ {dia['motivo']}</div>"
                else:
                    status_html = "<div class='status-ok'>Regular</div>"
                
                if dia['alertas'] and dia['motivo']:
                     status_html += f"<br><small style='color:grey'>{dia['motivo']}</small>"

                st.markdown(f"""
                <div class="card" style="border-left: 5px solid {border};">
                    <b>📅 {dia['data']}</b><hr style="margin:5px 0">
                    <div style="font-family:monospace; font-size:1.1em">{batidas_html}</div>
                    <div style="margin-top:8px; font-size:0.9em">{status_html}</div>
                </div>
                """, unsafe_allow_html=True)
                count += 1
        
        if count == 0: st.info("Nenhuma ocorrência encontrada para este filtro visual.")

else:
    st.title("📊 Auditoria de Ponto Online")
    st.markdown("### Bem-vindo! Carregue os PDFs no menu lateral para começar.")
