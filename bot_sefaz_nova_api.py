import discord
from discord.ext import commands, tasks
import requests
import datetime
import logging
import json
import os
import re
from dotenv import load_dotenv

load_dotenv("bot.env")

TOKEN = os.getenv("DISCORD_TOKEN")
CANAL_NOTIFICACAO_ID = int(os.getenv("CANAL_NOTIFICACAO_ID"))
PREFIXO = os.getenv("BOT_PREFIXO", "!")
INTERVALO_MONITORAMENTO_MINUTOS = int(os.getenv("INTERVALO_MONITORAMENTO_MINUTOS", 10))

API_URL = os.getenv("API_URL", "https://monitorsefaz.webmaniabr.com/summary.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s:%(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
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

ALIAS_MAP = {"SC": "SVRS", "AM": "AM"} 
def get_sefaz_status(autorizador_filtro=None):
    """
    Busca o status dos serviços da SEFAZ, adaptado para a nova API baseada em incidentes.
    Serviços não listados em 'activeIncidents' são considerados operacionais.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(API_URL, headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()

        incidents = data.get("activeIncidents", [])
        authorizers_com_problema = {}

        for incident in incidents:
            name = incident.get("name", "")
            match = re.search(r'Sefaz\s([A-Z]{2})', name)
            if match:
                autorizador = match.group(1)
                impact = incident.get("impact", "").upper()

                if impact == "MAJOROUTAGE":
                    status_texto, status_emoji = STATUS_FORA_DE_OPERACAO, EMOJI_OFFLINE
                else: 
                    status_texto, status_emoji = STATUS_INSTABILIDADE, EMOJI_INSTAVEL

                authorizers_com_problema[autorizador] = {
                    "autorizador": autorizador,
                    "status": status_texto,
                    "emoji": status_emoji,
                    "detalhes": name
                }

        if autorizador_filtro:
            uf_filtro = autorizador_filtro.upper()
            if uf_filtro in authorizers_com_problema:
                return [authorizers_com_problema[uf_filtro]], None
            else:
                return [{
                    "autorizador": uf_filtro,
                    "status": STATUS_OPERACIONAL,
                    "emoji": EMOJI_ONLINE
                }], None

        return list(authorizers_com_problema.values()), None

    except requests.exceptions.HTTPError as e:
        logger.error(f"Erro HTTP ao acessar a API: {e.response.status_code} - {e.response.text}")
        return None, f"Erro HTTP ao acessar a API: {e.response.status_code}"
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de requisição ao acessar a API: {e}")
        return None, f"Erro de requisição ao acessar a API: {e}"
    except json.JSONDecodeError:
        logger.error("Erro ao decodificar JSON da resposta da API.")
        return None, "Erro ao processar a resposta da API: formato inválido."
    except Exception as e:
        logger.error(f"Ocorreu um erro inesperado ao processar os dados: {e}", exc_info=True)
        return None, f"Ocorreu um erro inesperado: {e}"
    
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIXO, intents=intents)

ULTIMO_STATUS_FILE = "ultimo_status.json"


def carregar_ultimo_status():
    """
    Carrega o último estado conhecido do arquivo JSON.
    Retorna um dicionário vazio se o arquivo não existir ou ocorrer um erro.
    """
    try:
        if os.path.exists(ULTIMO_STATUS_FILE):
            with open(ULTIMO_STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except json.JSONDecodeError:
        logger.warning(f"Arquivo '{ULTIMO_STATUS_FILE}' corrompido ou vazio. Iniciando com status vazio.")
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
    logger.info("Iniciando monitoramento com a API do nfe.io.")
    if not monitoramento_sefaz.is_running():
        monitoramento_sefaz.start()

@tasks.loop(minutes=INTERVALO_MONITORAMENTO_MINUTOS)
async def monitoramento_sefaz():
    global ultimo_status_conhecido
    logger.info("Executando verificação automática (nfe.io)...")

    canal_notificacao = bot.get_channel(CANAL_NOTIFICACAO_ID)
    if not canal_notificacao:
        if CANAL_NOTIFICACAO_ID != 0:
            logger.error(f"ERRO CRÍTICO: Canal com ID {CANAL_NOTIFICACAO_ID} não encontrado.")
        return

    incidentes_atuais_lista, erro = get_sefaz_status()
    if erro:
        logger.warning(f"Falha na verificação automática: {erro}")
        return

    status_atual_dict = {item["autorizador"]: item["status"] for item in incidentes_atuais_lista}
    mudancas = []
    
    for autorizador, status_novo in status_atual_dict.items():
        status_anterior = ultimo_status_conhecido.get(autorizador, STATUS_OPERACIONAL)
        if status_novo != status_anterior:
            mudancas.append({
                "autorizador": autorizador,
                "status_anterior": status_anterior,
                "status_novo": status_novo,
                "resolvido": False
            })

    for autorizador, status_anterior in ultimo_status_conhecido.items():
        if autorizador not in status_atual_dict and status_anterior != STATUS_OPERACIONAL:
            mudancas.append({
                "autorizador": autorizador,
                "status_anterior": status_anterior,
                "status_novo": STATUS_OPERACIONAL,
                "resolvido": True
            })

    if mudancas:
        logger.info(f"Detectadas {len(mudancas)} mudanças de status. Enviando notificação.")
        embed = discord.Embed(
            title="📢 Alerta de Status da SEFAZ!",
            color=discord.Color.orange(),
            description=f"Fonte: WebmaniaBR | {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        for mudanca in mudancas:
            if mudanca["resolvido"]:
                embed.color = discord.Color.green()
                embed.title = "✅ Serviço da SEFAZ Normalizado!"
                value_text = f"O serviço voltou a operar normalmente. (Status anterior: **{mudanca['status_anterior']}**)"
            else:
                embed.color = discord.Color.red()
                embed.title = "🚨 Alerta de Instabilidade na SEFAZ!"
                value_text = f"Status alterado de **{mudanca['status_anterior']}** para **{mudanca['status_novo']}**"
            
            embed.add_field(
                name=f"🏢 Autorizador: {mudanca['autorizador']}",
                value=value_text,
                inline=False
            )
        await canal_notificacao.send(embed=embed)

    ultimo_status_conhecido = status_atual_dict
    salvar_ultimo_status(ultimo_status_conhecido)
    logger.info("Verificação concluída e status salvo.")
    
@bot.command(name="sefaz", help='Verifica o status da SEFAZ. Use "!sefaz SP" para um estado específico.')
async def checar_sefaz(ctx, estado: str = None):
    mensagem_espera = await ctx.send("🔍 Verificando status dos serviços...")
    status_lista, erro = get_sefaz_status(autorizador_filtro=estado)
    await mensagem_espera.delete()

    if erro:
        await ctx.send(f"❌ **Erro:**\n{erro}")
        return

    if estado:
        if not status_lista:
            await ctx.send(f"❌ **Erro:** Autorizador '{estado}' não encontrado ou sem dados disponíveis.")
            return
        servico = status_lista[0]
        cor = discord.Color.green() if servico["status"] == "Online" else (discord.Color.gold() if servico["status"] == "Instável" else discord.Color.red())
        embed = discord.Embed(title=f"Status SEFAZ para {servico['autorizador']}",
                              description=f"**Status:** {servico['emoji']} {servico['status']}",
                              color=cor)
    else:
        instabilidades = [s for s in status_lista if s["status"] != "Online"]
        embed = discord.Embed(title="Status Geral dos Serviços da SEFAZ",
                              color=discord.Color.red() if instabilidades else discord.Color.green())
        if not instabilidades:
            embed.add_field(name="✅ Tudo Certo!", value="Todos os serviços da SEFAZ estão operando normalmente.", inline=False)
        else:
            embed.description = "⚠️ **Foram encontradas instabilidades ou serviços offline!**"
            for servico in instabilidades:
                embed.add_field(name=f"{servico['emoji']} {servico['autorizador']}",
                                value=f"**Status:** {servico['status']}",
                                inline=True)
    
    embed.set_footer(text="Fonte: [monitorsefaz.webmaniabr.com](https://monitorsefaz.webmaniabr.com/)")
    await ctx.send(embed=embed)

@bot.command(name="autorizadores", help="Lista todos os autorizadores da SEFAZ monitorados.")
async def listar_autorizadores(ctx):
    mensagem_espera = await ctx.send("🔍 Buscando lista de autorizadores...")
    status_lista, erro = get_sefaz_status()
    await mensagem_espera.delete()

    if erro:
        await ctx.send(f"❌ **Erro ao obter autorizadores:**\n{erro}")
        return

    if not status_lista:
        await ctx.send("Nenhum autorizador encontrado ou dados indisponíveis.")
        return

    autorizadores_unicos = sorted(list(set([s["autorizador"] for s in status_lista if s["autorizador"]])))
    
    blocos_autorizadores = []
    current_block = ""
    for autorizador in autorizadores_unicos:
        if len(current_block) + len(autorizador) + 2 > 1000:
            blocos_autorizadores.append(current_block.strip())
            current_block = autorizador + ", "
        else:
            current_block += autorizador + ", "
    if current_block:
        blocos_autorizadores.append(current_block.strip())

    embed = discord.Embed(
        title="🏢 Autorizadores da SEFAZ Monitorados",
        description="Aqui estão os estados/autorizadores que a SEFAZ atua e são monitorados pela API:",
        color=discord.Color.blue()
    )

    if not blocos_autorizadores:
        embed.add_field(name="Nenhum Autorizador Encontrado", value="Não foi possível obter a lista de autorizadores no momento.", inline=False)
    elif len(blocos_autorizadores) == 1:
        embed.add_field(name="Estados/Autorizadores", value=blocos_autorizadores[0].rstrip(","), inline=False)
    else:
        for i, bloco in enumerate(blocos_autorizadores):
            embed.add_field(name=f"Estados/Autorizadores (Parte {i+1})", value=bloco.rstrip(","), inline=False)

    embed.set_footer(text="Fonte: [monitorsefaz.webmaniabr.com](https://monitorsefaz.webmaniabr.com/)")
    await ctx.send(embed=embed)

if TOKEN is None or CANAL_NOTIFICACAO_ID == 0:
    logger.critical("ERRO CRÍTICO: Variáveis de ambiente DISCORD_TOKEN ou CANAL_NOTIFICACAO_ID não configuradas. Crie um arquivo .env.")
    print("ERRO: Por favor, configure as variáveis DISCORD_TOKEN e CANAL_NOTIFICACAO_ID no arquivo .env.")
else:
    try:
        bot.run(TOKEN)
    except discord.LoginFailure:
        logger.critical("ERRO: O TOKEN do Discord fornecido é inválido. Verifique o arquivo .env.")
        print("ERRO: O TOKEN do Discord fornecido é inválido. Verifique o arquivo .env.")
    except Exception as e:
        logger.critical(f"Erro inesperado ao iniciar o bot: {e}", exc_info=True)
        print(f"ERRO: Erro inesperado ao iniciar o bot: {e}")