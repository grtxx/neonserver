import json
import os
import logging
from typing import Any
import aiomysql # type: ignore
from mcp.client.sse import sse_client  # type: ignore
from mcp.client.streamable_http import streamable_http_client # type: ignore
from mcp import ClientSession # type: ignore
from langchain_core.tools import Tool # type: ignore
from jsonmcp_client import jsonRPCClient


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
        credentials = servers[name]["credentials"] if "credentials" in servers[name] else None
        proto = servers[name]["proto"] if "proto" in servers[name] else "sse"
        overrides = servers[name]["overrides"] if "overrides" in servers[name] else {}
        log.info(f"Discovering tools: {name} ({url})")
        
        if proto == "streamablehttp":
            try:
                # Rövid timeout-ot érdemes rátenni, hogy ne akadjon el a startup, ha egy szerver lehalt
                async with streamable_http_client(url) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        mcp_tools = await session.list_tools()
                        
                        for t in mcp_tools.tools:
                            if ( t.name in overrides.get('tools-disabled', []) ):
                                log.info( f"  Skipped tool {t.name}, {t.description}" )
                                continue
                            log.info( f"  Discovered tool {t.name}" )
                            desc = overrides.get( "prefix", "" ) + (t.description if t.description else "") + overrides.get( "postfix", "" )
                            if ( overrides.get( t.name, None ) is not None ):
                                desc = overrides[t.name]
                            all_langchain_tools.append(
                                Tool(
                                    name=t.name,
                                    func=None, 
                                    description= desc
                                )
                            )
                            tool_to_server_map[t.name] = { 
                                "url": url, 
                                "inputSchema": t.inputSchema,
                                "credentials": credentials,
                                "proto": proto
                            }
                            
                        log.info(f"  Loaded {len(mcp_tools.tools)} tools from {name}")
            except Exception as e:
                log.error(f"Error accessing MCP server {name}: {str(e)}")


        if proto == "sse":
            try:
                # Rövid timeout-ot érdemes rátenni, hogy ne akadjon el a startup, ha egy szerver lehalt
                async with sse_client(url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        mcp_tools = await session.list_tools()
                        
                        for t in mcp_tools.tools:
                            if ( t.name in overrides.get('tools-disabled', []) ):
                                log.info( f"  Skipped tool {t.name}, {t.description}" )
                                continue
                            log.info( f"  Discovered tool {t.name}" )
                            desc = overrides.get( "prefix", "" ) + (t.description if t.description else "") + overrides.get( "postfix", "" )
                            if ( overrides.get( t.name, None ) is not None ):
                                desc = overrides[t.name]
                            all_langchain_tools.append(
                                Tool(
                                    name=t.name,
                                    func=None, 
                                    description= desc
                                )
                            )
                            tool_to_server_map[t.name] = { 
                                "url": url, 
                                "inputSchema": t.inputSchema,
                                "credentials": credentials,
                                "proto": proto
                            }
                            
                        log.info(f"  Loaded {len(mcp_tools.tools)} tools from {name}")
            except Exception as e:
                log.error(f"Error accessing MCP server {name}: {str(e)}")

        if proto == "jsonrpc":
            try:
                jsonmcp = jsonRPCClient( url, credentials=credentials )
                mcp_tools = await jsonmcp.listTools()
                if mcp_tools is not None:
                    for t in mcp_tools['tools']:
                        if ( t['name'] in overrides.get('tools-disabled', []) ):
                            log.info( f"  Skipped tool {t['name']}, {t['description']}" )
                            continue
                        log.info( f"  Discovered tool {t['name']}, {t['description']}" )
                        desc = overrides.get("prefix", "") + t.get("description", "") + overrides.get("postfix", "") if "description" in t else ""
                        if ( overrides.get( t['name'], None ) is not None ):
                            desc = overrides[t['name']]
                        all_langchain_tools.append(
                            Tool(
                                name=t["name"],
                                func=None, 
                                description=desc
                            )
                        )
                        tool_to_server_map[t["name"]] = { 
                            "url": url, 
                            "inputSchema": t["inputSchema"] if "inputSchema" in t else None,
                            "credentials": credentials,
                            "proto": proto
                        }
                else:
                    log.error(f"Cannot list tools from JSON-RPC MCP server {name}")
            except Exception as e:
                log.error(f"Error accessing JSON-RPC MCP server {name}: {str(e)}")
            
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




