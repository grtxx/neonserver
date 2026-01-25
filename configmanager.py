import json
import os
import logging
from typing import Any
import aiomysql # type: ignore
from mcp.client.sse import sse_client  # type: ignore
from mcp import ClientSession # type: ignore
from langchain_core.tools import Tool # type: ignore


class configManager:

    def __init__( self ):
        try:
            with open( os.path.dirname(__file__) + '/config.json', 'r') as f:
                self.conf = json.loads(f.read())
        except Exception as e:
            print( "Config load error:", str(e) )
            self.conf = {}  


    def get( self, qstr: str | None = None, default: Any = None ):
        if ( qstr is None ):
            return self.conf
        qstrA: list[str] = f"{qstr}".split( "." ) 
        cur = self.conf
        for q in qstrA:
            if ( q in cur ):
                cur = cur[q]
            else:
                return default
        return cur


async def init_db():
    db_pool = await aiomysql.create_pool(
        host=conf.get("mysql.host"),
        user=conf.get("mysql.user"),
        password=conf.get("mysql.password"),
        db=conf.get("mysql.database"),
        minsize=5, maxsize=20 # Egyszerre ennyi kapcsolat marad nyitva
    )
    return db_pool


async def get_tools():
    global conf
    all_langchain_tools = []
    tool_to_server_map = {}
    servers = conf.get( "mcpservers", {} )
    for name in conf.get( "mcpservers" ):
        #url = f"http://{servers[name]['host']}:{servers[name]['port']}/sse"
        url = servers[name]["url"]
        log.info(f"Discovering tools: {name} ({url})")
        
        try:
            # Rövid timeout-ot érdemes rátenni, hogy ne akadjon el a startup, ha egy szerver lehalt
            async with sse_client(url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    mcp_tools = await session.list_tools()
                    
                    for t in mcp_tools.tools:
                        all_langchain_tools.append(
                            Tool(
                                name=t.name,
                                func=None, 
                                description=t.description if t.description else ""
                            )
                        )
                        tool_to_server_map[t.name] = { "url": url, "inputSchema": t.inputSchema }
                    log.info(f"Loaded {len(mcp_tools.tools)} tools from {name}")
        except Exception as e:
            log.error(f"Error accessing MCP server {name}: {str(e)}")
            
    return all_langchain_tools, tool_to_server_map


def get_params_for_tool( map, tool_name: str):
    return map.get(tool_name)


conf = configManager()

logging.basicConfig( 
    level=getattr( logging, conf.get("logging.level", "INFO"), logging.INFO ), # type: ignore
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler( conf.get( "logging.filename" ) ), # type: ignore
        logging.StreamHandler()         
    ] )
log = logging.getLogger( __name__)




