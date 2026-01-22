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
    .card { border: 1px solid #ddd; padding: 15px; border-radius: 10px; background: white; margin-bottom: 10px; box-shadow: 2px 2px 5px rgba(0,0,0,0.1); }
    .status-ok { color: green; font-weight: bold; }
    .status-warning { color: orange; font-weight: bold; }
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

# --- MOTOR DE CÁLCULO REVISADO ---
def analisar_linha(data_raw, batidas_raw, motivo_raw, escala_str):
    d_str = str(data_raw).strip()
    
    # Ignora linhas que não começam com data (ex: cabeçalhos extras)
    if not re.match(r"^\d{2}", d_str): return None
    
    # Extração e Limpeza
    bats = re.findall(r"\d{2}:\d{2}", str(batidas_raw))
    # Remove quebras de linha e espaços extras do motivo
    motivo = str(motivo_raw).replace('\n', ' ').strip().upper() if motivo_raw else ""
    
    alertas = []
    
    # --- LISTAS DE PALAVRAS-CHAVE ---
    # Justificativas que abonam o dia inteiro (não precisa ter batida)
    termos_afastamento_total = [
        "FÉRIAS", "FERIAS", "RECESSO", "DISPENSA", "FOLGA", "FERIADO", 
        "ATESTADO", "MÉDICO", "FACULTATIVO", "LICENÇA", "LICENCA",
        "AFASTAMENTO", "SUP. 15D", "INSS", "DSR"
    ]
    
    # Justificativas que aceitam batidas parciais ou ímpares
    termos_abono_parcial = [
        "ABONO", "ABONADO", "ABONADAS", "ESQUECIMENTO", "DECLARAÇÃO"
    ]
    
    # Verificações booleanas
    is_afastado_total = any(x in motivo for x in termos_afastamento_total)
    is_abonado_parcial = any(x in motivo for x in termos_abono_parcial)
    is_justificado = is_afastado_total or is_abonado_parcial
    
    is_12x36 = "12X36" in str(escala_str).upper()
    is_fds = any(x in d_str.upper() for x in ["SAB", "SÁB", "DOM"])
    
    # Detecta se é escala de 30H (procura "30" na string da escala)
    is_escala_30h = "30" in str(escala_str)

    # --- LÓGICA DE ANÁLISE ---

    # 1. Análise de Falta Integral
    if not bats:
        if is_afastado_total:
            pass # Está justificado (ex: Afastamento Sup 15D), não faz nada.
        elif is_12x36:
             motivo = "FOLGA DE ESCALA" if not motivo else motivo
        elif not is_fds and not is_justificado:
            alertas.append("FALTA NÃO JUSTIFICADA")
    
    # 2. Análise das Batidas Existentes
    else:
        # Verifica quantidade de batidas (Pares vs Ímpares)
        if len(bats) % 2 != 0:
            if not is_justificado:
                alertas.append("MARCAÇÃO ÍMPAR")
        
        # Verifica carga horária incompleta (ex: só entrou e saiu de manhã)
        # Se tiver "HORAS ABONADAS" no motivo, ignora esse erro.
        if len(bats) == 2 and not is_justificado and not is_fds:
            alertas.append("CARGA HORÁRIA INCOMPLETA (2 BATIDAS)")

        # 3. Verificação de Intervalo (Almoço)
        if len(bats) >= 3: # Precisa de pelo menos Entrada, Saída p/ almoço, Volta do almoço
            try:
                # Pega a batida 2 (saída intervalo) e batida 3 (volta intervalo)
                # bats[1] é a segunda batida, bats[2] é a terceira
                s_int = datetime.strptime(bats[1], "%H:%M")
                v_int = datetime.strptime(bats[2], "%H:%M")
                
                # Ajuste para virada de noite (embora raro em intervalo de almoço)
                if v_int < s_int: v_int += timedelta(days=1)
                
                duracao_minutos = round((v_int - s_int).total_seconds() / 60)
                
                # Definição do Limite
                # Se for escala 30h, o limite mínimo é 15 min. Se for padrão, 60 min.
                limite_minimo = 15 if is_escala_30h else 60
                
                # A regra é: Intervalo deve ser MAIOR ou IGUAL ao limite.
                # Se duracao < limite, é irregular.
                # Ex: 15 < 15 é Falso (Regular). 14 < 15 é Verdadeiro (Irregular).
                if duracao_minutos < limite_minimo:
                    h, m = divmod(duracao_minutos, 60)
                    alertas.append(f"INTERVALO IRREGULAR ({h:02d}:{m:02d}) - Min: {limite_minimo}m")
            except:
                pass

    return {"data": d_str, "batidas": bats, "alertas": alertas, "motivo": motivo}

# --- INTERFACE DO DASHBOARD ---
st.sidebar.title("📂 Importação de Cartão Ponto")
st.sidebar.markdown("---")
uploaded_files = st.sidebar.file_uploader("Arraste os PDFs aqui", accept_multiple_files=True, type="pdf")

if uploaded_files:
    todos_dados = {}
    total_files = len(uploaded_files)
    progresso = st.sidebar.progress(0)
    
    for idx, file in enumerate(uploaded_files):
        with pdfplumber.open(file) as pdf:
            last_h = None
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                
                def find(label):
                    # Regex ajustada para pegar até o próximo rótulo ou fim da linha
                    m = re.search(rf"{label}:?\s*(.*?)(?=\s*\||\s*Matrícula:|\s*CPF:|\s*Escala:|\s*Cargo:|\s*Período:|\s*$|\n)", text, re.I)
                    return m.group(1).strip() if m else "N/A"
                
                nome_raw = find("Colaborador")
                # Limpeza extra para o nome caso venha grudado com matrícula
                nome = nome_raw.split("Matrícula")[0].strip()
                
                if nome == "N/A" and last_h: 
                    h = last_h
                else:
                    h = {
                        "nome": nome, 
                        "mat": find("Matrícula"), 
                        "cpf": find("CPF"), 
                        "escala": find("Escala"), 
                        "per": find("Período")
                    }
                    last_h = h
                
                table = page.extract_table()
                if table:
                    for r in table:
                        # O padrão do PDF parece ser 4 colunas ou mais
                        # Col 0: Data, Col 1: Marcações, Col 2: Motivo
                        if len(r) >= 3:
                            # Passa Data, Marcação, Motivo, Escala
                            res = analisar_linha(r[0], r[1], r[2], h['escala'])
                            if res:
                                if h['nome'] not in todos_dados: todos_dados[h['nome']] = {"h": h, "j": []}
                                todos_dados[h['nome']]["j"].append(res)
        progresso.progress((idx + 1) / total_files)

    # --- EXIBIÇÃO ---
    st.sidebar.markdown("---")
    selecionado = st.sidebar.selectbox("Selecione o Colaborador", list(todos_dados.keys()))
    
    if selecionado:
        user = todos_dados[selecionado]
        
        # Cabeçalho do Colaborador
        with st.container():
            col_a, col_b = st.columns([1, 3])
            with col_a:
                st.image("https://cdn-icons-png.flaticon.com/512/3135/3135715.png", width=100)
            with col_b:
                st.title(f"{selecionado}")
                st.markdown(f"**Matrícula:** {user['h']['mat']} | **Escala:** {user['h']['escala']}")
                st.markdown(f"**Período:** {user['h']['per']}")

        st.divider()

        # Filtros de visualização
        ver_apenas_erros = st.checkbox("Ver apenas dias com ocorrências", value=False)

        # Grid de Dias
        cols = st.columns(3)
        count = 0
        
        for dia in user['j']:
            # Lógica de filtro
            tem_alerta = len(dia['alertas']) > 0
            if ver_apenas_erros and not tem_alerta:
                continue

            with cols[count % 3]:
                # Define cor da borda baseada no status
                border_color = "red" if tem_alerta else "#ddd"
                
                # HTML do Card
                html_batidas = ' | '.join(dia['batidas']) if dia['batidas'] else '<span style="color:#ccc">Sem registro</span>'
                
                html_status = ""
                if dia['alertas']:
                    html_status = f"<div class='blink'>{'<br>'.join(dia['alertas'])}</div>"
                elif dia['motivo']:
                    html_status = f"<div class='status-ok'>✅ {dia['motivo']}</div>"
                else:
                    html_status = "<div class='status-ok'>Regular</div>"

                # Se tiver motivo E alertas (caso raro, mas possível), mostra o motivo também
                if dia['alertas'] and dia['motivo']:
                     html_status += f"<br><small style='color:grey'>{dia['motivo']}</small>"

                st.markdown(f"""
                <div class="card" style="border-left: 5px solid {border_color};">
                    <div style="display:flex; justify-content:space-between;">
                        <b>📅 {dia['data']}</b>
                    </div>
                    <hr style="margin: 5px 0;">
                    <div style="font-family: monospace; font-size: 1.1em;">{html_batidas}</div>
                    <div style="margin-top: 8px; font-size: 0.9em;">
                        {html_status}
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
            count += 1
        
        if count == 0:
            st.info("Nenhuma ocorrência encontrada para este filtro.")

else:
    st.title("📊 Auditoria de Ponto Online")
    st.markdown("""
    ### Instruções:
    1. Exporte o cartão de ponto em **PDF**.
    2. Arraste o arquivo para o menu lateral esquerdo.
    3. O sistema identificará automaticamente:
       - Faltas justificadas (Atestados, Afastamentos, Férias).
       - Intervalos irregulares (considerando escala 30h se aplicável).
       - Marcações ímpares.
    """)
