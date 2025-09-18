# -*- coding: utf-8 -*-
import discord
from discord.ext import commands, tasks
import requests
import datetime
import logging
import json
import os
import re
from dotenv import load_dotenv
from bs4 import BeautifulSoup

load_dotenv("bot.env")

TOKEN = os.getenv("DISCORD_TOKEN")
CANAL_NOTIFICACAO_ID = int(os.getenv("CANAL_NOTIFICACAO_ID", 0))
PREFIXO = os.getenv("BOT_PREFIXO", "!")
INTERVALO_MONITORAMENTO_MINUTOS = int(os.getenv("INTERVALO_MONITORAMENTO_MINUTOS", 10))

API_URL = os.getenv("API_URL", "https://monitorsefaz.webmaniabr.com/v2/components.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s:%(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("discord")

STATUS_OPERACIONAL = "Operacional"
STATUS_INSTABILIDADE = "Instabilidade"
STATUS_FORA_DE_OPERACAO = "Fora de Operação"
STATUS_DESCONHECIDO = "Desconhecido"

EMOJI_ONLINE = "🟢"
EMOJI_INSTAVEL = "🟡"
EMOJI_OFFLINE = "🔴"
EMOJI_DESCONHECIDO = "❓"


def get_sefaz_status(autorizador_filtro=None):
    """
    Busca o status dos serviços NFe/NFCe/CTe da SEFAZ utilizando a API v2 da WebmaniaBR.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MonitorSEFAZBot/1.0)"} 
        response = requests.get(API_URL, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()

        components = data.get("components", [])
        
        status_monitorados = []
        for comp in components:
            name = comp.get("name", "").strip()
            status_id = comp.get("status_id") 
            
            autorizador = "Desconhecido"
            servico_tipo = "Serviço"
            
            match_autorizador_servico = re.match(r'(?:SEFAZ\s)?(AM|BA|CE|GO|MG|MS|MT|PE|PR|RS|SP|SVRS|AN|Nacional)\s*-\s*(NFe|NFCe|CTe|MDFe|BPe|EPEC)', name, re.IGNORECASE)
            
            if match_autorizador_servico:
                autorizador = match_autorizador_servico.group(1).upper().replace('SEFAZ ', '')
                servico_tipo = match_autorizador_servico.group(2).upper()
            else:
                if 'NFe Nacional' in name:
                    autorizador = "Nacional"
                    servico_tipo = "NFe"
                elif 'NFCe Nacional' in name:
                    autorizador = "Nacional"
                    servico_tipo = "NFCe"
                elif 'CTe Nacional' in name:
                    autorizador = "Nacional"
                    servico_tipo = "CTe"
                else:
                    autorizador = "Outros" 
                    servico_tipo = name 


            status_texto, status_emoji = STATUS_DESCONHECIDO, EMOJI_DESCONHECIDO
            if status_id == 1: 
                status_texto, status_emoji = STATUS_OPERACIONAL, EMOJI_ONLINE
            elif status_id == 2 or status_id == 3:
                status_texto, status_emoji = STATUS_INSTABILIDADE, EMOJI_INSTAVEL
            elif status_id == 4: 
                status_texto, status_emoji = STATUS_FORA_DE_OPERACAO, EMOJI_OFFLINE
            
            status_monitorados.append({
                "autorizador": autorizador,
                "servico_tipo": servico_tipo,
                "status": status_texto,
                "emoji": status_emoji,
                "detalhes": name 
            })
        
        if autorizador_filtro:
            uf_filtro = autorizador_filtro.upper()
            
            incidentes_filtrados = [
                s for s in status_monitorados 
                if (s["autorizador"].upper() == uf_filtro or (uf_filtro == 'AN' and s["autorizador"] == 'AN') or (uf_filtro == 'NACIONAL' and s["autorizador"] == 'NACIONAL'))
                and s["status"] != STATUS_OPERACIONAL
            ]
            
            if incidentes_filtrados:
                return incidentes_filtrados, None
            else:

                return [{
                    "autorizador": uf_filtro,
                    "servico_tipo": "NFe/NFCe", 
                    "status": STATUS_OPERACIONAL,
                    "emoji": EMOJI_ONLINE
                }], None
        
        return [s for s in status_monitorados if s["status"] != STATUS_OPERACIONAL], None

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de requisição ao acessar a API WebmaniaBR v2: {e}")
        return None, f"Erro de requisição ao acessar a API WebmaniaBR v2: {e}"
    except Exception as e:
        logger.error(f"Ocorreu um erro inesperado em get_sefaz_status (API v2): {e}", exc_info=True)
        return None, f"Ocorreu um erro inesperado ao processar os dados da API v2: {e}"

def get_sat_sp_status():
    """
    Busca o status do sistema SAT da SEFAZ-SP por web scraping.
    """
    URL = "https://sat.fazenda.sp.gov.br/COMSAT/"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MonitorSEFAZBot/1.0)"}
        response = requests.get(URL, headers=headers, timeout=20)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        status_img = soup.find('img', src='imagens/bola_verde.gif')
        
        if status_img:
            return {"servico_tipo": "CF-e (SAT)", "status": STATUS_OPERACIONAL, "emoji": EMOJI_ONLINE}
        else:
            return {"servico_tipo": "CF-e (SAT)", "status": STATUS_INSTABILIDADE, "emoji": EMOJI_INSTAVEL} # Assumimos instabilidade se não está verde
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao consultar SAT-SP: {e}")
        return {"servico_tipo": "CF-e (SAT)", "status": STATUS_DESCONHECIDO, "emoji": EMOJI_DESCONHECIDO}

def get_mfe_ce_status():
    """
    Busca o status do sistema MFE da SEFAZ-CE por web scraping.
    """
    URL = "https://www.sefaz.ce.gov.br/mfe-status-servicos/"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MonitorSEFAZBot/1.0)"}
        response = requests.get(URL, headers=headers, timeout=20)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        status_td = soup.select_one('td:-soup-contains("Status do MFE") + td')

        if status_td and "OPERANDO NORMALMENTE" in status_td.text.upper():
            return {"servico_tipo": "CF-e (MFE)", "status": STATUS_OPERACIONAL, "emoji": EMOJI_ONLINE}
        else:
            return {"servico_tipo": "CF-e (MFE)", "status": STATUS_INSTABILIDADE, "emoji": EMOJI_INSTAVEL} # Assumimos instabilidade se não está normal
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao consultar MFE-CE: {e}")
        return {"servico_tipo": "CF-e (MFE)", "status": STATUS_DESCONHECIDO, "emoji": EMOJI_DESCONHECIDO}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIXO, intents=intents)

ULTIMO_STATUS_FILE = "ultimo_status.json"

def carregar_ultimo_status():
    try:
        if os.path.exists(ULTIMO_STATUS_FILE):
            with open(ULTIMO_STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        logger.warning(f"Arquivo '{ULTIMO_STATUS_FILE}' não encontrado ou corrompido. Iniciando do zero.")
    except Exception as e:
        logger.error(f"Erro ao carregar '{ULTIMO_STATUS_FILE}': {e}")
    return {}

def salvar_ultimo_status(status):
    try:
        with open(ULTIMO_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=4)
    except Exception as e:
        logger.error(f"Erro ao salvar '{ULTIMO_STATUS_FILE}': {e}")

ultimo_status_conhecido = carregar_ultimo_status()

@bot.event
async def on_ready():
    global ultimo_status_conhecido
    ultimo_status_conhecido = carregar_ultimo_status()
    logger.info(f"Bot conectado como {bot.user} (ID: {bot.user.id})")
    logger.info("Iniciando monitoramento.")
    if not monitoramento_sefaz.is_running():
        monitoramento_sefaz.start()

@tasks.loop(minutes=INTERVALO_MONITORAMENTO_MINUTOS)
async def monitoramento_sefaz():
    global ultimo_status_conhecido
    logger.info("Executando verificação automática...")
    
    canal_notificacao = bot.get_channel(CANAL_NOTIFICACAO_ID)
    if not canal_notificacao:
        if CANAL_NOTIFICACAO_ID != 0:
            logger.error(f"ERRO CRÍTICO: Canal com ID {CANAL_NOTIFICACAO_ID} não encontrado.")
        return

    incidentes_atuais_lista, erro = get_sefaz_status()
    if erro:
        logger.warning(f"Falha na verificação automática: {erro}")
        return

    status_atual_dict = {f"{item['autorizador']}-{item['servico_tipo']}": item['status'] for item in incidentes_atuais_lista}
    mudancas = []
    
    for chave, status_novo in status_atual_dict.items():
        status_anterior = ultimo_status_conhecido.get(chave, STATUS_OPERACIONAL)
        if status_novo != status_anterior:
            if '-' in chave:
                autorizador, servico_tipo = chave.split('-', 1)
                mudancas.append({"autorizador": autorizador, "servico_tipo": servico_tipo, "status_anterior": status_anterior, "status_novo": status_novo, "resolvido": False})
            else:
                logger.warning(f"Ignorando chave de incidente mal formatada no monitoramento: '{chave}'")

    for chave, status_anterior in ultimo_status_conhecido.items():
        if chave not in status_atual_dict and status_anterior != STATUS_OPERACIONAL:
            if '-' in chave:
                autorizador, servico_tipo = chave.split('-', 1)
                mudancas.append({"autorizador": autorizador, "servico_tipo": servico_tipo, "status_anterior": status_anterior, "status_novo": STATUS_OPERACIONAL, "resolvido": True})
            else:
                logger.warning(f"Ignorando chave mal formatada do arquivo de status no monitoramento: '{chave}'")

    if mudancas:
        logger.info(f"Detectadas {len(mudancas)} mudanças de status. Enviando notificação.")
        for mudanca in mudancas:
            if mudanca["resolvido"]:
                cor = discord.Color.green()
                titulo = f"✅ Serviço Normalizado: {mudanca['autorizador']} ({mudanca['servico_tipo']})"
                valor = f"O serviço voltou a operar normalmente.\n(Status anterior: **{mudanca['status_anterior']}**)"
            else:
                cor = discord.Color.red()
                titulo = f"🚨 Alerta de Instabilidade: {mudanca['autorizador']} ({mudanca['servico_tipo']})"
                valor = f"Status alterado de **{mudanca['status_anterior']}** para **{mudanca['status_novo']}**"
            
            embed = discord.Embed(title=titulo, color=cor, description=valor)
            embed.set_footer(text=f"Fonte: WebmaniaBR v2 API | {datetime.datetime.now(datetime.timezone.utc).strftime('%d/%m/%Y %H:%M')}")
            await canal_notificacao.send(embed=embed)

    ultimo_status_conhecido = status_atual_dict
    salvar_ultimo_status(ultimo_status_conhecido)
    logger.info("Verificação concluída e status salvo.")

@bot.command(name="sefaz", help='Verifica o status da SEFAZ. Use "!sefaz SP" para um estado específico.')
async def checar_sefaz(ctx, estado: str = None):
    mensagem_espera = await ctx.send("🔍 Verificando status dos serviços...")
    
    if estado and estado.upper() in ['SP', 'CE']:
        uf = estado.upper()
        embed = discord.Embed(title=f"Painel de Status Completo - SEFAZ {uf}", color=discord.Color.blue())
        
        nfe_status_lista, nfe_erro = get_sefaz_status(autorizador_filtro=uf)
        if nfe_erro:
            embed.add_field(name="Serviços NFe/NFCe/CTe", value=f"❌ Erro ao consultar: {nfe_erro}", inline=False)
        else:
            for servico in nfe_status_lista:
                embed.add_field(name=f"Serviço: {servico['servico_tipo']}", value=f"**Status:** {servico['emoji']} {servico['status']}", inline=False)

        cfe_status = get_sat_sp_status() if uf == 'SP' else get_mfe_ce_status()
        embed.add_field(name=f"Serviço: {cfe_status['servico_tipo']}", value=f"**Status:** {cfe_status['emoji']} {cfe_status['status']}", inline=False)
        
        await mensagem_espera.delete()
        embed.set_footer(text=f"Fontes: WebmaniaBR v2 API e SEFAZ-{uf}")
        await ctx.send(embed=embed)
        return

    status_lista, erro = get_sefaz_status(autorizador_filtro=estado)
    await mensagem_espera.delete()

    if erro:
        await ctx.send(f"❌ **Erro:**\n{erro}")
        return

    if not status_lista:
        if not estado:
            embed = discord.Embed(title="✅ Status Geral dos Serviços da SEFAZ", color=discord.Color.green(), description="Todos os serviços monitorados (NFe/NFCe/CTe) estão operando normalmente.")
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"Nenhum incidente (NFe/NFCe/CTe) encontrado para o autorizador '{estado}'.")
        return

    embed = discord.Embed(
        title="⚠️ Status Geral: Foram encontradas instabilidades!" if not estado else f"Status para {estado.upper()}",
        color=discord.Color.red() if not estado else discord.Color.blue(), # Cor diferente para estados específicos
        description="Apenas os serviços com problemas (NFe/NFCe/CTe) estão listados abaixo." if not estado else None
    )
    for servico in status_lista:
        embed.add_field(
            name=f"{servico['emoji']} {servico['autorizador']} ({servico['servico_tipo']})",
            value=f"**Status:** {servico['status']}",
            inline=True
        )
    
    embed.set_footer(text="Fonte: WebmaniaBR v2 API")
    await ctx.send(embed=embed)

if TOKEN is None or CANAL_NOTIFICACAO_ID == 0:
    logger.critical("ERRO CRÍTICO: Variáveis de ambiente DISCORD_TOKEN ou CANAL_NOTIFICACAO_ID não configuradas.")
    print("ERRO: Por favor, configure as variáveis DISCORD_TOKEN e CANAL_NOTIFICACAO_ID no arquivo .env ou nas secrets.")
else:
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.critical("ERRO: O TOKEN do Discord fornecido é inválido.")
        print("ERRO: O TOKEN do Discord fornecido é inválido. Verifique o arquivo .env ou as secrets.")
    except Exception as e:
        logger.critical(f"Erro inesperado ao iniciar o bot: {e}", exc_info=True)
        print(f"ERRO: Erro inesperado ao iniciar o bot: {e}")