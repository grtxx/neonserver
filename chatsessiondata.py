from langchain_core.messages import BaseMessage # type: ignore
from langchain_core.tools import Tool # type: ignore
from typing import Any, Dict, List
import uuid
import time
import aiomysql # type: ignore
from configmanager import conf, log
from langchain_core.messages import message_to_dict, messages_from_dict # type: ignore
import json
from fastapi import FastAPI, WebSocket # type: ignore
import re
from mcp.client.sse import sse_client # type: ignore
from mcp.client.streamable_http import streamable_http_client # type: ignore
from jsonmcp_client import jsonRPCClient
import os
import httpx

class ChatSessionData: 

    def __init__( self, app: FastAPI ):
        self.app = app
        self.name: str = ""
        self.messages: List[ BaseMessage ] = []
        self.settings = {
            "mode": "",
            "model": "",
            "modelConfig": {},
            "credentials": {},
            "userdata": {},
            "systemprompt": "",
            "status": "running"
        }
        self.settings["credentials"] = {}
        self.settings["userdata"] = {}
        self.sid = ""
        self.settings["mode"] = "default"
        self.tools: List[ Tool ] = self.app.state.mcptools


    def readPersona( self, name ) -> str:
        fn = conf.get( f"personas.{name}", "" )
        try:
            if fn != "":
                with open( os.path.join( os.path.dirname(__file__), f"personas/{fn}" ), "r" ) as f:
                    persona = ""
                    while True:
                        line = f.read()
                        if line == '':
                            break
                        persona += line
                return persona
        except Exception as e:
            print( f"Error in reading persona file {fn}: {e}" )
        return "Your name is BlackLight, you are almost dumb, don't understand any question, just telling things randomly but sometimes you can reflect to the user prompts and can give cryptic answers. You speak in {language}";


    async def updateStatus( self, status: str ):
        oldStatus = self.settings["status"]
        self.settings["status"] = status
        await self.save()


    async def createApproval( self, toolName: str, toolArgs: dict, toolCallId: str, toolResult: dict ):
        pass


    async def createChat( self, mode: str, userdata=None ):
        self.settings["userdata"] = userdata
        self.sid = uuid.uuid4().hex
        self.settings["mode"] = mode
        if ( mode == "default" ):
            self.settings["model"] = "gemini-3-flash-preview"
            self.settings["systemprompt"] = self.readPersona( "neon" )
            self.settings["modelConfig"] = conf.get()["models"][ self.settings["model"] ]
        await self.save()
        return self.sid


    async def extractCommands( self, content, laststate=0, websocket: WebSocket | None=None, exec=False ) -> ( int, str ): # type: ignore
        commandMode = laststate
        currentCommand: str = ""
        commands = []
        clearContent = ""
        
        if isinstance(content, list):
            if ( len(content) > 0 ):  
                content = content[0]
            else:
                return (commandMode, "")
        if "text" in content and not isinstance(content, str): # type: ignore
            content = content["text"]  # type: ignore
        for c in range( len(content) ): # type: ignore
            currentChar = content[c] # type: ignore
            nextChar = content[c+1] if (c+1) < len(content) else "" #type: ignore
            prevChar = content[c-1] if c-1 >= 0 else "" # type: ignore
            bprevChar = content[c-2] if c-2 >= 0 else "" # type: ignore
            if ( commandMode == 1 and bprevChar == "$" and prevChar == "$" and len(currentCommand) > 2 ):
                commandMode = 0
                commands.append( currentCommand )
            elif ( commandMode == 0 and currentChar == "$" and nextChar == "$" ):
                commandMode = 1
                currentCommand = ""
            
            if ( commandMode == 0 ):
                clearContent = clearContent + currentChar
            if ( commandMode > 0 ):
                currentCommand = currentCommand + currentChar        
        if ( currentCommand != "" and commandMode == 1 ):
            commands.append( currentCommand )
        if exec:
            await self.executeMemoryCommands( commands, websocket=websocket ) # type: ignore
        return ( commandMode, clearContent )


    async def executeMemoryCommands( self, commands: Dict[ str, str ], websocket: WebSocket | None=None ):
        for k in commands:
            g = re.match( r"^\$\$\s*(USER|SESSION)\s*\|\s*([a-zA-Z0-9_]+)\s*\|\s*(.*)\s*\$\$$", k )
            if ( g ):
                type = g.group(1)
                id = g.group(2)
                content = g.group(3)
                if ( type == "USER" ):
                    await self.saveUserMemory( id, content ) # type: ignore
                elif ( type == "SESSION" ):
                    await self.saveSessionMemory( id, content ) # type: ignore
                elif ( type == "OPENURL" ):
                    if websocket is not None:
                        await websocket.send_json( { "type": "openurl", "url": content } )


    async def getMemoryContents( self ):
        pool = self.app.state.dbpool
        res = ""
        async with pool.acquire() as conn:
            await conn.commit()
            async with conn.cursor( aiomysql.DictCursor ) as cursor:
                await cursor.execute(
                    "SELECT mem_id, mem_contents, updated FROM session_memory WHERE sid = %s UNION (SELECT mem_id, mem_contents, updated FROM user_memory WHERE userguid=%s) ORDER BY updated DESC LIMIT 50",
                    ( self.sid, self.settings["userdata"]["guid"] )
                )
                while True:
                    dt = await cursor.fetchone()
                    if dt is None:
                        break
                    res = res + "| %s | %s |\n" % ( dt["mem_id"], dt["mem_contents"] )
        return res
    

    async def getCustomisedSystemPrompt( self ):
        prompt = self.settings["systemprompt"]
        userdata = self.settings["userdata"]
        prompt = prompt.replace( "{currentdate}", time.strftime("%Y-%m-%d %H:%M:%S") )
        prompt = prompt.replace( "{memory_contents}", await self.getMemoryContents() )
        if userdata is not None:
            for k in userdata:
                try: 
                    prompt = prompt.replace( "{%s}" % (k, ), userdata[ k ] )
                except:
                    pass
        return prompt


    async def loadChat( self, sid: str ):
        self.sid = sid
        await self.load() # type: ignore


    async def saveMessage( self, message: BaseMessage ):
        dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime() )
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO messages (sid, date, message) VALUES (%s, %s, %s)",
                    ( self.sid, dt, json.dumps( message_to_dict( message ) ) )
                )
                await conn.commit()
        

    async def getHistory( self ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            await conn.commit()
            async with conn.cursor( aiomysql.DictCursor ) as cursor:
                await cursor.execute(
                    "SELECT * FROM messages WHERE sid = %s ORDER BY gid",
                    ( self.sid )
                )
                while True:
                    dt = await cursor.fetchone()
                    if dt is None:
                        break
                    yield messages_from_dict( [ json.loads( dt["message"] ) ] )[0]


    async def saveUserMemory( self, id: str, value: str ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                if ( value == "" ):
                    await cursor.execute(
                        "DELETE FROM user_memory WHERE userguid = %s AND mem_id = %s",
                        ( self.settings["userdata"]["guid"], id )
                    )
                else:
                    await cursor.execute(
                        "INSERT INTO user_memory (userguid, mem_id, mem_contents, updated) VALUES (%s, %s, %s, now()) ON DUPLICATE KEY UPDATE mem_contents = %s, updated=now()",
                        ( self.settings["userdata"]["guid"], id, value, value )
                    )
                await conn.commit()


    async def saveSessionMemory( self, id: str, value: str ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            async with conn.cursor() as cursor:
                if ( value == "" ):
                    await cursor.execute(
                        "DELETE FROM session_memory WHERE sid = %s AND mem_id = %s",
                        ( self.sid, id )
                    )
                else:
                    await cursor.execute(
                        "INSERT INTO session_memory (sid, mem_id, mem_contents, updated) VALUES (%s, %s, %s, now()) ON DUPLICATE KEY UPDATE mem_contents = %s, updated=now()",
                        ( self.sid, id, value, value )
                    )
                await conn.commit()


    async def getLastMessages( self, limit: int = 25 ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            async with conn.cursor( aiomysql.DictCursor ) as cursor:
                await cursor.execute(
                    "SELECT * FROM messages WHERE sid = %s ORDER BY gid DESC LIMIT %s",
                    ( self.sid, limit )
                )
                dts = await cursor.fetchall()
                self.messages = []
                for dt in reversed( dts ):
                    msg = messages_from_dict( [ json.loads( dt["message"] ) ] )[0]
                    ( ls, msg.content ) = await self.extractCommands( msg.content, 0, websocket=None, exec=False )
                    if ( msg.content != "" ):
                        self.messages.append( msg )

                return self.messages


    async def load( self, sid ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            await conn.commit()
            async with conn.cursor( aiomysql.DictCursor ) as cursor:
                await cursor.execute( "SELECT * FROM chats WHERE sid = %s", (sid,) )
                dt = await cursor.fetchone()
                if dt is None:
                    raise ValueError( "Unknown session ID" )
                    return
                self.name = dt["name"]
                self.settings = json.loads( dt["settings"] )
                self.sid = sid
                await self.getLastMessages()


    async def save( self ):
        pool = self.app.state.dbpool
        async with pool.acquire() as conn:
            await conn.commit()
            async with conn.cursor() as cursor:
                await cursor.execute( "SELECT sid FROM chats WHERE sid = %s", (self.sid,) )
                result = await cursor.fetchone()
                if result is not None:
                    await cursor.execute(
                        "UPDATE chats SET name = %s, settings = %s WHERE sid = %s",
                        ( self.name, json.dumps( self.settings ), self.sid )
                    )
                else:
                    await cursor.execute(
                        "INSERT INTO chats (sid, name, settings) VALUES (%s, %s, %s)",
                        ( self.sid, self.name, json.dumps( self.settings ) )
                    )
                await conn.commit()


    def getConfiguredJsonRPCClient( self, toolparams ):       
        try:
            client = jsonRPCClient( toolparams['url'], toolparams['credentials'] )
            client.setCredentialsStore( self.settings["credentials"] )
            return client
        except Exception as e:
            print( f"Error in instancing JSON RPC MCP client: {e}" )
            pass
   

    def getHeadersForTool( self, toolparams ):
        custom_headers = {}
        try:
            if "credentials" in toolparams and toolparams["credentials"] is not None:
                if "type" in toolparams["credentials"]:
                    if toolparams["credentials"]['type'] == 'bearer':
                        custom_headers = {
                            "Authorization": f"Bearer {toolparams['credentials']['bearertoken']}",
                        }
                    elif toolparams["credentials"]['type'] == 'bearer-user':
                        ok = True
                        if not 'token_name' in toolparams['credentials']:
                            ok = False
                        if not toolparams['credentials']['token_name'] in self.settings['credentials']:
                            ok = False
                        if ok:                
                            custom_headers = {
                                "Authorization": f"Bearer {self.settings['credentials'][ toolparams['credentials']['token_name'] ]}",
                            }
        except Exception as e:
            pass
        return custom_headers
    

    def getConfiguredSSEClient( self, toolparams ):
        return sse_client( toolparams['url'], headers=self.getHeadersForTool( toolparams ) )


    def getConfiguredStreamableHttpClient( self, toolparams ):
        client = httpx.AsyncClient(headers=self.getHeadersForTool( toolparams ), verify=False, timeout=30.0)
        return streamable_http_client( url=toolparams['url'], http_client=client )


